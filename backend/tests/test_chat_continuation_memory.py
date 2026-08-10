"""Chat continuation memory: recent history + rolling older-chat memory."""

from __future__ import annotations

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
    OrgMembership,
    OrgRole,
    Organization,
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
    RECENT_HISTORY_MAX_CHARS,
    RECENT_HISTORY_MAX_TURNS,
    TURN_SEPARATOR,
    TRUNCATION_MARKER,
    TurnHistoryEntry,
    chat_memory_service,
    format_recent_conversation_context,
    format_turn_history_block,
    select_recent_history_under_budget,
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

    assert len(entries) == RECENT_HISTORY_MAX_TURNS
    assert [e.user_message for e in entries] == [f"Prompt {i}" for i in range(2, 12)]
    assert context is not None
    assert context.index("Prompt 2") < context.index("Prompt 11")
    assert "Prompt 12" not in context
    # Avoid substring false positives like "Prompt 1" inside "Prompt 10"/"Prompt 11".
    assert all(e.user_message != "Prompt 1" for e in entries)
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
    # Recent window for a hypothetical next turn excludes turn 1.
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
    assert [e.user_message for e in entries] == [f"Prompt {i}" for i in range(2, 12)]


@pytest.mark.asyncio
async def test_deleted_challenge_and_verdictless_turns_excluded(memory_env):
    base = datetime(2026, 4, 1, tzinfo=UTC)
    async with memory_env.Session() as db:
        keep = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=1, created_at=base
        )
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
        await _add_completed_turn(
            db,
            chat_id=memory_env.chat_id,
            index=4,
            created_at=base + timedelta(minutes=3),
            with_verdict=False,
            status=TurnStatus.FAILED,
        )
        current = await _add_completed_turn(
            db,
            chat_id=memory_env.chat_id,
            index=5,
            created_at=base + timedelta(minutes=4),
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

    assert [e.turn_id for e in entries] == [keep.id]
    assert context is not None
    assert "Prompt 1" in context
    assert "Prompt 2" not in context
    assert "Prompt 3" not in context
    assert "Prompt 4" not in context
    assert "SECRET_COUNCIL_ANSWER" not in (context or "")


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
    assert len(ctx_a) <= tiny
    # Newer content preferred.
    assert "-5" in ctx_a
    assert "-1" not in ctx_a


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
            await db.execute(
                select(CostRecord).where(
                    CostRecord.chat_id == memory_env.chat_id,
                    CostRecord.kind == UsageKind.CHAT_MEMORY,
                )
            )
        ).scalars().all()

    assert merged == 1
    assert merged_again == 0
    assert len(calls) == 1
    assert memory_1 == "Upload supports PDF only."
    assert chat.rolling_memory == memory_1
    assert chat.rolling_memory_through_turn_id == through_1
    assert chat.rolling_memory_updated_at is not None
    assert len(costs) == 1
    assert "SECRET_COUNCIL" not in calls[0]
    assert "Prompt 1" in calls[0]
    assert "Verdict 1" in calls[0]


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
        turn = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=1
        )
        await db.commit()
        reloaded = await db.get(Turn, turn.id)
        assert reloaded.status == TurnStatus.COMPLETED
        assert reloaded.error_message is None
        verdict = (
            await db.execute(select(Verdict).where(Verdict.turn_id == turn.id))
        ).scalar_one()
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
