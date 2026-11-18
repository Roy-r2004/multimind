"""Phase 2: protected critical-facts ledger.

Covers the required test matrix: survival across many exchanges, question-
aware retrieval, correction/supersession, deletion/restoration lifecycle
(including the resurrection and cancellation-tombstone cases), grounding
safeguards (hallucination/attribution/hedge rejection, unconfirmed-
suggestion rejection), idempotent/bounded backfill recovery, CAS/transaction
atomicity with the rolling-memory watermark, context-budget truncation
without data loss, malformed-JSON handling, and single-AI/Council
compatibility.

All provider calls use deterministic fakes — no real network calls, no real
paid LLM usage.
"""

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
    ChatCriticalFact,
    ModelAnswer,
    ModelAnswerStatus,
    Organization,
    OrgMembership,
    OrgRole,
    Strategy,
    Turn,
    TurnStatus,
    User,
    Verdict,
)
from app.services import chat_memory_service as chat_memory_module
from app.services.chat_memory_service import (
    FactCandidateSchema,
    _validate_fact_candidate,
    chat_memory_service,
    memory_update_response_format,
    parse_structured_memory_response,
    render_protected_facts_block,
    select_relevant_facts,
)


@pytest.fixture
async def memory_env(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'critical_facts.db'}", poolclass=NullPool
    )
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with Session() as db:
        org = Organization(name="Facts Org", slug="facts-org")
        user = User(email="facts@example.com", hashed_password="x", full_name="Facts User")
        db.add_all([org, user])
        await db.flush()
        db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.OWNER))
        chat = Chat(org_id=org.id, created_by=user.id, title="Facts chat")
        db.add(chat)
        await db.commit()
        auth = AuthContext(user=user, org_id=org.id, role=OrgRole.OWNER)
        chat_id = chat.id
        org_id = org.id

    try:
        yield SimpleNamespace(Session=Session, auth=auth, chat_id=chat_id, org_id=org_id)
    finally:
        await engine.dispose()


async def _add_completed_turn(
    db: AsyncSession,
    *,
    chat_id: str,
    index: int,
    created_at: datetime | None = None,
    with_verdict: bool = True,
) -> Turn:
    turn = Turn(
        chat_id=chat_id,
        user_message=f"Prompt {index}",
        model_set_id="test-set",
        strategy=Strategy.SYNTHESIZE,
        verdict_model="gemini",
        status=TurnStatus.COMPLETED,
    )
    if created_at is not None:
        turn.created_at = created_at
    db.add(turn)
    await db.flush()
    db.add(
        ModelAnswer(
            turn_id=turn.id,
            model_id="gpt-4.1",
            text=f"Answer {index}",
            status=ModelAnswerStatus.COMPLETED,
        )
    )
    if with_verdict:
        db.add(
            Verdict(
                turn_id=turn.id,
                model_id="gemini",
                strategy=Strategy.SYNTHESIZE,
                text=f"Answer {index}",
                reason="",
            )
        )
    await db.flush()
    return turn


def _structured(
    memory: str,
    candidates: list[dict] | None = None,
) -> SimpleNamespace:
    import json

    payload = {"updated_memory": memory, "fact_candidates": candidates or []}
    return SimpleNamespace(
        text=json.dumps(payload), tokens_input=10, tokens_output=10, cost_usd=0.0
    )


def _patch_provider(monkeypatch, provider) -> None:
    monkeypatch.setattr(
        chat_memory_module,
        "get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _p: provider),
    )


# ---------------------------------------------------------------------------
# 1. Grounding safeguards — pure, no DB
# ---------------------------------------------------------------------------


def test_hallucinated_source_span_is_rejected():
    raw = {
        "fact_slot_id": "budget",
        "category": "fact",
        "content": "Budget is $50,000",
        "speaker": "user",
        "source_span": "the budget is fifty thousand dollars",  # not verbatim
        "grounding": "explicit_user_statement",
    }
    result = _validate_fact_candidate(raw, user_message="The budget is $50,000.", assistant_text="")
    assert result is None


def test_verbatim_source_span_is_accepted():
    raw = {
        "fact_slot_id": "budget",
        "category": "fact",
        "content": "Budget is $50,000",
        "speaker": "user",
        "source_span": "The budget is $50,000",
        "grounding": "explicit_user_statement",
    }
    result = _validate_fact_candidate(raw, user_message="The budget is $50,000.", assistant_text="")
    assert result is not None
    assert result["content"] == "Budget is $50,000"


def test_incorrect_attribution_is_rejected():
    """Span found in assistant text but tagged speaker=user must be rejected."""
    raw = {
        "fact_slot_id": "budget",
        "category": "fact",
        "content": "Budget is $50,000",
        "speaker": "user",
        "source_span": "The budget is $50,000",
        "grounding": "explicit_user_statement",
    }
    result = _validate_fact_candidate(
        raw, user_message="unrelated", assistant_text="The budget is $50,000"
    )
    assert result is None  # not found in the claimed speaker's (user's) text


def test_unconfirmed_assistant_suggestion_cannot_become_a_decision():
    """Core safeguard: assistant suggestions cannot become user-confirmed facts."""
    raw = {
        "fact_slot_id": "hosting",
        "category": "decision",
        "content": "Use AWS for hosting",
        "speaker": "assistant",
        "source_span": "I suggest using AWS for hosting",
        "grounding": "explicit_confirmed_decision",  # self-reported, but must still be rejected
    }
    result = _validate_fact_candidate(
        raw, user_message="what should we use?", assistant_text="I suggest using AWS for hosting"
    )
    assert result is None  # decision/requirement/correction require speaker=user


def test_assistant_category_fact_is_allowed_non_decision():
    """Plain 'fact'/'preference'/'open_task' categories don't require speaker=user —
    only decision/requirement/correction do."""
    raw = {
        "fact_slot_id": "note",
        "category": "fact",
        "content": "The API rate limit is 100 req/s",
        "speaker": "assistant",
        "source_span": "the API rate limit is 100 req/s",
        "grounding": "explicit_user_statement",
    }
    result = _validate_fact_candidate(
        raw, user_message="", assistant_text="the API rate limit is 100 req/s"
    )
    assert result is not None


def test_hedge_language_is_rejected():
    raw = {
        "fact_slot_id": "limit",
        "category": "requirement",
        "content": "maybe 50MB",
        "speaker": "user",
        "source_span": "maybe we should consider 50MB",
        "grounding": "explicit_confirmed_decision",
    }
    result = _validate_fact_candidate(
        raw, user_message="maybe we should consider 50MB", assistant_text=""
    )
    assert result is None


def test_confirmed_decision_sourced_from_confirming_user_turn_is_accepted():
    """The user confirming an assistant suggestion — source_span is the
    user's own confirming text, never the assistant's suggestion text."""
    raw = {
        "fact_slot_id": "hosting",
        "category": "decision",
        "content": "Use AWS for hosting",
        "speaker": "user",
        "source_span": "Yes, let's use AWS",
        "grounding": "explicit_confirmed_decision",
    }
    result = _validate_fact_candidate(
        raw, user_message="Yes, let's use AWS", assistant_text="I suggest AWS"
    )
    assert result is not None


# ---------------------------------------------------------------------------
# 2. Parsing — lenient, backward compatible
# ---------------------------------------------------------------------------


def test_parse_structured_response_extracts_memory_and_candidates():
    import json

    text = json.dumps({"updated_memory": "fact A", "fact_candidates": [{"x": 1}]})
    memory, candidates = parse_structured_memory_response(text)
    assert memory == "fact A"
    assert candidates == [{"x": 1}]


def test_parse_plain_text_response_is_backward_compatible():
    """Every pre-existing plain-text fake-provider response in the test
    suite must keep working unchanged — this is the whole point of the
    lenient-parse design."""
    memory, candidates = parse_structured_memory_response("Upload supports PDF only.")
    assert memory == "Upload supports PDF only."
    assert candidates == []


def test_parse_malformed_json_falls_back_to_plain_text():
    text = '{"updated_memory": "oops, not closed'
    memory, candidates = parse_structured_memory_response(text)
    assert memory == text  # treated as plain memory text, not an error
    assert candidates == []


def test_response_format_schema_is_well_formed():
    fmt = memory_update_response_format()
    assert fmt["type"] == "json_schema"
    schema = fmt["json_schema"]["schema"]
    assert "updated_memory" in schema["properties"]
    assert "fact_candidates" in schema["properties"]


# ---------------------------------------------------------------------------
# 3. Deterministic, question-aware selection — no embeddings
# ---------------------------------------------------------------------------


def _fact(slot, category, content, created_at, source_turn=None, id_suffix="") -> ChatCriticalFact:
    f = ChatCriticalFact(
        id=f"fact-{slot}{id_suffix}",
        chat_id="chat",
        org_id="org",
        fact_slot_id=slot,
        category=category,
        content=content,
        speaker="user",
        source_turn_id=None,
        source_span=content,
        grounding="explicit_user_statement",
        status="normal",
    )
    f.created_at = created_at
    f.source_turn = source_turn
    return f


def test_selection_finds_old_relevant_fact_among_many_unrelated():
    base = datetime(2030, 1, 1, tzinfo=UTC)
    old_relevant = _fact(
        "budget_cap", "fact", "The maximum budget approved is $42,750", base, id_suffix="old"
    )
    unrelated = [
        _fact(f"topic{i}", "fact", f"unrelated note {i}", base + timedelta(minutes=i))
        for i in range(50)
    ]
    facts = [old_relevant, *unrelated]
    selected = select_relevant_facts(
        facts, user_message="What is the maximum budget approved?", max_tokens=100
    )
    assert any(f.fact_slot_id == "budget_cap" for f in selected)
    # It should rank ahead of at least some unrelated, more-recent facts.
    assert selected[0].fact_slot_id == "budget_cap"


def test_selection_is_deterministic_across_repeated_calls():
    base = datetime(2030, 1, 1, tzinfo=UTC)
    facts = [_fact(f"topic{i}", "fact", f"note {i}", base + timedelta(minutes=i)) for i in range(20)]
    first = [f.id for f in select_relevant_facts(facts, user_message="note", max_tokens=200)]
    second = [f.id for f in select_relevant_facts(facts, user_message="note", max_tokens=200)]
    assert first == second


def test_selection_falls_back_to_recency_when_no_overlap():
    base = datetime(2030, 1, 1, tzinfo=UTC)
    facts = [_fact(f"topic{i}", "fact", f"content {i}", base + timedelta(minutes=i)) for i in range(5)]
    selected = select_relevant_facts(facts, user_message="completely unrelated chit chat", max_tokens=1000)
    # No overlap anywhere -> pure recency order, most recent first.
    assert [f.fact_slot_id for f in selected] == ["topic4", "topic3", "topic2", "topic1", "topic0"]


def test_open_task_is_always_standing_regardless_of_topic():
    base = datetime(2030, 1, 1, tzinfo=UTC)
    task = _fact("hosting_decision", "open_task", "hosting provider still undecided", base)
    unrelated = _fact("weather", "fact", "it is sunny today", base + timedelta(minutes=5))
    selected = select_relevant_facts(
        [unrelated, task], user_message="totally unrelated question", max_tokens=1000
    )
    assert selected[0].fact_slot_id == "hosting_decision"  # standing category wins ties


def test_budget_overflow_truncates_surfaced_facts_but_render_never_deletes_storage():
    base = datetime(2030, 1, 1, tzinfo=UTC)
    facts = [_fact(f"topic{i}", "fact", "x" * 200, base + timedelta(minutes=i)) for i in range(20)]
    selected = select_relevant_facts(facts, user_message="x", max_tokens=50)
    assert 0 < len(selected) < len(facts)  # truncated for context...
    block = render_protected_facts_block(selected)
    assert block  # ...but rendering what *was* selected still works
    # Storage itself is untouched by selection — simulated by the caller
    # simply re-querying `facts` (the full list) again, which still has 20.
    assert len(facts) == 20


def test_cancellation_renders_as_no_current_value_not_a_dropped_fact():
    base = datetime(2030, 1, 1, tzinfo=UTC)
    tombstone = _fact("upload_limit", "cancellation", "ignored content", base)
    block = render_protected_facts_block([tombstone])
    assert "no current value" in block.lower()


# ---------------------------------------------------------------------------
# 4. current_facts_for_chat — the read-time "current per slot" computation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_fact_is_latest_by_source_turn(memory_env):
    base = datetime(2030, 2, 1, tzinfo=UTC)
    async with memory_env.Session() as db:
        t1 = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=1, created_at=base)
        t2 = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=2, created_at=base + timedelta(minutes=90)
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="upload_limit",
                category="requirement", content="25MB", speaker="user", source_turn_id=t1.id,
                source_span="25MB", grounding="explicit_user_statement",
            )
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="upload_limit",
                category="requirement", content="100MB", speaker="user", source_turn_id=t2.id,
                source_span="100MB", grounding="explicit_confirmed_decision",
            )
        )
        await db.commit()

        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert len(current) == 1
    assert current[0].content == "100MB"  # latest (requirement changed twice -> latest wins)


@pytest.mark.asyncio
async def test_deleting_newer_correction_resurrects_older_value(memory_env):
    """Turn 5: 25MB. Turn 90: 100MB. Turn 90 deleted -> 25MB becomes current
    again — with NO retraction write on the 25MB row at all."""
    base = datetime(2030, 2, 2, tzinfo=UTC)
    async with memory_env.Session() as db:
        t_old = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=5, created_at=base)
        t_new = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=90, created_at=base + timedelta(minutes=90)
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="upload_limit",
                category="requirement", content="25MB", speaker="user", source_turn_id=t_old.id,
                source_span="25MB", grounding="explicit_user_statement",
            )
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="upload_limit",
                category="requirement", content="100MB", speaker="user", source_turn_id=t_new.id,
                source_span="100MB", grounding="explicit_confirmed_decision",
            )
        )
        await db.commit()

        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        assert current[0].content == "100MB"

        # Turn 150: delete turn 90.
        t_new.deleted_at = datetime.now(UTC)
        await db.commit()

        current_after = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert len(current_after) == 1
    assert current_after[0].content == "25MB"  # resurrected with zero mutation to its own row


@pytest.mark.asyncio
async def test_explicit_cancellation_blocks_reversion_to_older_value(memory_env):
    """Unlike deleting a correction, an explicit cancellation must NOT let
    the older value become current — there should be no valid requirement."""
    base = datetime(2030, 2, 3, tzinfo=UTC)
    async with memory_env.Session() as db:
        t_old = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=5, created_at=base)
        t_cancel = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=150, created_at=base + timedelta(minutes=150)
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="upload_limit",
                category="requirement", content="25MB", speaker="user", source_turn_id=t_old.id,
                source_span="25MB", grounding="explicit_user_statement",
            )
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="upload_limit",
                category="cancellation", content="no current limit", speaker="user",
                source_turn_id=t_cancel.id, source_span="no limit", grounding="explicit_user_statement",
            )
        )
        await db.commit()

        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        assert current[0].category == "cancellation"

        # Now delete the cancelling turn itself -> old value correctly reappears.
        t_cancel.deleted_at = datetime.now(UTC)
        await db.commit()
        current_after = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert current_after[0].content == "25MB"


@pytest.mark.asyncio
async def test_explicit_retraction_also_falls_back_correctly(memory_env):
    base = datetime(2030, 2, 4, tzinfo=UTC)
    async with memory_env.Session() as db:
        t1 = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=1, created_at=base)
        t2 = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=2, created_at=base + timedelta(minutes=10)
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="pref",
                category="preference", content="likes dark mode", speaker="user", source_turn_id=t1.id,
                source_span="dark mode", grounding="explicit_user_statement",
            )
        )
        f2 = ChatCriticalFact(
            chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="pref",
            category="preference", content="likes light mode", speaker="user", source_turn_id=t2.id,
            source_span="light mode", grounding="explicit_user_statement",
        )
        db.add(f2)
        await db.commit()

        f2.status = "retracted"
        await db.commit()
        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert current[0].content == "likes dark mode"


@pytest.mark.asyncio
async def test_chat_and_org_isolation(memory_env):
    base = datetime(2030, 2, 5, tzinfo=UTC)
    async with memory_env.Session() as db:
        t1 = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=1, created_at=base)
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="shared_name",
                category="fact", content="chat A's fact", speaker="user", source_turn_id=t1.id,
                source_span="fact", grounding="explicit_user_statement",
            )
        )
        await db.commit()

        other_chat = Chat(org_id=memory_env.org_id, created_by=memory_env.auth.user.id, title="Other chat")
        db.add(other_chat)
        await db.commit()
        current_other = await chat_memory_service.current_facts_for_chat(
            db, chat_id=other_chat.id, org_id=memory_env.org_id
        )
    assert current_other == []  # same org, different chat -> no leakage


# ---------------------------------------------------------------------------
# 5. Idempotent, bounded backfill recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_fact_insert_is_idempotent(memory_env):
    base = datetime(2030, 2, 6, tzinfo=UTC)
    async with memory_env.Session() as db:
        turn = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=1, created_at=base)
        await db.commit()
        candidate = {
            "fact_slot_id": "budget", "category": "fact", "content": "x", "speaker": "user",
            "source_span": "x", "grounding": "explicit_user_statement",
        }
        await chat_memory_service._persist_fact_candidates(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id, turn_id=turn.id,
            candidates=[candidate],
        )
        await db.commit()
        # Retry the exact same candidate for the exact same turn — must not duplicate.
        await chat_memory_service._persist_fact_candidates(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id, turn_id=turn.id,
            candidates=[candidate],
        )
        await db.commit()
        rows = (
            await db.execute(select(ChatCriticalFact).where(ChatCriticalFact.chat_id == memory_env.chat_id))
        ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_backfill_recovers_a_failed_extraction_without_touching_memory(memory_env, monkeypatch):
    base = datetime(2030, 2, 7, tzinfo=UTC)

    class FactOnlyProvider:
        def __init__(self):
            self.calls = 0

        async def complete(self, **_kwargs):
            self.calls += 1
            import json

            return SimpleNamespace(
                text=json.dumps(
                    {
                        "updated_memory": "irrelevant, discarded by backfill",
                        "fact_candidates": [
                            {
                                "fact_slot_id": "budget", "category": "fact",
                                "content": "Budget is $9,000", "speaker": "user",
                                "source_span": "Prompt 1", "grounding": "explicit_user_statement",
                            }
                        ],
                    }
                ),
                tokens_input=5, tokens_output=5, cost_usd=0.0,
            )

    provider = FactOnlyProvider()
    _patch_provider(monkeypatch, provider)

    async with memory_env.Session() as db:
        turn = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=1, created_at=base)
        chat = await db.get(Chat, memory_env.chat_id)
        chat.rolling_memory = "existing unrelated memory"
        turn.facts_extraction_status = "failed"
        await db.commit()

        ok = await chat_memory_service.backfill_fact_extraction_for_turn(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id, turn_id=turn.id
        )
        await db.commit()
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)
        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert ok is True
    assert provider.calls == 1
    assert chat.rolling_memory == "existing unrelated memory"  # untouched by backfill
    assert len(current) == 1
    assert current[0].content == "Budget is $9,000"


@pytest.mark.asyncio
async def test_backfill_is_bounded_and_marks_failed_on_exhaustion(memory_env, monkeypatch):
    base = datetime(2030, 2, 8, tzinfo=UTC)
    call_count = 0

    class AlwaysFailProvider:
        async def complete(self, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("provider outage")

    _patch_provider(monkeypatch, AlwaysFailProvider())
    async with memory_env.Session() as db:
        turn = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=1, created_at=base)
        turn.facts_extraction_status = "failed"
        await db.commit()

        ok = await chat_memory_service.backfill_fact_extraction_for_turn(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id, turn_id=turn.id
        )
        await db.commit()
        reloaded = await db.get(Turn, turn.id, populate_existing=True)
    assert ok is False
    assert reloaded.facts_extraction_status == "failed"
    assert call_count == chat_memory_module._FACT_EXTRACTION_MAX_ATTEMPTS  # bounded, not infinite


@pytest.mark.asyncio
async def test_recovery_sweep_finds_and_reenqueues_failed_extractions(memory_env, monkeypatch):
    base = datetime(2030, 2, 9, tzinfo=UTC)
    async with memory_env.Session() as db:
        turn = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=1, created_at=base)
        turn.facts_extraction_status = "failed"
        await db.commit()

    enqueued = []

    async def fake_enqueue(*, chat_id, org_id, turn_id):
        enqueued.append((chat_id, org_id, turn_id))

    monkeypatch.setattr(chat_memory_module, "enqueue_fact_extraction_backfill", fake_enqueue)
    swept = await chat_memory_module.recover_failed_fact_extractions(
        {"session_factory": memory_env.Session}
    )
    assert swept == 1
    assert enqueued == [(memory_env.chat_id, memory_env.org_id, (await _first_turn_id(memory_env)))]


async def _first_turn_id(memory_env) -> str:
    async with memory_env.Session() as db:
        row = (await db.execute(select(Turn.id).where(Turn.chat_id == memory_env.chat_id))).scalar_one()
    return row


# ---------------------------------------------------------------------------
# 6. End-to-end through merge_expired_turns: atomicity, CAS, single-AI/Council
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_merge_persists_memory_and_fact_together(memory_env, monkeypatch):
    base = datetime(2030, 2, 10, tzinfo=UTC)

    class StructuredProvider:
        async def complete(self, **_kwargs):
            return _structured(
                "compact summary",
                [
                    {
                        "fact_slot_id": "budget", "category": "fact", "content": "Budget is $42,750",
                        "speaker": "user", "source_span": "Prompt 0", "grounding": "explicit_user_statement",
                    }
                ],
            )

    _patch_provider(monkeypatch, StructuredProvider())
    async with memory_env.Session() as db:
        for i in range(11):
            await _add_completed_turn(db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i))
        await db.commit()
        merged = await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)
        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert merged == 1
    assert chat.rolling_memory == "compact summary"
    assert len(current) == 1
    assert current[0].content == "Budget is $42,750"


@pytest.mark.asyncio
async def test_malformed_fact_candidates_does_not_block_successful_memory_merge(memory_env, monkeypatch):
    """Failed extraction with successful rolling-memory merge: the memory
    update must still succeed and its watermark still advance."""
    import json

    class MalformedFactsProvider:
        async def complete(self, **_kwargs):
            payload = {"updated_memory": "compact summary", "fact_candidates": "not-a-list"}
            return SimpleNamespace(text=json.dumps(payload), tokens_input=5, tokens_output=5, cost_usd=0.0)

    base = datetime(2030, 2, 11, tzinfo=UTC)
    _patch_provider(monkeypatch, MalformedFactsProvider())
    async with memory_env.Session() as db:
        for i in range(11):
            await _add_completed_turn(db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i))
        await db.commit()
        merged = await chat_memory_service.merge_expired_turns(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)
    assert merged == 1
    assert chat.rolling_memory == "compact summary"
    assert chat.rolling_memory_through_turn_id is not None


@pytest.mark.asyncio
async def test_cas_conflict_rolls_back_fact_insert_together_with_memory(memory_env, monkeypatch):
    """A CAS conflict must roll back both the rolling-memory write AND any
    fact insert attempted in that same losing transaction — atomicity, not
    two independently-committed halves."""
    import asyncio

    started = asyncio.Event()
    release = asyncio.Event()

    class SlowStructuredProvider:
        async def complete(self, **_kwargs):
            started.set()
            await release.wait()
            return _structured(
                "stale memory",
                [
                    {
                        "fact_slot_id": "race", "category": "fact", "content": "stale fact",
                        "speaker": "user", "source_span": "Prompt 0", "grounding": "explicit_user_statement",
                    }
                ],
            )

    base = datetime(2030, 2, 12, tzinfo=UTC)
    _patch_provider(monkeypatch, SlowStructuredProvider())
    async with memory_env.Session() as db:
        for i in range(11):
            await _add_completed_turn(db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i))
        await db.commit()
        eligible = await chat_memory_service.list_eligible_turns_oldest_first(db, memory_env.chat_id)
        turn, assistant_result = eligible[0]

    async def run_slow_merge():
        async with memory_env.Session() as inner_db:
            inner_chat = await inner_db.get(Chat, memory_env.chat_id)
            return await chat_memory_service._merge_one_turn(
                inner_db, chat=inner_chat, turn=turn, assistant_result=assistant_result,
                org_id=memory_env.org_id, project_id=None,
            )

    task = asyncio.create_task(run_slow_merge())
    await asyncio.wait_for(started.wait(), timeout=2)
    async with memory_env.Session() as db:
        await chat_memory_service.invalidate_memory(db, chat_id=memory_env.chat_id)
        await db.commit()
    release.set()
    result = await asyncio.wait_for(task, timeout=5)
    assert result is None  # stale candidate rejected

    async with memory_env.Session() as db:
        chat = await db.get(Chat, memory_env.chat_id, populate_existing=True)
        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert chat.rolling_memory is None
    assert current == []  # the fact from the losing attempt was never persisted


@pytest.mark.asyncio
async def test_single_ai_and_council_both_extract_facts(memory_env, monkeypatch):
    class StructuredProvider:
        async def complete(self, **_kwargs):
            return _structured(
                "summary",
                [
                    {
                        "fact_slot_id": "mode_check", "category": "fact", "content": "works",
                        "speaker": "user", "source_span": "Prompt 0", "grounding": "explicit_user_statement",
                    }
                ],
            )

    base = datetime(2030, 2, 13, tzinfo=UTC)
    for with_verdict in (False, True):  # single-AI vs Council
        _patch_provider(monkeypatch, StructuredProvider())
        async with memory_env.Session() as db:
            org2 = Organization(name=f"org-{with_verdict}", slug=f"org-{with_verdict}")
            user2 = User(email=f"u{with_verdict}@example.com", hashed_password="x", full_name="U")
            db.add_all([org2, user2])
            await db.flush()
            chat2 = Chat(org_id=org2.id, created_by=user2.id, title="c")
            db.add(chat2)
            await db.commit()
            for i in range(11):
                await _add_completed_turn(
                    db, chat_id=chat2.id, index=i, created_at=base + timedelta(minutes=i),
                    with_verdict=with_verdict,
                )
            await db.commit()
            merged = await chat_memory_service.merge_expired_turns(db, chat_id=chat2.id, org_id=org2.id)
            current = await chat_memory_service.current_facts_for_chat(db, chat_id=chat2.id, org_id=org2.id)
        assert merged == 1
        assert len(current) == 1


# ---------------------------------------------------------------------------
# 7. 200-exchange survival (deterministic fake, mechanics only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_critical_fact_survives_200_exchanges(memory_env, monkeypatch):
    """Unlike the measured 100% real-model loss rate for the free-text
    rolling summary at this scale, the protected fact must still be the
    current fact for its slot after 190 merges — because it is never
    re-summarized, only ever read verbatim from its own row."""

    class StructuredProvider:
        def __init__(self):
            self.call = 0

        async def complete(self, **_kwargs):
            self.call += 1
            candidates = []
            if self.call == 1:
                candidates = [
                    {
                        "fact_slot_id": "budget_cap", "category": "fact",
                        "content": "Budget cap is $42,750", "speaker": "user",
                        "source_span": "Prompt 0", "grounding": "explicit_user_statement",
                    }
                ]
            return _structured(f"compact summary #{self.call}", candidates)

    base = datetime(2030, 2, 14, tzinfo=UTC)
    _patch_provider(monkeypatch, StructuredProvider())
    async with memory_env.Session() as db:
        for i in range(200):
            await _add_completed_turn(db, chat_id=memory_env.chat_id, index=i, created_at=base + timedelta(minutes=i))
        await db.commit()
        merged_total = 0
        for _ in range(50):
            n = await chat_memory_service.merge_expired_turns(db, chat_id=memory_env.chat_id, org_id=memory_env.org_id)
            merged_total += n
            if n == 0:
                break
        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert merged_total == 190
    assert len(current) == 1


# ---------------------------------------------------------------------------
# 8. Timestamp normalization — naive/aware datetime regression
#
# Root cause: ChatCriticalFact.created_at is populated via a server default
# (func.now()), which SQLite always returns naive; Turn.created_at is often
# set explicitly by the app/tests as an aware (tzinfo=UTC) value and is never
# refetched while the object stays in the session (expire_on_commit=False).
# `current_facts_for_chat`'s ordering tuple and `select_relevant_facts`'s
# sort key used to compare these raw values directly, which raises
# ``TypeError: can't compare offset-naive and offset-aware datetimes`` the
# moment a tie on the first element forces a fallback comparison, or the two
# compared values otherwise differ in awareness. Both sites now normalize
# through ``chat_memory_module._as_utc`` before comparing.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_facts_handles_two_naive_timestamps(memory_env):
    base = datetime(2030, 3, 1)  # naive
    async with memory_env.Session() as db:
        t1 = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=1, created_at=base)
        t2 = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=2, created_at=base + timedelta(minutes=10)
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="slot",
                category="fact", content="old", speaker="user", source_turn_id=t1.id,
                source_span="old", grounding="explicit_user_statement",
            )
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="slot",
                category="fact", content="new", speaker="user", source_turn_id=t2.id,
                source_span="new", grounding="explicit_user_statement",
            )
        )
        await db.commit()
        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert len(current) == 1
    assert current[0].content == "new"


@pytest.mark.asyncio
async def test_current_facts_handles_two_aware_timestamps(memory_env):
    base = datetime(2030, 3, 2, tzinfo=UTC)
    async with memory_env.Session() as db:
        t1 = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=1, created_at=base)
        t2 = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=2, created_at=base + timedelta(minutes=10)
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="slot",
                category="fact", content="old", speaker="user", source_turn_id=t1.id,
                source_span="old", grounding="explicit_user_statement",
            )
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="slot",
                category="fact", content="new", speaker="user", source_turn_id=t2.id,
                source_span="new", grounding="explicit_user_statement",
            )
        )
        await db.commit()
        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert len(current) == 1
    assert current[0].content == "new"


@pytest.mark.asyncio
async def test_current_facts_handles_mixed_naive_and_aware_timestamps(memory_env):
    """The exact mismatch that crashed the real evaluation: one source turn's
    ``created_at`` is naive, the other aware — the ordering tuple must still
    resolve correctly instead of raising ``TypeError``."""
    async with memory_env.Session() as db:
        t_old = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=1, created_at=datetime(2030, 3, 3, 8, 0, 0)  # naive
        )
        t_new = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=2,
            created_at=datetime(2030, 3, 3, 9, 0, 0, tzinfo=UTC),  # aware
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="slot",
                category="fact", content="old", speaker="user", source_turn_id=t_old.id,
                source_span="old", grounding="explicit_user_statement",
            )
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="slot",
                category="fact", content="new", speaker="user", source_turn_id=t_new.id,
                source_span="new", grounding="explicit_user_statement",
            )
        )
        await db.commit()
        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert len(current) == 1
    assert current[0].content == "new"  # 09:00 (aware) is later than 08:00 (naive, treated as UTC)


@pytest.mark.asyncio
async def test_current_facts_same_slot_different_tz_representation(memory_env):
    """Same instant expressed in two different UTC offsets must normalize to
    the same point in time rather than being misordered."""
    async with memory_env.Session() as db:
        t_a = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=1,
            created_at=datetime(2030, 3, 4, 10, 0, 0, tzinfo=UTC),
        )
        # Same instant as t_a, expressed as +02:00 — 10:00+02:00 == 08:00Z.
        from datetime import timezone as _tz

        t_b = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=2,
            created_at=datetime(2030, 3, 4, 10, 0, 0, tzinfo=_tz(timedelta(hours=2))),
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="slot",
                category="fact", content="from_a_utc", speaker="user", source_turn_id=t_a.id,
                source_span="a", grounding="explicit_user_statement",
            )
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="slot",
                category="fact", content="from_b_plus2", speaker="user", source_turn_id=t_b.id,
                source_span="b", grounding="explicit_user_statement",
            )
        )
        await db.commit()
        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    # t_a (10:00 UTC) is strictly later than t_b's true instant (08:00 UTC).
    assert len(current) == 1
    assert current[0].content == "from_a_utc"


@pytest.mark.asyncio
async def test_current_facts_identical_timestamps_tie_break_by_id(memory_env):
    """When source-turn timestamps tie exactly, ordering must fall through to
    ``fact.created_at`` and then ``fact.id`` deterministically, with no
    crash even when those fallback values are naive."""
    tie = datetime(2030, 3, 5, 12, 0, 0, tzinfo=UTC)
    async with memory_env.Session() as db:
        t1 = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=1, created_at=tie)
        t2 = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=2, created_at=tie)
        f_a = ChatCriticalFact(
            id="fact-tie-a", chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="slot",
            category="fact", content="a", speaker="user", source_turn_id=t1.id,
            source_span="a", grounding="explicit_user_statement",
        )
        f_b = ChatCriticalFact(
            id="fact-tie-b", chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="slot",
            category="fact", content="b", speaker="user", source_turn_id=t2.id,
            source_span="b", grounding="explicit_user_statement",
        )
        db.add_all([f_a, f_b])
        await db.commit()
        # Force the fallback comparison to also tie, pushing the decision to
        # `fact.id` — and make one of those fallback values naive.
        f_a.created_at = datetime(2030, 3, 5, 12, 0, 0)  # naive
        f_b.created_at = datetime(2030, 3, 5, 12, 0, 0, tzinfo=UTC)  # aware, same instant
        await db.commit()
        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        current_run_twice = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert len(current) == 1
    assert current[0].id == "fact-tie-b"  # "fact-tie-b" > "fact-tie-a" lexicographically
    assert current_run_twice[0].id == current[0].id  # deterministic across repeated calls


@pytest.mark.asyncio
async def test_cancellation_ordering_unaffected_by_naive_timestamp(memory_env):
    """Regression guard: cancellation-blocks-reversion behavior (see
    ``test_explicit_cancellation_blocks_reversion_to_older_value`` above)
    must be unchanged when the cancelling turn's timestamp is naive."""
    base = datetime(2030, 3, 6, tzinfo=UTC)
    async with memory_env.Session() as db:
        t_old = await _add_completed_turn(db, chat_id=memory_env.chat_id, index=5, created_at=base)
        t_cancel = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=150,
            created_at=datetime(2030, 3, 6, 2, 30, 0),  # naive, but later than base
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="upload_limit",
                category="requirement", content="25MB", speaker="user", source_turn_id=t_old.id,
                source_span="25MB", grounding="explicit_user_statement",
            )
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="upload_limit",
                category="cancellation", content="no current limit", speaker="user",
                source_turn_id=t_cancel.id, source_span="no limit", grounding="explicit_user_statement",
            )
        )
        await db.commit()
        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        assert current[0].category == "cancellation"

        t_cancel.deleted_at = datetime.now(UTC)
        await db.commit()
        current_after = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert current_after[0].content == "25MB"


@pytest.mark.asyncio
async def test_deletion_and_restoration_unaffected_by_naive_timestamp(memory_env):
    """Regression guard: delete/restore resurrection behavior (see
    ``test_deleting_newer_correction_resurrects_older_value`` above) must be
    unchanged when one of the two source turns has a naive timestamp."""
    async with memory_env.Session() as db:
        t_old = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=5, created_at=datetime(2030, 3, 7, 1, 0, 0)  # naive
        )
        t_new = await _add_completed_turn(
            db, chat_id=memory_env.chat_id, index=90,
            created_at=datetime(2030, 3, 7, 3, 0, 0, tzinfo=UTC),  # aware
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="upload_limit",
                category="requirement", content="25MB", speaker="user", source_turn_id=t_old.id,
                source_span="25MB", grounding="explicit_user_statement",
            )
        )
        db.add(
            ChatCriticalFact(
                chat_id=memory_env.chat_id, org_id=memory_env.org_id, fact_slot_id="upload_limit",
                category="requirement", content="100MB", speaker="user", source_turn_id=t_new.id,
                source_span="100MB", grounding="explicit_confirmed_decision",
            )
        )
        await db.commit()

        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
        assert current[0].content == "100MB"

        t_new.deleted_at = datetime.now(UTC)
        await db.commit()
        current_after = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert len(current_after) == 1
    assert current_after[0].content == "25MB"


def test_select_relevant_facts_sorts_naive_and_aware_timestamps_together():
    """``select_relevant_facts``'s recency tie-breaker must not raise or
    misorder when facts mix naive and aware ``created_at``/source-turn
    timestamps (second confirmed site for the same class of bug)."""
    naive_older = _fact("a", "fact", "older naive", datetime(2030, 3, 8, 8, 0, 0))
    aware_newer = _fact("b", "fact", "newer aware", datetime(2030, 3, 8, 9, 0, 0, tzinfo=UTC))
    selected = select_relevant_facts(
        [naive_older, aware_newer], user_message="irrelevant chit chat", max_tokens=1000
    )
    assert [f.fact_slot_id for f in selected] == ["b", "a"]  # most recent first


@pytest.mark.asyncio
async def test_single_ai_and_council_both_tolerate_naive_aware_mix(memory_env, monkeypatch):
    """Same as ``test_single_ai_and_council_both_extract_facts`` above, but
    with one chat's turns carrying naive timestamps and the other aware,
    confirming the fix is independent of single-AI vs Council mode."""

    class StructuredProvider:
        async def complete(self, **_kwargs):
            return _structured(
                "summary",
                [
                    {
                        "fact_slot_id": "mode_check", "category": "fact", "content": "works",
                        "speaker": "user", "source_span": "Prompt 0", "grounding": "explicit_user_statement",
                    }
                ],
            )

    for with_verdict, naive in ((False, True), (True, False)):
        _patch_provider(monkeypatch, StructuredProvider())
        base = (
            datetime(2030, 3, 9)
            if naive
            else datetime(2030, 3, 9, tzinfo=UTC)
        )
        async with memory_env.Session() as db:
            org2 = Organization(name=f"org-tz-{with_verdict}", slug=f"org-tz-{with_verdict}")
            user2 = User(email=f"utz{with_verdict}@example.com", hashed_password="x", full_name="U")
            db.add_all([org2, user2])
            await db.flush()
            chat2 = Chat(org_id=org2.id, created_by=user2.id, title="c")
            db.add(chat2)
            await db.commit()
            for i in range(11):
                await _add_completed_turn(
                    db, chat_id=chat2.id, index=i, created_at=base + timedelta(minutes=i),
                    with_verdict=with_verdict,
                )
            await db.commit()
            merged = await chat_memory_service.merge_expired_turns(db, chat_id=chat2.id, org_id=org2.id)
            current = await chat_memory_service.current_facts_for_chat(db, chat_id=chat2.id, org_id=org2.id)
        assert merged == 1
        assert len(current) == 1


@pytest.mark.asyncio
async def test_critical_facts_no_crash_at_50_exchanges_naive_aware_mix(memory_env, monkeypatch):
    """Reproduces the original crash's shape at the scale it first appeared
    (the real evaluation crashed at 50 exchanges): many merges, repeated
    corrections to the same fact slot, turns alternating naive/aware
    timestamps, and `ChatCriticalFact.created_at` always left to the
    (naive, on SQLite) server default."""

    class CorrectingProvider:
        def __init__(self):
            self.call = 0

        async def complete(self, **_kwargs):
            self.call += 1
            candidates = [
                {
                    "fact_slot_id": "budget_cap", "category": "fact",
                    "content": f"Budget cap is ${self.call * 1000}", "speaker": "user",
                    "source_span": "Prompt", "grounding": "explicit_user_statement",
                }
            ]
            return _structured(f"compact summary #{self.call}", candidates)

    _patch_provider(monkeypatch, CorrectingProvider())
    async with memory_env.Session() as db:
        for i in range(50):
            created = (
                datetime(2030, 3, 10, 0, 0, 0) + timedelta(hours=i)
                if i % 2 == 0
                else datetime(2030, 3, 10, 0, 0, 0, tzinfo=UTC) + timedelta(hours=i)
            )
            await _add_completed_turn(db, chat_id=memory_env.chat_id, index=i, created_at=created)
        await db.commit()
        merged_total = 0
        for _ in range(60):
            n = await chat_memory_service.merge_expired_turns(db, chat_id=memory_env.chat_id, org_id=memory_env.org_id)
            merged_total += n
            if n == 0:
                break
        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert merged_total == 40  # 50 turns, last 10 stay in the recent window
    assert len(current) == 1
    assert current[0].content == "Budget cap is $40000"  # latest correction wins


@pytest.mark.asyncio
async def test_critical_facts_no_crash_at_200_exchanges_naive_aware_mix(memory_env, monkeypatch):
    """Same as the 50-exchange reproduction above, scaled to 200 exchanges
    (the scale of the fuller real evaluation run)."""

    class CorrectingProvider:
        def __init__(self):
            self.call = 0

        async def complete(self, **_kwargs):
            self.call += 1
            candidates = [
                {
                    "fact_slot_id": "budget_cap", "category": "fact",
                    "content": f"Budget cap is ${self.call * 1000}", "speaker": "user",
                    "source_span": "Prompt", "grounding": "explicit_user_statement",
                }
            ]
            return _structured(f"compact summary #{self.call}", candidates)

    _patch_provider(monkeypatch, CorrectingProvider())
    async with memory_env.Session() as db:
        for i in range(200):
            created = (
                datetime(2030, 3, 11, 0, 0, 0) + timedelta(minutes=i)
                if i % 2 == 0
                else datetime(2030, 3, 11, 0, 0, 0, tzinfo=UTC) + timedelta(minutes=i)
            )
            await _add_completed_turn(db, chat_id=memory_env.chat_id, index=i, created_at=created)
        await db.commit()
        merged_total = 0
        for _ in range(250):
            n = await chat_memory_service.merge_expired_turns(db, chat_id=memory_env.chat_id, org_id=memory_env.org_id)
            merged_total += n
            if n == 0:
                break
        current = await chat_memory_service.current_facts_for_chat(
            db, chat_id=memory_env.chat_id, org_id=memory_env.org_id
        )
    assert merged_total == 190
    assert len(current) == 1
    assert current[0].content == "Budget cap is $190000"  # latest correction wins
