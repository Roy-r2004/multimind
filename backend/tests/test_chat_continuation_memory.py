"""Chat continuation memory: recent history + rolling older-chat memory."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.dependencies import AuthContext
from app.db.base import Base
from app.db.models import (
    Chat,
    CostRecord,
    ModelAnswer,
    ModelAnswerStatus,
    Organization,
    OrgMembership,
    OrgRole,
    Strategy,
    Turn,
    TurnStatus,
    UsageKind,
    User,
    UserBrain,
    Verdict,
)
from app.llm.prompt_engine import PromptEngine
from app.services import chat_memory_service as chat_memory_module
from app.services.chat_memory_service import (
    CHAT_MEMORY_ASSISTANT_MAX_TOKENS,
    CHAT_MEMORY_STORED_MAX_TOKENS,
    RECENT_HISTORY_MAX_CHARS,
    RECENT_HISTORY_MAX_TOKENS,
    RECENT_HISTORY_MAX_TURNS,
    TRUNCATION_MARKER,
    TURN_SEPARATOR,
    TurnAssistantResult,
    TurnHistoryEntry,
    chat_memory_service,
    format_recent_conversation_context,
    format_turn_history_block,
    partition_history_by_recent_budget,
    select_recent_history_under_budget,
    truncate_text_under_token_budget,
)
from app.services.chat_service import (
    CHALLENGE_TURN_MARKER,
    _run_chat_memory_update_best_effort,
    chat_service,
)


@pytest.fixture
async def memory_env(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'chat_memory.db'}", poolclass=NullPool
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as db:
        org = Organization(name="Mem Org", slug="mem-org")
        user = User(email="mem@example.com", hashed_password="x", full_name="Mem User")
        db.add_all([org, user])
        await db.flush()
        db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.OWNER))
        chat = Chat(org_id=org.id, created_by=user.id, title="Memory chat")
        db.add(chat)
        await db.commit()
        auth = AuthContext(user=user, org_id=org.id, role=OrgRole.OWNER)
        chat_id = chat.id
        org_id = org.id
        user_id = user.id

    try:
        yield SimpleNamespace(
            Session=Session,
            auth=auth,
            chat_id=chat_id,
            org_id=org_id,
            user_id=user_id,
        )
    finally:
        await engine.dispose()


async def _add_completed_turn(
    db: AsyncSession,
    *,
    chat_id: str,
    index: int,
    created_at: datetime | None = None,
    reason: str | None = ...,  # type: ignore[assignment]
    council_text: str | None = ...,  # type: ignore[assignment]
    status: TurnStatus = TurnStatus.COMPLETED,
    error_message: str | None = None,
    deleted_at: datetime | None = None,
    with_verdict: bool = True,
) -> Turn:
    if reason is ...:
        reason = f"Reason {index}"
    if council_text is ...:
        council_text = f"SECRET_COUNCIL_ANSWER_{index}"
    turn = Turn(
        chat_id=chat_id,
        user_message=f"Prompt {index}",
        model_set_id="test-set",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gemini",
        status=status,
        error_message=error_message,
        deleted_at=deleted_at,
    )
    if created_at is not None:
        turn.created_at = created_at
    db.add(turn)
    await db.flush()
    if council_text is not None:
        db.add(
            ModelAnswer(
                turn_id=turn.id,
                model_id="gpt-4.1",
                text=council_text,
                status=ModelAnswerStatus.COMPLETED,
            )
        )
    if with_verdict:
        db.add(
            Verdict(
                turn_id=turn.id,
                model_id="gemini",
                strategy=Strategy.SYNTHESIZE,
                text=f"Verdict {index}",
                reason=reason if reason is not None else "",
            )
        )
    await db.flush()
    return turn


class AccumulatingMemoryProvider:
    """Small deterministic fake that preserves the real memory prompt's facts."""

    def __init__(self) -> None:
        self.fail = False
        self.remaining_successes: int | None = None
        self.merged_questions: list[str] = []

    async def complete(self, *, system: str, **_kwargs):
        if self.fail or self.remaining_successes == 0:
            return SimpleNamespace(text="", tokens_input=2, tokens_output=0, cost_usd=0)
        if self.remaining_successes is not None:
            self.remaining_successes -= 1
        match = re.search(
            r"\*\*User question:\*\*\s*\n(.+?)\n\n\*\*Final verdict:", system, re.DOTALL
        )
        question = match.group(1).strip() if match else "unknown"
        self.merged_questions.append(question)
        current_match = re.search(
            r"## Current Rolling Memory\s*\n(.+?)\n\n## Newly Expired Turn", system, re.DOTALL
        )
        current = current_match.group(1).strip() if current_match else ""
        if current.startswith("(Empty"):
            current = ""
        text = "\n".join(part for part in (current, question) if part)
        return SimpleNamespace(text=text, tokens_input=2, tokens_output=2, cost_usd=0)


def test_select_recent_history_prefers_newer_blocks_under_budget():
    blocks = [f"OLD-{i}-" + ("x" * 40) for i in range(1, 5)]
    # Budget fits roughly two newest full blocks + separator.
    max_chars = len(blocks[-1]) + len(TURN_SEPARATOR) + len(blocks[-2])
    result = select_recent_history_under_budget(blocks, max_chars=max_chars)
    assert "OLD-4-" in result
    assert "OLD-3-" in result
    assert "OLD-2-" not in result
    assert "OLD-1-" not in result
    # Chronological order preserved among selected.
    assert result.index("OLD-3-") < result.index("OLD-4-")


def test_select_recent_history_truncates_single_oversized_newest_block():
    huge = "NEWEST-" + ("y" * 500)
    result = select_recent_history_under_budget([huge], max_chars=80)
    assert result.endswith(TRUNCATION_MARKER.strip()) or TRUNCATION_MARKER in result
    assert len(result) <= 80
    assert result.startswith("NEWEST-")


def test_format_turn_history_omits_empty_reason_and_never_mentions_council():
    block = format_turn_history_block("Q?", "V!", None)
    assert "User question:" in block
    assert "Final verdict:" in block
    assert "Verdict rationale:" not in block
    assert "council" not in block.lower()

    with_reason = format_turn_history_block("Q?", "V!", "Because")
    assert "Verdict rationale:\nBecause" in with_reason


def test_format_recent_conversation_context_chronological():
    base = datetime(2026, 1, 1, tzinfo=UTC)
    entries = [
        TurnHistoryEntry("t1", "Prompt 1", "Verdict 1", "Reason 1", base),
        TurnHistoryEntry("t2", "Prompt 2", "Verdict 2", None, base + timedelta(minutes=1)),
        TurnHistoryEntry("t3", "Prompt 3", "Verdict 3", "Reason 3", base + timedelta(minutes=2)),
    ]
    text = format_recent_conversation_context(entries)
    assert text is not None
    assert text.index("Prompt 1") < text.index("Prompt 2") < text.index("Prompt 3")
    assert "Reason 2" not in text
    assert "Verdict rationale:\nReason 1" in text


@pytest.mark.asyncio
async def test_recent_history_includes_up_to_ten_chronological_turns(memory_env):
    base = datetime(2026, 2, 1, tzinfo=UTC)
    async with memory_env.Session() as db:
        turns = []
        for i in range(1, 13):
            turns.append(
                await _add_completed_turn(
                    db,
                    chat_id=memory_env.chat_id,
                    index=i,
                    created_at=base + timedelta(minutes=i),
                )
            )
        await db.commit()
        current = turns[-1]
        entries = await chat_memory_service.load_recent_history_entries(
            db, memory_env.chat_id, current.id, current.created_at
        )
        context = await chat_memory_service.build_recent_conversation_context(
            db, memory_env.chat_id, current.id, current.created_at
        )

    # Turn 1 is desired-compactable but has no persisted memory yet, so it is
    # retained as fallback alongside the ten-turn recent suffix.
    assert len(entries) == RECENT_HISTORY_MAX_TURNS + 1
    assert [e.user_message for e in entries] == [f"Prompt {i}" for i in range(1, 12)]
    assert context is not None
    assert context.index("Prompt 2") < context.index("Prompt 11")
    assert "Prompt 12" not in context
    assert entries[0].user_message == "Prompt 1"
    assert "SECRET_COUNCIL_ANSWER" not in context


@pytest.mark.asyncio
async def test_turn_eleven_expires_oldest_into_window_slide(memory_env):
    base = datetime(2026, 3, 1, tzinfo=UTC)
    async with memory_env.Session() as db:
        turns = []
        for i in range(1, 12):
            turns.append(
                await _add_completed_turn(
                    db,
                    chat_id=memory_env.chat_id,
                    index=i,
                    created_at=base + timedelta(minutes=i),
                )
            )
        await db.commit()
        eligible = await chat_memory_service.list_eligible_turns_oldest_first(
            db, memory_env.chat_id
        )
        newly = chat_memory_service.newly_expired_turns(eligible, through_turn_id=None)

    assert len(eligible) == 11
    assert [t.user_message for t, _ in newly] == ["Prompt 1"]
    # The desired recent window excludes turn 1, but raw prompt context cannot
    # exclude it until the memory watermark actually covers it.
    async with memory_env.Session() as db:
        next_turn = await _add_completed_turn(
            db,
            chat_id=memory_env.chat_id,
            index=12,
            created_at=base + timedelta(minutes=12),
            with_verdict=False,
            council_text=None,
            status=TurnStatus.PENDING,
        )
        await db.commit()
        entries = await chat_memory_service.load_recent_history_entries(
            db, memory_env.chat_id, next_turn.id, next_turn.created_at
        )
    assert [e.user_message for e in entries] == [f"Prompt {i}" for i in range(1, 12)]


@pytest.mark.asyncio
async def test_single_answer_history_included_but_broken_verdictless_turns_excluded(memory_env):
    base = datetime(2026, 4, 1, tzinfo=UTC)
    async with memory_env.Session() as db:
        keep = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=1, created_at=base)
        await _add_completed_turn(
            db,
            chat_id=memory_env.chat_id,
            index=2,
            created_at=base + timedelta(minutes=1),
            deleted_at=base + timedelta(hours=1),
        )
        await _add_completed_turn(
            db,
            chat_id=memory_env.chat_id,
            index=3,
            created_at=base + timedelta(minutes=2),
            error_message=CHALLENGE_TURN_MARKER,
        )
        single = await _add_completed_turn(
            db,
            chat_id=memory_env.chat_id,
            index=4,
            created_at=base + timedelta(minutes=3),
            with_verdict=False,
            status=TurnStatus.COMPLETED,
        )
        broken_multi = await _add_completed_turn(
            db,
            chat_id=memory_env.chat_id,
            index=5,
            created_at=base + timedelta(minutes=4),
            with_verdict=False,
            status=TurnStatus.COMPLETED,
        )
        db.add(
            ModelAnswer(
                turn_id=broken_multi.id,
                model_id="claude",
                text="A second answer without a verdict",
                status=ModelAnswerStatus.COMPLETED,
            )
        )
        current = await _add_completed_turn(
            db,
            chat_id=memory_env.chat_id,
            index=6,
            created_at=base + timedelta(minutes=5),
            status=TurnStatus.PENDING,
            with_verdict=False,
            council_text=None,
        )
        await db.commit()
        entries = await chat_memory_service.load_recent_history_entries(
            db, memory_env.chat_id, current.id, current.created_at
        )
        context = await chat_memory_service.build_recent_conversation_context(
            db, memory_env.chat_id, current.id, current.created_at
        )

    assert [e.turn_id for e in entries] == [keep.id, single.id]
    assert context is not None
    assert "Prompt 1" in context
    assert "Prompt 2" not in context
    assert "Prompt 3" not in context
    assert "Prompt 4" in context
    assert "Assistant answer:\nSECRET_COUNCIL_ANSWER_4" in context
    assert "Prompt 5" not in context


@pytest.mark.asyncio
async def test_recent_history_character_budget_deterministic(memory_env):
    base = datetime(2026, 5, 1, tzinfo=UTC)
    async with memory_env.Session() as db:
        for i in range(1, 6):
            turn = Turn(
                chat_id=memory_env.chat_id,
                user_message=("Q" * 200) + f"-{i}",
                model_set_id="test-set",
                strategy=Strategy.SYNTHESIZE,
                verdict_model="gemini",
                status=TurnStatus.COMPLETED,
                created_at=base + timedelta(minutes=i),
            )
            db.add(turn)
            await db.flush()
            db.add(
                Verdict(
                    turn_id=turn.id,
                    model_id="gemini",
                    strategy=Strategy.SYNTHESIZE,
                    text=("V" * 200) + f"-{i}",
                    reason="",
                )
            )
        current = Turn(
            chat_id=memory_env.chat_id,
            user_message="current",
            model_set_id="test-set",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=TurnStatus.PENDING,
            created_at=base + timedelta(minutes=10),
        )
        db.add(current)
        await db.commit()

        tiny = 500
        ctx_a = await chat_memory_service.build_recent_conversation_context(
            db, memory_env.chat_id, current.id, current.created_at, max_chars=tiny
        )
        ctx_b = await chat_memory_service.build_recent_conversation_context(
            db, memory_env.chat_id, current.id, current.created_at, max_chars=tiny
        )

    assert ctx_a == ctx_b
    assert ctx_a is not None
    assert len(ctx_a) > tiny
    assert all(f"-{i}" in ctx_a for i in range(1, 6))
    # No compactable turn may be dropped before persistence.
    assert "-5" in ctx_a
    assert "-1" in ctx_a


@pytest.mark.asyncio
async def test_rolling_memory_merges_expired_once_and_is_idempotent(memory_env, monkeypatch):
    base = datetime(2026, 6, 1, tzinfo=UTC)
    calls: list[str] = []

    class FakeProvider:
        async def complete(self, *, system, user, model, max_tokens=4096):
            calls.append(system)
            # Echo a deterministic memory update for assertions.
            return SimpleNamespace(
                text="Upload supports PDF only.",
                tokens_input=10,
                tokens_output=5,
                cost_usd=0.001,
            )

        def parse_json_response(self, text: str):
            return {}

    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _p: FakeProvider()),
    )
    monkeypatch.setattr(
        chat_memory_module,
        "get_model",
        lambda _mid: SimpleNamespace(provider="openai", provider_model="gpt-4.1"),
    )
    monkeypatch.setattr(chat_memory_module, "resolve_llm_cost", lambda *a, **k: 0.001)

    async with memory_env.Session() as db:
        for i in range(1, 12):
            await _add_completed_turn(
                db,
                chat_id=memory_env.chat_id,
                index=i,
                created_at=base + timedelta(minutes=i),
                with_verdict=i != 1,
            )
        await db.commit()

        merged = await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        await db.commit()
        chat = await db.get(Chat, memory_env.chat_id)
        through_1 = chat.rolling_memory_through_turn_id
        memory_1 = chat.rolling_memory

        merged_again = await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        await db.commit()
        chat = await db.get(Chat, memory_env.chat_id)

        costs = (
            (
                await db.execute(
                    select(CostRecord).where(
                        CostRecord.chat_id == memory_env.chat_id,
                        CostRecord.kind == UsageKind.CHAT_MEMORY,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert merged == 1
    assert merged_again == 0
    assert len(calls) == 1
    assert memory_1 == "Upload supports PDF only."
    assert chat.rolling_memory == memory_1
    assert chat.rolling_memory_through_turn_id == through_1
    assert chat.rolling_memory_updated_at is not None
    assert len(costs) == 1
    assert "Prompt 1" in calls[0]
    assert "SECRET_COUNCIL_ANSWER_1" in calls[0]


@pytest.mark.asyncio
async def test_later_correction_supersedes_old_memory_fact(memory_env, monkeypatch):
    base = datetime(2026, 7, 1, tzinfo=UTC)
    responses = [
        "Upload supports PDF only.",
        "Upload supports PDF and images.",
    ]

    class FakeProvider:
        async def complete(self, *, system, user, model, max_tokens=4096):
            return SimpleNamespace(
                text=responses.pop(0),
                tokens_input=1,
                tokens_output=1,
                cost_usd=0.0,
            )

    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _p: FakeProvider()),
    )
    monkeypatch.setattr(
        chat_memory_module,
        "get_model",
        lambda _mid: SimpleNamespace(provider="openai", provider_model="gpt-4.1"),
    )
    monkeypatch.setattr(chat_memory_module, "resolve_llm_cost", lambda *a, **k: 0.0)

    async with memory_env.Session() as db:
        for i in range(1, 12):
            await _add_completed_turn(
                db,
                chat_id=memory_env.chat_id,
                index=i,
                created_at=base + timedelta(minutes=i),
            )
        await db.commit()

        await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        await db.commit()
        chat = await db.get(Chat, memory_env.chat_id)
        assert chat.rolling_memory == "Upload supports PDF only."

        await _add_completed_turn(
            db,
            chat_id=memory_env.chat_id,
            index=12,
            created_at=base + timedelta(minutes=12),
            reason="policy update",
        )
        await db.commit()

        await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        await db.commit()
        chat = await db.get(Chat, memory_env.chat_id)

    assert chat.rolling_memory == "Upload supports PDF and images."
    assert "PDF only" not in (chat.rolling_memory or "")


@pytest.mark.asyncio
async def test_memory_model_failure_does_not_fail_completed_turn(memory_env, monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("memory model down")

    monkeypatch.setattr(
        chat_memory_module.chat_memory_service,
        "merge_expired_turns",
        boom,
    )

    # Direct best-effort helper must swallow errors.
    await _run_chat_memory_update_best_effort(
        chat_id=memory_env.chat_id,
        org_id=memory_env.org_id,
        project_id=None,
        turn_id="any-turn",
    )

    async with memory_env.Session() as db:
        turn = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=1)
        await db.commit()
        reloaded = await db.get(Turn, turn.id)
        assert reloaded.status == TurnStatus.COMPLETED
        assert reloaded.error_message is None
        verdict = (await db.execute(select(Verdict).where(Verdict.turn_id == turn.id))).scalar_one()
        assert verdict.text == "Verdict 1"


@pytest.mark.asyncio
async def test_prompts_receive_rolling_memory_and_recent_history():
    engine = PromptEngine()
    model_prompt = engine.model_answer_prompt(
        user_message="What next?",
        model_id="gpt-4.1",
        model_name="GPT",
        vendor="OpenAI",
        model_set_name="Council",
        rolling_chat_memory="Remember: budget is $10k.",
        recent_conversation_context="User question:\nPrior?\n\nFinal verdict:\nPrior answer",
    )
    verdict_prompt = engine.verdict_prompt(
        strategy="Synthesize",
        user_message="What next?",
        model_answers=[
            {
                "answer_id": "answer-gpt",
                "model_id": "gpt-4.1",
                "model_name": "GPT",
                "vendor": "OpenAI",
                "text": "Current council answer only",
                "confidence": 80,
                "failed": False,
            }
        ],
        rolling_chat_memory="Remember: budget is $10k.",
        recent_conversation_context="User question:\nPrior?\n\nFinal verdict:\nPrior answer",
    )

    for prompt in (model_prompt, verdict_prompt):
        assert "## Older Chat Memory" in prompt
        assert "Remember: budget is $10k." in prompt
        assert "## Recent Conversation" in prompt
        assert "Prior?" in prompt
        assert "## Current User Question" in prompt
        assert "What next?" in prompt
        assert "Previous final verdict" not in prompt

    assert "Current council answer only" in verdict_prompt
    assert "Current council answer only" not in model_prompt


def test_orchestrator_prompt_kwargs_match_rolling_memory_architecture():
    """Regression: orchestrator must not pass removed previous_verdict_context."""
    import inspect
    from pathlib import Path

    from app.db.models import Chat
    from app.llm import orchestrator as orch_mod
    from app.llm.orchestrator import TurnContext
    from app.llm.prompt_engine import PromptEngine
    from app.services import chat_service as chat_service_mod

    orch_src = Path(orch_mod.__file__).read_text(encoding="utf-8")
    chat_src = Path(chat_service_mod.__file__).read_text(encoding="utf-8")
    assert "previous_verdict_context" not in orch_src
    assert "previous_verdict_context" not in chat_src
    assert "rolling_chat_memory=ctx.rolling_chat_memory" in orch_src
    assert "recent_conversation_context=ctx.recent_conversation_context" in orch_src
    assert "rolling_chat_memory" in TurnContext.__dataclass_fields__
    assert "recent_conversation_context" in TurnContext.__dataclass_fields__
    assert "previous_verdict_context" not in TurnContext.__dataclass_fields__
    assert hasattr(Chat, "rolling_memory")
    assert "rolling_memory" in Chat.__table__.columns

    model_params = inspect.signature(PromptEngine.model_answer_prompt).parameters
    verdict_params = inspect.signature(PromptEngine.verdict_prompt).parameters
    assert "rolling_chat_memory" in model_params
    assert "recent_conversation_context" in model_params
    assert "previous_verdict_context" not in model_params
    assert "previous_verdict_context" not in verdict_params

    # Binding smoke test: kwargs orchestrator passes must be accepted (no TypeError).
    engine = PromptEngine()
    engine.model_answer_prompt(
        user_message="hi",
        model_id="gpt-4.1",
        model_name="GPT",
        vendor="OpenAI",
        model_set_name="Council",
        rolling_chat_memory="older memory",
        recent_conversation_context="recent history",
    )
    engine.verdict_prompt(
        strategy="Referee",
        user_message="hi",
        model_answers=[],
        rolling_chat_memory="older memory",
        recent_conversation_context="recent history",
    )


@pytest.mark.asyncio
async def test_brain_remains_unchanged_by_chat_memory_merge(memory_env, monkeypatch):
    base = datetime(2026, 8, 1, tzinfo=UTC)

    class FakeProvider:
        async def complete(self, *, system, user, model, max_tokens=4096):
            return SimpleNamespace(
                text="Chat-only memory fact.",
                tokens_input=1,
                tokens_output=1,
                cost_usd=0.0,
            )

    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _p: FakeProvider()),
    )
    monkeypatch.setattr(
        chat_memory_module,
        "get_model",
        lambda _mid: SimpleNamespace(provider="openai", provider_model="gpt-4.1"),
    )
    monkeypatch.setattr(chat_memory_module, "resolve_llm_cost", lambda *a, **k: 0.0)

    async with memory_env.Session() as db:
        brain = UserBrain(
            user_id=memory_env.user_id,
            org_id=memory_env.org_id,
            user_name="Mem User",
            summary="Original brain summary",
            thinking_style="careful",
            likes=["clarity"],
            dislikes=["fluff"],
            memories=[{"id": "m1", "title": "keep", "insight": "stay"}],
            lesson_count=1,
        )
        db.add(brain)
        for i in range(1, 12):
            await _add_completed_turn(
                db,
                chat_id=memory_env.chat_id,
                index=i,
                created_at=base + timedelta(minutes=i),
            )
        await db.commit()

        await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        await db.commit()

        brain = (
            await db.execute(select(UserBrain).where(UserBrain.user_id == memory_env.user_id))
        ).scalar_one()
        chat = await db.get(Chat, memory_env.chat_id)

    assert brain.summary == "Original brain summary"
    assert brain.thinking_style == "careful"
    assert brain.likes == ["clarity"]
    assert brain.dislikes == ["fluff"]
    assert brain.memories == [{"id": "m1", "title": "keep", "insight": "stay"}]
    assert chat.rolling_memory == "Chat-only memory fact."


@pytest.mark.asyncio
async def test_chat_service_recent_context_wrapper_excludes_deleted(memory_env):
    base = datetime(2026, 9, 1, tzinfo=UTC)
    async with memory_env.Session() as db:
        turns = []
        for i in range(1, 4):
            turns.append(
                await _add_completed_turn(
                    db,
                    chat_id=memory_env.chat_id,
                    index=i,
                    created_at=base + timedelta(minutes=i),
                )
            )
        turns[1].deleted_at = base + timedelta(hours=1)
        await db.commit()
        context = await chat_service._recent_conversation_context(
            db, memory_env.chat_id, turns[2].id, None
        )

    assert context is not None
    assert "Prompt 1" in context
    assert "Verdict 1" in context
    assert "Prompt 2" not in context
    assert "Verdict 2" not in context
    assert "SECRET_COUNCIL_ANSWER" not in context


def test_recent_history_constants():
    assert RECENT_HISTORY_MAX_TURNS == 10
    assert RECENT_HISTORY_MAX_CHARS == 30_000
    assert RECENT_HISTORY_MAX_TOKENS == 7_500


def test_unified_partition_compacts_large_turns_before_turn_eleven_without_gap():
    pairs = []
    for index in range(6):
        turn = Turn(
            id=f"turn-{index}",
            chat_id="chat",
            user_message=f"Question {index} " + ("q" * 4_000),
            model_set_id="set",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=TurnStatus.COMPLETED,
        )
        pairs.append((turn, TurnAssistantResult("v" * 4_000, None, True)))

    partition = partition_history_by_recent_budget(pairs, max_tokens=2_100, max_turns=10)
    compact_ids = {turn.id for turn, _ in partition.compactable}
    recent_ids = {turn.id for turn, _ in partition.recent}

    assert compact_ids
    assert recent_ids
    assert compact_ids.isdisjoint(recent_ids)
    assert compact_ids | recent_ids == {turn.id for turn, _ in pairs}


def test_unified_partition_keeps_newest_oversized_turn_for_explicit_truncation():
    turn = Turn(
        id="newest",
        chat_id="chat",
        user_message="Q" * 20_000,
        model_set_id="set",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gemini",
        status=TurnStatus.COMPLETED,
    )
    result = TurnAssistantResult("A" * 20_000, None, False)
    partition = partition_history_by_recent_budget([(turn, result)], max_tokens=100, max_turns=10)
    entry = TurnHistoryEntry(turn.id, turn.user_message, result.text, None, None, False)
    rendered = format_recent_conversation_context([entry], max_chars=400)

    assert [item.id for item, _ in partition.recent] == ["newest"]
    assert not partition.compactable
    assert rendered is not None
    assert TRUNCATION_MARKER.strip() in rendered


def test_memory_source_and_stored_output_token_bounds_are_explicit():
    huge = "x" * 100_000
    source = truncate_text_under_token_budget(huge, CHAT_MEMORY_ASSISTANT_MAX_TOKENS)
    stored = truncate_text_under_token_budget(huge, CHAT_MEMORY_STORED_MAX_TOKENS)

    assert len(source) <= CHAT_MEMORY_ASSISTANT_MAX_TOKENS * 4
    assert len(stored) <= CHAT_MEMORY_STORED_MAX_TOKENS * 4
    assert TRUNCATION_MARKER.strip() in source
    assert TRUNCATION_MARKER.strip() in stored


@pytest.mark.asyncio
async def test_empty_memory_output_does_not_advance_watermark(memory_env, monkeypatch):
    base = datetime(2026, 10, 1, tzinfo=UTC)

    class EmptyProvider:
        async def complete(self, **_kwargs):
            return SimpleNamespace(text="", tokens_input=1, tokens_output=0, cost_usd=0.0)

    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: EmptyProvider()),
    )

    async with memory_env.Session() as db:
        for index in range(11):
            await _add_completed_turn(
                db,
                chat_id=memory_env.chat_id,
                index=index,
                created_at=base + timedelta(minutes=index),
            )
        await db.commit()
        assert (
            await chat_memory_service.merge_expired_turns(
                db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
            )
            == 0
        )
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)

    assert chat.rolling_memory is None
    assert chat.rolling_memory_through_turn_id is None


@pytest.mark.asyncio
async def test_concurrent_same_watermark_discards_stale_candidate_and_bills_once(
    memory_env, monkeypatch
):
    base = datetime(2026, 11, 1, tzinfo=UTC)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    call_count = 0

    class RacingProvider:
        async def complete(self, **_kwargs):
            nonlocal call_count
            call_count += 1
            call_number = call_count
            if call_number == 1:
                first_started.set()
                await release_first.wait()
            return SimpleNamespace(
                text=f"memory-from-call-{call_number}",
                tokens_input=2,
                tokens_output=1,
                cost_usd=0.0,
            )

    provider = RacingProvider()
    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: provider),
    )
    monkeypatch.setattr(chat_memory_module, "resolve_llm_cost", lambda *_a, **_k: 0.0)

    async with memory_env.Session() as db:
        for index in range(11):
            await _add_completed_turn(
                db,
                chat_id=memory_env.chat_id,
                index=index,
                created_at=base + timedelta(minutes=index),
            )
        await db.commit()

    async def run_merge():
        async with memory_env.Session() as db:
            return await chat_memory_service.merge_expired_turns(
                db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
            )

    first_task = asyncio.create_task(run_merge())
    await asyncio.wait_for(first_started.wait(), timeout=2)
    second_result = await run_merge()
    release_first.set()
    first_result = await asyncio.wait_for(first_task, timeout=2)

    async with memory_env.Session() as db:
        chat = await db.get(Chat, memory_env.chat_id)
        costs = (
            (
                await db.execute(
                    select(CostRecord).where(
                        CostRecord.chat_id == memory_env.chat_id,
                        CostRecord.kind == UsageKind.CHAT_MEMORY,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert sorted([first_result, second_result]) == [0, 1]
    assert chat.rolling_memory == "memory-from-call-2"
    assert len(costs) == 1


@pytest.mark.asyncio
async def test_delete_and_restore_rebuild_from_surviving_history(memory_env, monkeypatch):
    base = datetime(2026, 12, 1, tzinfo=UTC)
    responses = iter(["original compact fact", "restored compact fact"])

    class RebuildProvider:
        async def complete(self, **_kwargs):
            return SimpleNamespace(
                text=next(responses), tokens_input=2, tokens_output=1, cost_usd=0.0
            )

    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: RebuildProvider()),
    )
    monkeypatch.setattr(chat_memory_module, "resolve_llm_cost", lambda *_a, **_k: 0.0)

    async with memory_env.Session() as db:
        turns = []
        for index in range(11):
            turns.append(
                await _add_completed_turn(
                    db,
                    chat_id=memory_env.chat_id,
                    index=index,
                    created_at=base + timedelta(minutes=index),
                )
            )
        await db.commit()
        await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )

    async with memory_env.Session() as db:
        await chat_service.delete_turn(db, memory_env.auth, memory_env.chat_id, turns[0].id)
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)
        assert chat.rolling_memory is None
        assert chat.rolling_memory_through_turn_id is None

        await chat_service.restore_turn(db, memory_env.auth, memory_env.chat_id, turns[0].id)
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)

    assert chat.rolling_memory == "restored compact fact"
    assert chat.rolling_memory_through_turn_id == turns[0].id


@pytest.mark.parametrize("size", [30_000, 30_001, 30_002, 29_999])
def test_renderer_does_not_reselect_authoritative_entries_at_rounding_boundaries(size):
    first = TurnHistoryEntry("first", "FIRST_USER", "FIRST_ANSWER", None, None, True)
    second = TurnHistoryEntry("second", "S" * size, "SECOND_ANSWER", None, None, True)

    rendered = format_recent_conversation_context([first, second], max_chars=30_000)

    assert rendered is not None
    assert "FIRST_USER" in rendered
    assert "FIRST_ANSWER" in rendered
    assert "SECOND_ANSWER" in rendered


def test_oversized_newest_represents_user_and_assistant_with_markers():
    entry = TurnHistoryEntry("large", "USER_FACT_" * 10_000, "ANSWER_FACT_" * 10_000, None, None)

    rendered = format_recent_conversation_context([entry], max_chars=400)

    assert rendered is not None
    assert len(rendered) <= 400
    assert "USER_FACT_" in rendered
    assert "ANSWER_FACT_" in rendered
    assert rendered.count(TRUNCATION_MARKER.strip()) == 2


@pytest.mark.asyncio
async def test_compaction_lag_keeps_fallback_until_watermark_advances(memory_env, monkeypatch):
    base = datetime(2027, 1, 1, tzinfo=UTC)

    class Provider:
        async def complete(self, **_kwargs):
            return SimpleNamespace(
                text="persisted prompt one", tokens_input=2, tokens_output=2, cost_usd=0
            )

    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: Provider()),
    )
    async with memory_env.Session() as db:
        turns = [
            await _add_completed_turn(
                db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i)
            )
            for i in range(1, 12)
        ]
        current = Turn(
            chat_id=memory_env.chat_id,
            user_message="current",
            model_set_id="test-set",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=TurnStatus.PENDING,
            created_at=base + timedelta(minutes=20),
        )
        db.add(current)
        await db.commit()
        before = await chat_memory_service.build_recent_conversation_context(
            db, memory_env.chat_id, current.id, current.created_at
        )
        await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        after = await chat_memory_service.build_recent_conversation_context(
            db, memory_env.chat_id, current.id, current.created_at
        )
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)

    assert "User question:\nPrompt 1\n\n" in (before or "")
    assert "User question:\nPrompt 1\n\n" not in (after or "")
    assert chat.rolling_memory_through_turn_id == turns[0].id


@pytest.mark.asyncio
async def test_failed_compaction_keeps_compactable_turn_in_next_context(memory_env, monkeypatch):
    base = datetime(2027, 2, 1, tzinfo=UTC)

    class FailingProvider:
        async def complete(self, **_kwargs):
            raise RuntimeError("memory unavailable")

    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: FailingProvider()),
    )
    async with memory_env.Session() as db:
        turns = [
            await _add_completed_turn(
                db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i)
            )
            for i in range(1, 12)
        ]
        await db.commit()
        current_id = turns[-1].id
        current_created_at = turns[-1].created_at
        await chat_memory_service.rebuild_memory_best_effort(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        context = await chat_memory_service.build_recent_conversation_context(
            db, memory_env.chat_id, current_id, current_created_at
        )
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)

    assert "Prompt 1" in (context or "")
    assert chat.rolling_memory_through_turn_id is None


@pytest.mark.asyncio
async def test_partial_rebuild_omits_only_successfully_persisted_prefix(memory_env, monkeypatch):
    base = datetime(2027, 3, 1, tzinfo=UTC)
    calls = 0

    class PartialProvider:
        async def complete(self, **_kwargs):
            nonlocal calls
            calls += 1
            text = f"memory through {calls}" if calls <= 5 else ""
            return SimpleNamespace(text=text, tokens_input=2, tokens_output=2, cost_usd=0)

    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: PartialProvider()),
    )
    async with memory_env.Session() as db:
        turns = [
            await _add_completed_turn(
                db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i)
            )
            for i in range(1, 17)
        ]
        await db.commit()
        merged = await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        context = await chat_memory_service.build_recent_conversation_context(
            db, memory_env.chat_id, turns[-1].id, turns[-1].created_at
        )
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)

    assert merged == 5
    assert chat.rolling_memory_through_turn_id == turns[4].id
    assert "Prompt 5" not in (context or "")
    assert "Prompt 6" in (context or "")
    assert "Prompt 15" in (context or "")


@pytest.mark.asyncio
async def test_oversized_generated_summary_does_not_advance_or_bill(memory_env, monkeypatch):
    base = datetime(2027, 4, 1, tzinfo=UTC)

    class OversizedProvider:
        async def complete(self, **_kwargs):
            return SimpleNamespace(
                text="x" * 20_000, tokens_input=2, tokens_output=5000, cost_usd=1
            )

    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: OversizedProvider()),
    )
    async with memory_env.Session() as db:
        for i in range(11):
            await _add_completed_turn(
                db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i)
            )
        await db.commit()
        assert (
            await chat_memory_service.merge_expired_turns(
                db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
            )
            == 0
        )
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)
        costs = (
            (await db.execute(select(CostRecord).where(CostRecord.kind == UsageKind.CHAT_MEMORY)))
            .scalars()
            .all()
        )

    assert chat.rolling_memory_through_turn_id is None
    assert chat.rolling_memory is None
    assert costs == []


@pytest.mark.asyncio
async def test_continuous_cas_conflicts_stop_at_bounded_retry(memory_env, monkeypatch):
    base = datetime(2027, 5, 1, tzinfo=UTC)
    calls = 0

    async def conflict(*_args, **_kwargs):
        nonlocal calls
        calls += 1

    monkeypatch.setattr(chat_memory_service, "_merge_one_turn", conflict)
    async with memory_env.Session() as db:
        for i in range(11):
            await _add_completed_turn(
                db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i)
            )
        await db.commit()
        merged = await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )

    assert merged == 0
    assert calls == chat_memory_module.CHAT_MEMORY_MAX_CAS_RETRIES


@pytest.mark.asyncio
async def test_invalidation_during_memory_llm_call_rejects_stale_write_and_bill(
    memory_env, monkeypatch
):
    base = datetime(2027, 6, 1, tzinfo=UTC)
    started = asyncio.Event()
    release = asyncio.Event()

    class PausedProvider:
        async def complete(self, **_kwargs):
            started.set()
            await release.wait()
            return SimpleNamespace(
                text="stale candidate", tokens_input=2, tokens_output=2, cost_usd=0
            )

    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: PausedProvider()),
    )
    async with memory_env.Session() as db:
        for i in range(11):
            await _add_completed_turn(
                db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i)
            )
        await db.commit()

    async def run_candidate():
        async with memory_env.Session() as db:
            chat = await db.get(Chat, memory_env.chat_id)
            eligible = await chat_memory_service.list_eligible_turns_oldest_first(
                db, memory_env.chat_id
            )
            turn, assistant_result = eligible[0]
            return await chat_memory_service._merge_one_turn(
                db,
                chat=chat,
                turn=turn,
                assistant_result=assistant_result,
                org_id=memory_env.org_id,
                project_id=None,
            )

    task = asyncio.create_task(run_candidate())
    await asyncio.wait_for(started.wait(), timeout=2)
    async with memory_env.Session() as db:
        await chat_memory_service.invalidate_memory(db, chat_id=memory_env.chat_id)
        await db.commit()
    release.set()
    assert await asyncio.wait_for(task, timeout=2) is None

    async with memory_env.Session() as db:
        chat = await db.get(Chat, memory_env.chat_id)
        costs = (
            (
                await db.execute(
                    select(CostRecord).where(
                        CostRecord.chat_id == memory_env.chat_id,
                        CostRecord.kind == UsageKind.CHAT_MEMORY,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert chat.rolling_memory is None
    assert chat.rolling_memory_through_turn_id is None
    assert costs == []


@pytest.mark.asyncio
async def test_delete_summarized_turn_before_watermark_rebuilds_surviving_prefix(
    memory_env, monkeypatch
):
    provider = AccumulatingMemoryProvider()
    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: provider),
    )
    base = datetime(2027, 9, 1, tzinfo=UTC)
    async with memory_env.Session() as db:
        turns = [
            await _add_completed_turn(
                db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i)
            )
            for i in range(14)
        ]
        await db.commit()
        assert (
            await chat_memory_service.merge_expired_turns(
                db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
            )
            == 4
        )
        before = await db.get(Chat, memory_env.chat_id, populate_existing=True)
        assert before.rolling_memory_through_turn_id == turns[3].id

        await chat_service.delete_turn(db, memory_env.auth, memory_env.chat_id, turns[1].id)
        after = await db.get(Chat, memory_env.chat_id, populate_existing=True)

    assert after.rolling_memory_through_turn_id == turns[3].id
    assert "Prompt 1" not in (after.rolling_memory or "")
    assert "Prompt 0" in (after.rolling_memory or "")
    assert "Prompt 2" in (after.rolling_memory or "")


@pytest.mark.asyncio
async def test_multi_turn_restore_rebuild_is_idempotent_and_not_double_billed(
    memory_env, monkeypatch
):
    provider = AccumulatingMemoryProvider()
    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: provider),
    )
    monkeypatch.setattr(chat_memory_module, "resolve_llm_cost", lambda *_a, **_k: 0.0)
    base = datetime(2027, 10, 1, tzinfo=UTC)
    async with memory_env.Session() as db:
        turns = [
            await _add_completed_turn(
                db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i)
            )
            for i in range(14)
        ]
        await db.commit()
        await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        await chat_service.delete_turn(db, memory_env.auth, memory_env.chat_id, turns[1].id)
        await chat_service.restore_turn(db, memory_env.auth, memory_env.chat_id, turns[1].id)
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)
        memory_before = chat.rolling_memory
        watermark_before = chat.rolling_memory_through_turn_id
        costs_before = len(
            (
                await db.execute(
                    select(CostRecord).where(
                        CostRecord.chat_id == memory_env.chat_id,
                        CostRecord.kind == UsageKind.CHAT_MEMORY,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert (
            await chat_memory_service.merge_expired_turns(
                db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
            )
            == 0
        )
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)
        costs_after = len(
            (
                await db.execute(
                    select(CostRecord).where(
                        CostRecord.chat_id == memory_env.chat_id,
                        CostRecord.kind == UsageKind.CHAT_MEMORY,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert chat.rolling_memory == memory_before
    assert chat.rolling_memory_through_turn_id == watermark_before == turns[3].id
    assert costs_after == costs_before
    assert (chat.rolling_memory or "").splitlines() == [f"Prompt {i}" for i in range(4)]


@pytest.mark.asyncio
async def test_restore_rebuild_failure_leaves_all_uncovered_history_raw(memory_env, monkeypatch):
    provider = AccumulatingMemoryProvider()
    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: provider),
    )
    base = datetime(2027, 11, 1, tzinfo=UTC)
    async with memory_env.Session() as db:
        turns = [
            await _add_completed_turn(
                db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i)
            )
            for i in range(14)
        ]
        await db.commit()
        await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        await chat_service.delete_turn(db, memory_env.auth, memory_env.chat_id, turns[1].id)
        provider.fail = True
        restored = await chat_service.restore_turn(
            db, memory_env.auth, memory_env.chat_id, turns[1].id
        )
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)
        context = await chat_memory_service.build_recent_conversation_context(
            db, memory_env.chat_id, turns[-1].id, turns[-1].created_at
        )

    assert restored.id == turns[1].id
    assert chat.rolling_memory is None
    assert chat.rolling_memory_through_turn_id is None
    assert all(f"Prompt {i}" in (context or "") for i in range(13))


@pytest.mark.asyncio
async def test_partial_restore_rebuild_resumes_at_first_uncovered_turn(memory_env, monkeypatch):
    provider = AccumulatingMemoryProvider()
    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: provider),
    )
    base = datetime(2027, 12, 1, tzinfo=UTC)
    async with memory_env.Session() as db:
        turns = [
            await _add_completed_turn(
                db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i)
            )
            for i in range(15)
        ]
        await db.commit()
        await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        await chat_service.delete_turn(db, memory_env.auth, memory_env.chat_id, turns[1].id)
        provider.remaining_successes = 2
        provider.merged_questions.clear()
        await chat_service.restore_turn(db, memory_env.auth, memory_env.chat_id, turns[1].id)
        partial = await db.get(Chat, memory_env.chat_id, populate_existing=True)
        assert partial.rolling_memory_through_turn_id == turns[1].id
        context = await chat_memory_service.build_recent_conversation_context(
            db, memory_env.chat_id, turns[-1].id, turns[-1].created_at
        )
        assert "Prompt 2" in (context or "")
        provider.remaining_successes = None
        calls_before_resume = list(provider.merged_questions)
        assert (
            await chat_memory_service.merge_expired_turns(
                db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
            )
            == 3
        )
        final = await db.get(Chat, memory_env.chat_id, populate_existing=True)

    assert calls_before_resume == ["Prompt 0", "Prompt 1"]
    assert provider.merged_questions[2:] == ["Prompt 2", "Prompt 3", "Prompt 4"]
    assert final.rolling_memory_through_turn_id == turns[4].id


@pytest.mark.asyncio
@pytest.mark.parametrize("block_size", [29_999, 30_000, 30_001, 30_002])
async def test_production_context_rounding_keeps_every_selected_id_raw(memory_env, block_size):
    base = datetime(2028, 1, 1, tzinfo=UTC)
    async with memory_env.Session() as db:
        first = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=1, created_at=base)
        user_prefix = "BOUNDARY_USER_"
        second = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=2, created_at=base + timedelta(minutes=1)
        )
        fixed_chars = len(format_turn_history_block("x", "Verdict 2", "Reason 2")) - 1
        user_chars = block_size - fixed_chars
        second.user_message = user_prefix + ("x" * (user_chars - len(user_prefix)))
        assert (
            len(format_turn_history_block(second.user_message, "Verdict 2", "Reason 2"))
            == block_size
        )
        current = Turn(
            chat_id=memory_env.chat_id,
            user_message="current",
            model_set_id="test-set",
            strategy=Strategy.SYNTHESIZE,
            verdict_model="gemini",
            status=TurnStatus.PENDING,
            created_at=base + timedelta(minutes=2),
        )
        db.add(current)
        await db.commit()
        entries = await chat_memory_service.load_recent_history_entries(
            db, memory_env.chat_id, current.id, current.created_at
        )
        context = await chat_memory_service.build_recent_conversation_context(
            db, memory_env.chat_id, current.id, current.created_at
        )

    assert [entry.turn_id for entry in entries] == [first.id, second.id]
    assert "Prompt 1" in (context or "")
    assert user_prefix in (context or "")
    assert "Verdict 2" in (context or "")


@pytest.mark.asyncio
async def test_failed_background_merge_later_resumes_and_removes_raw_overlap(
    memory_env, monkeypatch
):
    provider = AccumulatingMemoryProvider()
    provider.fail = True
    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: provider),
    )
    base = datetime(2028, 2, 1, tzinfo=UTC)
    async with memory_env.Session() as db:
        turns = [
            await _add_completed_turn(
                db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i)
            )
            for i in range(12)
        ]
        await db.commit()
        assert (
            await chat_memory_service.rebuild_memory_best_effort(
                db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
            )
            == 0
        )
        before = await chat_memory_service.build_recent_conversation_context(
            db, memory_env.chat_id, turns[-1].id, turns[-1].created_at
        )
        provider.fail = False
        assert (
            await chat_memory_service.merge_expired_turns(
                db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
            )
            == 2
        )
        after = await chat_memory_service.build_recent_conversation_context(
            db, memory_env.chat_id, turns[-1].id, turns[-1].created_at
        )
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)

    assert "User question:\nPrompt 0\n\n" in (before or "")
    assert "User question:\nPrompt 0\n\n" not in (after or "")
    assert chat.rolling_memory_through_turn_id == turns[1].id


@pytest.mark.asyncio
async def test_non_null_cas_conflict_applies_and_bills_only_one_new_candidate(
    memory_env, monkeypatch
):
    base = datetime(2028, 3, 1, tzinfo=UTC)
    provider = AccumulatingMemoryProvider()
    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: provider),
    )
    monkeypatch.setattr(chat_memory_module, "resolve_llm_cost", lambda *_a, **_k: 0.0)
    async with memory_env.Session() as db:
        turns = [
            await _add_completed_turn(
                db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i)
            )
            for i in range(12)
        ]
        await db.commit()
        assert (
            await chat_memory_service.merge_expired_turns(
                db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
            )
            == 2
        )
        await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=12, created_at=base + timedelta(minutes=12)
        )
        await db.commit()

    started = asyncio.Event()
    release = asyncio.Event()
    race_calls = 0

    class RacingNonNullProvider(AccumulatingMemoryProvider):
        async def complete(self, **kwargs):
            nonlocal race_calls
            race_calls += 1
            if race_calls == 1:
                started.set()
                await release.wait()
            return await super().complete(**kwargs)

    racing = RacingNonNullProvider()
    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _provider: racing),
    )

    async def merge():
        async with memory_env.Session() as db:
            return await chat_memory_service.merge_expired_turns(
                db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
            )

    first = asyncio.create_task(merge())
    await asyncio.wait_for(started.wait(), timeout=2)
    second_result = await merge()
    release.set()
    first_result = await asyncio.wait_for(first, timeout=2)
    async with memory_env.Session() as db:
        chat = await db.get(Chat, memory_env.chat_id)
        costs = (
            (
                await db.execute(
                    select(CostRecord).where(
                        CostRecord.chat_id == memory_env.chat_id,
                        CostRecord.kind == UsageKind.CHAT_MEMORY,
                    )
                )
            )
            .scalars()
            .all()
        )

    assert sorted([first_result, second_result]) == [0, 1]
    assert chat.rolling_memory_through_turn_id == turns[2].id
    assert len(costs) == 3
