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
from app.services.prompt_builder_service import prompt_builder_service
from tests.conftest import create_model_set, create_other_auth


class ScriptedProvider:
    def __init__(self, responses: list[str] | None = None, *, error: Exception | None = None):
        self.responses = list(responses or [])
        self.error = error
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if not self.responses:
            return LLMResponse(text="", tokens_input=1, tokens_output=1)
        text = self.responses.pop(0)
        return LLMResponse(text=text, tokens_input=1, tokens_output=1)


def _patch_providers(monkeypatch: pytest.MonkeyPatch, provider: ScriptedProvider) -> None:
    registry = SimpleNamespace(
        get_provider=lambda _name: provider,
        validate_configured=lambda: None,
    )
    monkeypatch.setattr(
        "app.services.prompt_builder_service.get_provider_registry",
        lambda: registry,
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
    assert before == after == {
        "chats": 0,
        "turns": 0,
        "answers": 0,
        "verdicts": 0,
        "costs": 0,
    }
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


@pytest.mark.asyncio
async def test_normal_chat_model_set_resolution_unchanged(
    db: AsyncSession, auth: AuthContext
):
    """Smoke: chat_service still resolves the same model set slug used by Prompt Builder."""
    from app.services.chat_service import chat_service

    model_set = await create_model_set(db, auth, slug="shared-set")
    resolved = await chat_service._resolve_model_set(db, auth, "shared-set")
    assert resolved.id == model_set.id
    assert list(resolved.models) == list(model_set.models)


# --- Frontend session contract (mirrors src/lib/promptBuilderSession.ts) ---


def _create_session(seed: str = "") -> dict:
    return {
        "messages": [],
        "draft": seed,
        "candidate": None,
        "error": None,
        "loading": False,
    }


def _begin_send(session: dict, user_text: str) -> dict:
    content = user_text.strip()
    if not content:
        return {**session, "error": "Enter a prompt to improve."}
    return {
        **session,
        "messages": [*session["messages"], {"role": "user", "content": content}],
        "draft": "",
        "error": None,
        "loading": True,
    }


def _apply_success(session: dict, improved: str) -> dict:
    content = improved.strip()
    return {
        **session,
        "messages": [*session["messages"], {"role": "assistant", "content": content}],
        "candidate": content,
        "error": None,
        "loading": False,
    }


def _apply_failure(session: dict, error: str) -> dict:
    return {**session, "error": error, "loading": False}


def _clear_session() -> dict:
    return _create_session("")


def test_session_seed_and_close_discards_history():
    session = _create_session("seed from Chat A composer")
    assert session["draft"] == "seed from Chat A composer"
    session = _begin_send(session, session["draft"])
    session = _apply_success(session, "improved")
    assert session["candidate"] == "improved"
    assert len(session["messages"]) == 2
    cleared = _clear_session()
    assert cleared == {
        "messages": [],
        "draft": "",
        "candidate": None,
        "error": None,
        "loading": False,
    }


def test_use_prompt_returns_candidate_without_implying_send():
    session = _create_session("")
    session = _begin_send(session, "Improve: x")
    session = _apply_success(session, "Final candidate")
    used = session["candidate"]
    assert used == "Final candidate"
    cleared = _clear_session()
    assert cleared["messages"] == []
    assert used == "Final candidate"


def test_failure_keeps_existing_candidate():
    session = _create_session("")
    session = _begin_send(session, "Improve: first")
    session = _apply_success(session, "good candidate")
    session = _begin_send(session, "Make it worse somehow")
    session = _apply_failure(session, "Could not improve prompt. Please try again.")
    assert session["candidate"] == "good candidate"
    assert session["error"]
    assert session["loading"] is False
    assert session["messages"][-1]["role"] == "user"
