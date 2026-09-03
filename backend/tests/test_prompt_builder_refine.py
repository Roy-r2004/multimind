"""Prompt Builder refine — isolated ephemeral council (no Chat A contamination)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.exceptions import AppError, NotFoundError, ValidationError
from app.db.models import Chat, CostRecord, ModelAnswer, Turn, Verdict
from app.llm.providers import LLMResponse
from app.schemas.api import PromptBuilderRefineMessage
from app.services.prompt_builder_service import (
    COUNCIL_MAX_TOKENS,
    PROMPT_BUILDER_LLM_TIMEOUT_SECONDS,
    PROMPT_BUILDER_MAX_OUTPUT_TOKENS,
    REFEREE_MAX_TOKENS,
    prompt_builder_service,
)
from tests.conftest import create_model_set, create_other_auth


class ScriptedProvider:
    def __init__(
        self,
        responses: list[str] | None = None,
        *,
        error: Exception | None = None,
        finish_reasons: list[str | None] | None = None,
    ):
        self.responses = list(responses or [])
        self.finish_reasons = list(finish_reasons or [])
        self.error = error
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if not self.responses:
            return LLMResponse(text="", tokens_input=1, tokens_output=1)
        text = self.responses.pop(0)
        finish_reason = self.finish_reasons.pop(0) if self.finish_reasons else "stop"
        return LLMResponse(text=text, tokens_input=1, tokens_output=1, finish_reason=finish_reason)


def _patch_providers(
    monkeypatch: pytest.MonkeyPatch,
    provider: ScriptedProvider,
    *,
    context_length: int = 200_000,
    max_completion_tokens: int | None = None,
) -> None:
    registry = SimpleNamespace(
        get_provider=lambda _name: provider,
        validate_configured=lambda: None,
    )
    monkeypatch.setattr(
        "app.services.prompt_builder_service.get_provider_registry",
        lambda: registry,
    )
    metadata = {"context_length": context_length}
    if max_completion_tokens is not None:
        metadata["top_provider"] = {"max_completion_tokens": max_completion_tokens}
    pricing = SimpleNamespace(
        ensure_loaded=AsyncMock(return_value=None),
        get_slug_metadata=lambda _slug: metadata,
    )
    monkeypatch.setattr(
        "app.services.prompt_builder_service.get_pricing_service",
        lambda: pricing,
    )


async def _counts(db: AsyncSession) -> dict[str, int]:
    chats = (await db.execute(select(func.count()).select_from(Chat))).scalar_one()
    turns = (await db.execute(select(func.count()).select_from(Turn))).scalar_one()
    answers = (await db.execute(select(func.count()).select_from(ModelAnswer))).scalar_one()
    verdicts = (await db.execute(select(func.count()).select_from(Verdict))).scalar_one()
    costs = (await db.execute(select(func.count()).select_from(CostRecord))).scalar_one()
    return {
        "chats": int(chats),
        "turns": int(turns),
        "answers": int(answers),
        "verdicts": int(verdicts),
        "costs": int(costs),
    }


@pytest.mark.asyncio
async def test_refine_creates_no_chat_turn_answer_verdict_or_cost(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(db, auth, models=["gpt-4.1", "claude"])
    provider = ScriptedProvider(
        [
            "Improved by gpt",
            "Improved by claude",
            "Final synthesized prompt",
        ]
    )
    _patch_providers(monkeypatch, provider)

    brain_mock = AsyncMock(side_effect=AssertionError("Brain must not be called"))
    monkeypatch.setattr(
        "app.services.brain_service.brain_service.get_context_for_user",
        brain_mock,
    )
    memory_mock = AsyncMock(side_effect=AssertionError("Rolling memory must not be called"))
    monkeypatch.setattr(
        "app.services.chat_memory_service.chat_memory_service.build_recent_conversation_context",
        memory_mock,
    )

    before = await _counts(db)
    result = await prompt_builder_service.refine(
        db,
        auth,
        messages=[
            PromptBuilderRefineMessage(
                role="user",
                content="Improve this prompt: inspect our memory system.",
            )
        ],
        model_set_id=model_set.slug,
    )
    after = await _counts(db)

    assert result.improved_prompt == "Final synthesized prompt"
    assert result.assistant_message == "Final synthesized prompt"
    assert (
        before
        == after
        == {
            "chats": 0,
            "turns": 0,
            "answers": 0,
            "verdicts": 0,
            "costs": 0,
        }
    )
    brain_mock.assert_not_awaited()
    memory_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_refine_does_not_update_existing_chat_rolling_memory(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(db, auth)
    chat = Chat(
        org_id=auth.org_id,
        created_by=auth.user.id,
        title="Chat A",
        rolling_memory="SECRET_CHAT_A_MEMORY",
    )
    db.add(chat)
    await db.flush()

    provider = ScriptedProvider(["proposal-a", "proposal-b", "refined prompt"])
    _patch_providers(monkeypatch, provider)

    await prompt_builder_service.refine(
        db,
        auth,
        messages=[PromptBuilderRefineMessage(role="user", content="Improve: hello")],
        model_set_id=model_set.slug,
    )

    await db.refresh(chat)
    assert chat.rolling_memory == "SECRET_CHAT_A_MEMORY"
    for call in provider.calls:
        assert "SECRET_CHAT_A_MEMORY" not in call["system"]
        assert "SECRET_CHAT_A_MEMORY" not in call["user"]


@pytest.mark.asyncio
async def test_refine_request_contains_only_prompt_builder_messages(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(db, auth, models=["gpt-4.1"])
    provider = ScriptedProvider(["council proposal", "final prompt"])
    _patch_providers(monkeypatch, provider)

    await prompt_builder_service.refine(
        db,
        auth,
        messages=[
            PromptBuilderRefineMessage(role="user", content="Improve: first"),
            PromptBuilderRefineMessage(role="assistant", content="First improved"),
            PromptBuilderRefineMessage(role="user", content="Also say do not edit code"),
        ],
        model_set_id=model_set.slug,
    )

    assert len(provider.calls) == 2  # one council + one referee
    for call in provider.calls:
        assert "Improve: first" in call["user"]
        assert "First improved" in call["user"]
        assert "Also say do not edit code" in call["user"]
        assert "Chat A" not in call["user"]


@pytest.mark.asyncio
async def test_refine_uses_model_set_only_for_configuration(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(
        db,
        auth,
        slug="pb-set",
        models=["gpt-4.1", "claude"],
        verdict_model="gpt-4.1",
    )
    model_set.custom_instructions = "ORG_CHAT_CUSTOM_INSTRUCTIONS_SHOULD_NOT_LEAK"
    await db.flush()

    provider = ScriptedProvider(["p1", "p2", "final"])
    _patch_providers(monkeypatch, provider)

    await prompt_builder_service.refine(
        db,
        auth,
        messages=[PromptBuilderRefineMessage(role="user", content="Improve: x")],
        model_set_id=model_set.slug,
    )

    assert len(provider.calls) == 3
    for call in provider.calls:
        assert "ORG_CHAT_CUSTOM_INSTRUCTIONS_SHOULD_NOT_LEAK" not in call["system"]
        assert "ORG_CHAT_CUSTOM_INSTRUCTIONS_SHOULD_NOT_LEAK" not in call["user"]
        assert "Prompt Builder" in call["system"]


@pytest.mark.asyncio
async def test_refine_multi_turn_history(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(db, auth, models=["gpt-4.1"])
    provider = ScriptedProvider(["second draft", "second final"])
    _patch_providers(monkeypatch, provider)

    result = await prompt_builder_service.refine(
        db,
        auth,
        messages=[
            PromptBuilderRefineMessage(role="user", content="Improve: inspect memory"),
            PromptBuilderRefineMessage(
                role="assistant",
                content="Please inspect the memory implementation carefully.",
            ),
            PromptBuilderRefineMessage(
                role="user",
                content="Good, but tell Cursor not to change code.",
            ),
        ],
        model_set_id=model_set.slug,
    )
    assert result.improved_prompt == "second final"
    assert "tell Cursor not to change code" in provider.calls[0]["user"]
    assert "Please inspect the memory implementation carefully." in provider.calls[0]["user"]


@pytest.mark.asyncio
async def test_refine_rejects_invalid_roles_and_limits(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(db, auth)
    _patch_providers(monkeypatch, ScriptedProvider(["x"]))

    with pytest.raises(ValidationError, match="role"):
        await prompt_builder_service.refine(
            db,
            auth,
            messages=[PromptBuilderRefineMessage(role="system", content="nope")],
            model_set_id=model_set.slug,
        )

    with pytest.raises(ValidationError, match="end with a user message"):
        await prompt_builder_service.refine(
            db,
            auth,
            messages=[
                PromptBuilderRefineMessage(role="user", content="hi"),
                PromptBuilderRefineMessage(role="assistant", content="there"),
            ],
            model_set_id=model_set.slug,
        )


@pytest.mark.asyncio
async def test_refine_model_set_access_control(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    other = await create_other_auth(db)
    foreign = await create_model_set(db, other, slug="foreign-set")
    _patch_providers(monkeypatch, ScriptedProvider(["x"]))

    with pytest.raises(NotFoundError):
        await prompt_builder_service.refine(
            db,
            auth,
            messages=[PromptBuilderRefineMessage(role="user", content="Improve: z")],
            model_set_id=foreign.slug,
        )


@pytest.mark.asyncio
async def test_refine_provider_failure_raises_without_persistence(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(db, auth, models=["gpt-4.1"])
    provider = ScriptedProvider(error=RuntimeError("provider down"))
    _patch_providers(monkeypatch, provider)

    with pytest.raises(AppError):
        await prompt_builder_service.refine(
            db,
            auth,
            messages=[PromptBuilderRefineMessage(role="user", content="Improve: fail")],
            model_set_id=model_set.slug,
        )

    assert await _counts(db) == {
        "chats": 0,
        "turns": 0,
        "answers": 0,
        "verdicts": 0,
        "costs": 0,
    }


@pytest.mark.asyncio
async def test_legacy_improve_still_works(auth: AuthContext, monkeypatch: pytest.MonkeyPatch):
    provider = ScriptedProvider(["legacy improved"])
    _patch_providers(monkeypatch, provider)
    result = await prompt_builder_service.improve(auth, "raw idea")
    assert result.improved_prompt == "legacy improved"
    assert provider.calls[0]["max_tokens"] == 20_000
    assert provider.calls[0]["timeout"] == 300.0


@pytest.mark.asyncio
async def test_legacy_improve_uses_effective_20k_output_limit(
    auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    provider = ScriptedProvider(["legacy improved"])
    _patch_providers(monkeypatch, provider, context_length=400_000)
    await prompt_builder_service.improve(auth, "raw idea")
    assert provider.calls[0]["max_tokens"] == PROMPT_BUILDER_MAX_OUTPUT_TOKENS
    assert provider.calls[0]["max_tokens"] != 1024


@pytest.mark.asyncio
async def test_legacy_improve_respects_model_completion_limit(
    auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    provider = ScriptedProvider(["legacy improved"])
    _patch_providers(monkeypatch, provider, max_completion_tokens=4096)
    await prompt_builder_service.improve(auth, "raw idea")
    assert provider.calls[0]["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_legacy_improve_rejects_truncated_finish_reason(
    auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    provider = ScriptedProvider(["cut off"], finish_reasons=["length"])
    _patch_providers(monkeypatch, provider)
    with pytest.raises(AppError, match="output limit before completing"):
        await prompt_builder_service.improve(auth, "raw idea")


@pytest.mark.asyncio
async def test_normal_chat_model_set_resolution_unchanged(db: AsyncSession, auth: AuthContext):
    """Smoke: chat_service still resolves the same model set slug used by Prompt Builder."""
    from app.services.chat_service import chat_service

    model_set = await create_model_set(db, auth, slug="shared-set")
    resolved = await chat_service._resolve_model_set(db, auth, "shared-set")
    assert resolved.id == model_set.id
    assert list(resolved.models) == list(model_set.models)


@pytest.mark.asyncio
async def test_refine_preserves_verbatim_long_history(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(db, auth, models=["gpt-4.1"])
    generated = "  generated prompt\r\nkeep spacing  "
    provider = ScriptedProvider([" proposal ", generated])
    _patch_providers(monkeypatch, provider)
    original = "  original\r\n" + ("long text " * 700)
    refinement = "\trefine without trimming\n"

    result = await prompt_builder_service.refine(
        db,
        auth,
        messages=[
            PromptBuilderRefineMessage(role="user", content=original),
            PromptBuilderRefineMessage(role="assistant", content=" prior output \n"),
            PromptBuilderRefineMessage(role="user", content=refinement),
        ],
        model_set_id=model_set.slug,
    )

    assert original in provider.calls[0]["user"]
    assert refinement in provider.calls[0]["user"]
    assert result.improved_prompt == generated
    assert all(call["preserve_whitespace"] is True for call in provider.calls)


@pytest.mark.asyncio
async def test_context_overflow_refuses_before_any_model_call_without_truncation(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(db, auth, models=["gpt-4.1"])
    provider = ScriptedProvider(["must not be used"])
    _patch_providers(monkeypatch, provider)
    tiny_pricing = SimpleNamespace(
        ensure_loaded=AsyncMock(return_value=None),
        get_slug_metadata=lambda _slug: {"context_length": 1100},
    )
    monkeypatch.setattr(
        "app.services.prompt_builder_service.get_pricing_service", lambda: tiny_pricing
    )
    history = "VERBATIM_HISTORY_" * 100

    with pytest.raises(AppError, match="Nothing was deleted"):
        await prompt_builder_service.refine(
            db,
            auth,
            messages=[PromptBuilderRefineMessage(role="user", content=history)],
            model_set_id=model_set.slug,
        )

    assert provider.calls == []


@pytest.mark.asyncio
async def test_context_preflight_accounts_for_referee_proposals(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(
        db, auth, models=["gpt-4.1", "claude"], verdict_model="gpt-4.1"
    )
    _patch_providers(monkeypatch, ScriptedProvider())
    response = await prompt_builder_service.context(
        db,
        auth,
        messages=[PromptBuilderRefineMessage(role="user", content="original")],
        model_set_id=model_set.slug,
    )
    assert response.context_usage.limiting_call == "referee"
    assert response.context_usage.estimated_input_tokens > 2 * COUNCIL_MAX_TOKENS


@pytest.mark.asyncio
async def test_prompt_builder_uses_20k_output_limit_and_preflight_reservation(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(db, auth, models=["gpt-4.1"])
    provider = ScriptedProvider(["proposal", "final"])
    _patch_providers(monkeypatch, provider, context_length=400_000)

    context = await prompt_builder_service.context(
        db,
        auth,
        messages=[PromptBuilderRefineMessage(role="user", content="original")],
        model_set_id=model_set.slug,
    )
    await prompt_builder_service.refine(
        db,
        auth,
        messages=[PromptBuilderRefineMessage(role="user", content="original")],
        model_set_id=model_set.slug,
    )

    assert COUNCIL_MAX_TOKENS == 20_000
    assert REFEREE_MAX_TOKENS == 20_000
    assert PROMPT_BUILDER_MAX_OUTPUT_TOKENS == 20_000
    assert context.context_usage.reserved_output_tokens == 20_000
    assert [call["max_tokens"] for call in provider.calls] == [20_000, 20_000]
    assert all(call["max_tokens"] != 1024 for call in provider.calls)
    assert PROMPT_BUILDER_LLM_TIMEOUT_SECONDS == 300.0
    assert all(call["timeout"] == PROMPT_BUILDER_LLM_TIMEOUT_SECONDS for call in provider.calls)


@pytest.mark.asyncio
async def test_every_council_and_referee_generation_uses_effective_20k(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(
        db, auth, models=["gpt-4.1", "claude"], verdict_model="gpt-4.1"
    )
    provider = ScriptedProvider(["proposal-a", "proposal-b", "final"])
    _patch_providers(monkeypatch, provider, context_length=400_000)

    await prompt_builder_service.refine(
        db,
        auth,
        messages=[PromptBuilderRefineMessage(role="user", content="original")],
        model_set_id=model_set.slug,
    )

    assert len(provider.calls) == 3
    assert [call["max_tokens"] for call in provider.calls] == [20_000, 20_000, 20_000]
    assert all(call["max_tokens"] != 1024 for call in provider.calls)
    assert all(call["timeout"] == 300.0 for call in provider.calls)


@pytest.mark.asyncio
async def test_model_completion_limit_caps_effective_output_reservation(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(db, auth, models=["gpt-4.1"])
    provider = ScriptedProvider(["proposal", "final"])
    _patch_providers(monkeypatch, provider, max_completion_tokens=4096)

    context = await prompt_builder_service.context(
        db,
        auth,
        messages=[PromptBuilderRefineMessage(role="user", content="original")],
        model_set_id=model_set.slug,
    )
    await prompt_builder_service.refine(
        db,
        auth,
        messages=[PromptBuilderRefineMessage(role="user", content="original")],
        model_set_id=model_set.slug,
    )

    assert context.context_usage.reserved_output_tokens == 4096
    assert [call["max_tokens"] for call in provider.calls] == [4096, 4096]
    assert all(call["max_tokens"] != 1024 for call in provider.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("truncated_call", ["council", "referee"])
async def test_finish_reason_length_rejects_truncated_prompt(
    db: AsyncSession,
    auth: AuthContext,
    monkeypatch: pytest.MonkeyPatch,
    truncated_call: str,
):
    model_set = await create_model_set(db, auth, models=["gpt-4.1"])
    reasons = ["length", "stop"] if truncated_call == "council" else ["stop", "length"]
    provider = ScriptedProvider(["cut off mid-sentence", "also cut off"], finish_reasons=reasons)
    _patch_providers(monkeypatch, provider)

    with pytest.raises(AppError, match="output limit before completing"):
        await prompt_builder_service.refine(
            db,
            auth,
            messages=[PromptBuilderRefineMessage(role="user", content="original")],
            model_set_id=model_set.slug,
        )

    assert len(provider.calls) == (1 if truncated_call == "council" else 2)


@pytest.mark.asyncio
async def test_long_generated_prompt_is_returned_in_full(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    model_set = await create_model_set(db, auth, models=["gpt-4.1"])
    long_prompt = "complete section\n" * 5000
    provider = ScriptedProvider(["proposal", long_prompt])
    _patch_providers(monkeypatch, provider, context_length=400_000)

    result = await prompt_builder_service.refine(
        db,
        auth,
        messages=[PromptBuilderRefineMessage(role="user", content="original")],
        model_set_id=model_set.slug,
    )

    assert result.improved_prompt == long_prompt


# --- Frontend session contract (mirrors src/lib/promptBuilderSession.ts) ---


def _create_session(seed: str = "") -> dict:
    return {
        "version": 1,
        "originalPrompt": seed,
        "messages": [],
        "draft": seed,
        "latestPrompt": None,
        "modelSetId": "set-a",
    }


def _begin_send(session: dict, user_text: str) -> dict:
    if not user_text.strip():
        return session
    capture_original = session["originalPrompt"] == "" and len(session["messages"]) == 0
    next_session = {
        **session,
        "originalPrompt": user_text if capture_original else session["originalPrompt"],
        "messages": [*session["messages"], {"role": "user", "content": user_text}],
        "draft": "",
    }
    if capture_original:
        next_session["intentionalEmpty"] = False
    return next_session


def _apply_success(session: dict, improved: str) -> dict:
    return {
        **session,
        "messages": [*session["messages"], {"role": "assistant", "content": improved}],
        "latestPrompt": improved,
    }


def test_prompt_builder_service_has_no_1024_output_limit():
    import inspect
    from app.core.config import Settings
    from app.services import prompt_builder_service as module

    source = inspect.getsource(module)
    assert "1024" not in source
    assert "1_024" not in source
    assert module.PROMPT_BUILDER_MAX_OUTPUT_TOKENS == 20_000
    assert module.COUNCIL_MAX_TOKENS == 20_000
    assert module.REFEREE_MAX_TOKENS == 20_000
    assert module.PROMPT_BUILDER_LLM_TIMEOUT_SECONDS == 300.0
    assert Settings.model_fields["llm_timeout_seconds"].default == 120.0
    assert module.PROMPT_BUILDER_LLM_TIMEOUT_SECONDS > Settings.model_fields["llm_timeout_seconds"].default


def test_new_session_starts_empty_without_copying_composer():
    composer = "Some text currently in composer"
    session = {
        "version": 1,
        "originalPrompt": "",
        "messages": [],
        "draft": "",
        "latestPrompt": None,
        "modelSetId": "set-a",
        "intentionalEmpty": True,
    }
    assert session["originalPrompt"] == ""
    assert session["draft"] == ""
    assert session["messages"] == []
    assert session["latestPrompt"] is None
    assert composer == "Some text currently in composer"


def test_new_session_first_send_captures_original_prompt_verbatim():
    session = {
        "version": 1,
        "originalPrompt": "",
        "messages": [],
        "draft": "",
        "latestPrompt": None,
        "modelSetId": "set-a",
        "intentionalEmpty": True,
    }
    first = "ORIGINAL PROMPT TEST 123"
    session = _begin_send(session, first)
    assert session["originalPrompt"] == first
    assert session["messages"][0]["content"] == first
    session = _begin_send(session, "Make it more technical")
    assert session["originalPrompt"] == first
    session = _begin_send(session, "Add clinical governance")
    assert session["originalPrompt"] == first


def test_session_is_lossless_across_close_and_use():
    original = "  seed from Chat A composer\r\n"
    session = _create_session(original)
    session = _begin_send(session, session["draft"])
    session = _apply_success(session, "  improved\n")
    closed = session
    used = session["latestPrompt"]
    assert closed == session
    assert used == "  improved\n"
    assert session["originalPrompt"] == original
    assert session["messages"][0]["content"] == original
    assert len(session["messages"]) == 2


def test_long_messages_are_not_trimmed_or_removed():
    original = " x " * 20_000
    session = _begin_send(_create_session(original), original)
    for index in range(50):
        session = _apply_success(session, f" assistant {index} \n")
        session = _begin_send(session, f" user {index} \n")
    assert session["originalPrompt"] == original
    assert len(session["messages"]) == 101
    assert session["messages"][0]["content"] == original
    assert session["messages"][-1]["content"] == " user 49 \n"
