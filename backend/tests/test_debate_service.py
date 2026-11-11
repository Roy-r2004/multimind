"""Phase 3 coverage: app.services.debate_service (eligibility, claiming,
incremental persistence, isolation from Council/Verdict/Challenge).

Uses the shared conftest `db`/`auth` fixtures for setup, and a dedicated
`_session_factory_for` helper (same pattern as test_gosom_supervisor.py) so
`execute_debate_session`'s internally-opened AsyncSessionLocal sessions write
to the SAME in-memory sqlite connection the test can then inspect.
"""

import asyncio
import json
from dataclasses import dataclass, field

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError
from app.db.models import (
    Chat,
    CostRecord,
    DebateChallenge,
    DebateResponse,
    DebateSession,
    DebateSessionStatus,
    ModelAnswer,
    ModelAnswerStatus,
    OrgRole,
    Organization,
    Strategy,
    Turn,
    TurnStatus,
    UsageKind,
    User,
    Verdict,
    VerdictLesson,
)
from app.llm.providers import LLMResponse
from app.services import debate_service

MODEL_A = "gpt-4.1"
MODEL_B = "gpt-4.1-mini"
MODEL_C = "gpt-5-nano"
ROUND3_MARKER = "RESPONSE TO CHALLENGES (ROUND 3)"


async def _noop_publish(*args, **kwargs) -> None:
    return None


def _session_factory_for(db_session: AsyncSession):
    bind = db_session.bind if db_session.bind is not None else db_session.get_bind()
    return async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)


def _serialized_session_factory(db_session: AsyncSession):
    """Like `_session_factory_for`, but serializes each `async with
    AsyncSessionLocal() as db: ...` block with a lock.

    The real orchestrator intentionally opens independent, concurrently-used
    sessions for persistence (debate_service.py's module docstring explains
    why — it's correct and desired against a real multi-connection Postgres
    pool). The test harness's in-memory sqlite StaticPool shares exactly ONE
    physical connection for every session this produces, which cannot safely
    interleave two independent transactions the way separate Postgres
    connections can. This lock exists only to make persistence assertions in
    THIS test file deterministic; it is not a statement about production
    behavior, and engine-level concurrency itself is already covered without
    any database in test_debate_orchestrator.py.
    """
    base_factory = async_sessionmaker(
        bind=db_session.bind if db_session.bind is not None else db_session.get_bind(),
        class_=AsyncSession,
        expire_on_commit=False,
    )
    lock = asyncio.Lock()

    class _LockedSession:
        async def __aenter__(self):
            await lock.acquire()
            self._inner = base_factory()
            return await self._inner.__aenter__()

        async def __aexit__(self, exc_type, exc, tb):
            try:
                return await self._inner.__aexit__(exc_type, exc, tb)
            finally:
                lock.release()

    return _LockedSession


async def _make_turn(
    db: AsyncSession,
    auth: AuthContext,
    *,
    model_ids: list[str],
    turn_status: TurnStatus = TurnStatus.COMPLETED,
    with_verdict: bool = True,
    incomplete_model_ids: list[str] | None = None,
) -> Turn:
    chat = Chat(org_id=auth.org_id, created_by=auth.user.id, title="Debate test chat")
    db.add(chat)
    await db.flush()
    turn = Turn(
        chat_id=chat.id,
        user_message="What should we do?",
        model_set_id="set-x",
        strategy=Strategy.SYNTHESIZE,
        verdict_model=MODEL_A,
        status=turn_status,
    )
    db.add(turn)
    await db.flush()
    for model_id in model_ids:
        db.add(
            ModelAnswer(
                turn_id=turn.id, model_id=model_id, text=f"Answer by {model_id}", status=ModelAnswerStatus.COMPLETED
            )
        )
    for model_id in incomplete_model_ids or []:
        db.add(ModelAnswer(turn_id=turn.id, model_id=model_id, status=ModelAnswerStatus.FAILED))
    if with_verdict:
        db.add(Verdict(turn_id=turn.id, model_id=MODEL_A, strategy=Strategy.SYNTHESIZE, text="V", reason="R"))
    await db.flush()
    return turn


async def _other_org_auth(db: AsyncSession) -> AuthContext:
    org = Organization(name="Other Org", slug="other-org-debate")
    user = User(email="other-debate@example.com", hashed_password="x", full_name="Other")
    db.add_all([org, user])
    await db.flush()
    return AuthContext(user=user, org_id=org.id, role=OrgRole.OWNER)


# --- Eligibility --------------------------------------------------------------


async def test_eligibility_requires_completed_turn(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B], turn_status=TurnStatus.RUNNING, with_verdict=False)
    with pytest.raises(debate_service.DebateNotEligibleError):
        await debate_service.check_debate_eligibility(db, auth, turn.id)


async def test_eligibility_requires_a_finalized_verdict(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B], with_verdict=False)
    with pytest.raises(debate_service.DebateNotEligibleError):
        await debate_service.check_debate_eligibility(db, auth, turn.id)


async def test_eligibility_requires_at_least_two_completed_answers(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A], incomplete_model_ids=[MODEL_B])
    with pytest.raises(debate_service.DebateNotEligibleError):
        await debate_service.check_debate_eligibility(db, auth, turn.id)


async def test_eligibility_uses_only_completed_model_answers_not_modelset(db: AsyncSession, auth: AuthContext):
    """Participants come from ModelAnswer rows, never from re-resolving a
    (possibly since-changed) ModelSet — there's no ModelSet lookup at all."""
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B], incomplete_model_ids=[MODEL_C])
    _turn, _verdict, participants = await debate_service.check_debate_eligibility(db, auth, turn.id)
    assert {p.model_id for p in participants} == {MODEL_A, MODEL_B}


# --- 13. Organization isolation ----------------------------------------------


async def test_organization_isolation_turn_from_other_org_is_not_found(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    other_auth = await _other_org_auth(db)
    with pytest.raises(NotFoundError):
        await debate_service.check_debate_eligibility(db, other_auth, turn.id)


# --- Claiming / idempotency ---------------------------------------------------


async def test_start_debate_session_claims_a_running_session(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session, participants, claimed = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()
    assert claimed is True
    assert session.status == DebateSessionStatus.RUNNING
    assert session.turn_id == turn.id
    assert set(session.model_ids) == {MODEL_A, MODEL_B}
    assert {p.model_id for p in participants} == {MODEL_A, MODEL_B}


# --- 11. Concurrent start / idempotency --------------------------------------


async def test_concurrent_start_recovers_via_existing_active_session(db, auth, monkeypatch):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    first_session, _, claimed = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()
    assert claimed is True

    # Simulate a true race: a concurrent caller's "is there an active session?"
    # check would have returned None right before the other caller committed.
    # Forcing that here deterministically exercises the IntegrityError recovery
    # path instead of depending on real thread/process timing.
    original = debate_service._get_active_session
    seen = {"n": 0}

    async def flaky_get_active(db_, turn_id):
        seen["n"] += 1
        if seen["n"] == 1:
            return None
        return await original(db_, turn_id)

    monkeypatch.setattr(debate_service, "_get_active_session", flaky_get_active)

    second_session, _, claimed_again = await debate_service.start_debate_session(db, auth, turn.id)
    assert claimed_again is False
    assert second_session.id == first_session.id

    all_sessions = (await db.execute(select(DebateSession).where(DebateSession.turn_id == turn.id))).scalars().all()
    assert len(all_sessions) == 1  # the race never produced a duplicate row


# --- 12. Failed/cancelled session retry behavior + bounded retries ----------


async def test_a_new_session_can_start_after_a_previous_one_failed(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    first, _, _ = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()
    first.status = DebateSessionStatus.FAILED
    await db.flush()
    await db.commit()

    second, _, claimed = await debate_service.start_debate_session(db, auth, turn.id)
    assert claimed is True
    assert second.id != first.id

    sessions = (await db.execute(select(DebateSession).where(DebateSession.turn_id == turn.id))).scalars().all()
    assert len(sessions) == 2  # old failed attempt and the new one are both distinguishable


async def test_retries_are_bounded(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    for _ in range(debate_service.DEBATE_MAX_ATTEMPTS_PER_TURN):
        session, _, claimed = await debate_service.start_debate_session(db, auth, turn.id)
        assert claimed is True
        await db.commit()
        session.status = DebateSessionStatus.FAILED
        await db.flush()
        await db.commit()

    with pytest.raises(debate_service.DebateNotEligibleError):
        await debate_service.start_debate_session(db, auth, turn.id)


async def test_no_new_council_turn_is_created_for_a_debate_retry(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session, _, _ = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()
    session.status = DebateSessionStatus.FAILED
    await db.flush()
    await db.commit()
    await debate_service.start_debate_session(db, auth, turn.id)

    turns = (await db.execute(select(Turn).where(Turn.chat_id == turn.chat_id))).scalars().all()
    assert len(turns) == 1  # still just the one original Council turn


# --- Scripted provider for execute_debate_session integration tests --------


@dataclass
class ScriptedProvider:
    round2_by_slug: dict[str, object] = field(default_factory=dict)
    round3_by_slug: dict[str, object] = field(default_factory=dict)

    async def complete(self, *, system, user, model, max_tokens):
        table = self.round3_by_slug if ROUND3_MARKER in system else self.round2_by_slug
        outcome = table.get(model)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome is None:
            raise RuntimeError(f"no scripted response for {model}")
        return outcome


@dataclass
class ScriptedRegistry:
    provider: ScriptedProvider

    def get_provider(self, _name: str) -> ScriptedProvider:
        return self.provider


def _challenge_llm(author: str, challenges: list[dict]) -> LLMResponse:
    return LLMResponse(text=json.dumps({"author_model_id": author, "challenges": challenges}), tokens_input=10, tokens_output=5, cost_usd=0.002)


def _response_llm(author: str, responses: list[dict]) -> LLMResponse:
    return LLMResponse(text=json.dumps({"author_model_id": author, "responses": responses}), tokens_input=6, tokens_output=3, cost_usd=0.0015)


async def _run_execute_debate_session(db, monkeypatch, *, turn, participants, provider, **overrides):
    # Use the lock-serialized factory (see its docstring): the in-memory
    # sqlite StaticPool shares one physical connection across every
    # AsyncSessionLocal() a real concurrent run opens, which can silently
    # lose a write when two independent sessions interleave on it — unlike a
    # real multi-connection Postgres pool. Round-2/3 *engine* concurrency
    # itself is already fully covered without any database in
    # test_debate_orchestrator.py; this file is about persistence
    # correctness, so make the DB side of it deterministic.
    factory = _serialized_session_factory(db)
    monkeypatch.setattr(debate_service, "AsyncSessionLocal", factory)
    monkeypatch.setattr(debate_service, "get_provider_registry", lambda: ScriptedRegistry(provider))
    # No live Redis in the test environment; SSE publishing is Phase 4's
    # concern and covered separately (test_debate_events.py / the API
    # tests), not something this persistence-focused suite needs to send
    # over a real socket (publish_debate_event already swallows a Redis
    # outage on its own, but skipping the real connection attempt here
    # keeps this suite fast and deterministic).
    monkeypatch.setattr(debate_service, "publish_debate_event", _noop_publish)
    session, loaded_participants, _ = await debate_service.start_debate_session(db, auth=overrides.pop("auth"), turn_id=turn.id)
    await db.commit()
    await debate_service.execute_debate_session(
        org_id=session.org_id,
        turn_id=turn.id,
        chat_id=turn.chat_id,
        session_id=session.id,
        user_message=turn.user_message,
        participants=participants or loaded_participants,
        **overrides,
    )
    return session


# --- 10. Partial debate completion + 14. cost accounting --------------------


async def test_partial_completion_and_cost_accounting(db: AsyncSession, auth: AuthContext, monkeypatch):
    """Exercises the persistence helpers directly and sequentially.

    The in-memory sqlite StaticPool used by the test harness shares exactly
    one physical connection across every AsyncSessionLocal() a real
    concurrent orchestrator run opens — unlike a real multi-connection
    Postgres pool, concurrent independent writers against that single
    connection are not safely isolated here. Calling the persistence
    functions directly (in the order the engine would call them) tests the
    exact same code path deterministically instead of depending on sqlite
    coping with interleaved writers it was never meant to support.
    """
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session, participants, claimed = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()
    assert claimed is True
    factory = _session_factory_for(db)
    monkeypatch.setattr(debate_service, "AsyncSessionLocal", factory)
    monkeypatch.setattr(debate_service, "publish_debate_event", _noop_publish)

    await debate_service._persist_challenge_result(
        org_id=session.org_id,
        turn_id=turn.id,
        chat_id=turn.chat_id,
        session_id=session.id,
        model_id=MODEL_A,
        data={"challenges": [], "tokens_input": 10, "tokens_output": 5, "cost_usd": 0.002, "error": None},
    )
    await debate_service._persist_challenge_result(
        org_id=session.org_id,
        turn_id=turn.id,
        chat_id=turn.chat_id,
        session_id=session.id,
        model_id=MODEL_B,
        data={"challenges": None, "tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0, "error": "provider unavailable"},
    )

    from app.llm.debate_contracts import ChallengeSet
    from app.llm.debate_orchestrator import ChallengeCallResult, DebateRunResult, LLMCallOutcome

    run_result = DebateRunResult(
        challenge_results=[
            ChallengeCallResult(
                model_id=MODEL_A,
                challenge_set=ChallengeSet(author_model_id=MODEL_A, challenges=[]),
                outcome=LLMCallOutcome(model_id=MODEL_A, tokens_input=10, tokens_output=5, cost_usd=0.002),
            ),
            ChallengeCallResult(
                model_id=MODEL_B,
                challenge_set=None,
                outcome=LLMCallOutcome(model_id=MODEL_B, error="provider unavailable"),
            ),
        ]
    )
    await debate_service._finalize_session(session.id, run_result)

    reloaded = (
        await db.execute(select(DebateSession).where(DebateSession.id == session.id).execution_options(populate_existing=True))
    ).scalar_one()
    assert reloaded.status == DebateSessionStatus.PARTIAL
    assert reloaded.completed_at is not None

    challenges = (await db.execute(select(DebateChallenge).where(DebateChallenge.debate_session_id == session.id))).scalars().all()
    by_author = {c.author_model_id: c for c in challenges}
    assert by_author[MODEL_A].status.value == "completed"
    assert by_author[MODEL_B].status.value == "failed"
    assert by_author[MODEL_B].error_message == "provider unavailable"

    cost_records = (await db.execute(select(CostRecord).where(CostRecord.turn_id == turn.id))).scalars().all()
    assert len(cost_records) == 1  # only the model that actually returned usage
    assert cost_records[0].kind == UsageKind.DEBATE
    assert cost_records[0].model_id == MODEL_A
    assert cost_records[0].cost_usd == pytest.approx(0.002)


async def test_fully_successful_debate_with_rebuttal_completes(db: AsyncSession, auth: AuthContext, monkeypatch):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_llm(MODEL_A, [{"challenge_id": "c1", "target_model_id": MODEL_B, "issue": "x", "reasoning": "y", "severity": "low"}]),
            "openai/gpt-4.1-mini": _challenge_llm(MODEL_B, []),
        },
        round3_by_slug={
            "openai/gpt-4.1-mini": _response_llm(MODEL_B, [{"challenge_id": "c1", "challenger_model_id": MODEL_A, "stance": "defends", "explanation": "ok"}]),
        },
    )
    session = await _run_execute_debate_session(db, monkeypatch, turn=turn, participants=None, provider=provider, auth=auth)

    reloaded = (await db.execute(select(DebateSession).where(DebateSession.id == session.id).execution_options(populate_existing=True))).scalar_one()
    assert reloaded.status == DebateSessionStatus.COMPLETED
    assert len(reloaded.unresolved_disagreements) == 1
    assert reloaded.final_summary  # a short deterministic string, not empty

    responses = (await db.execute(select(DebateResponse).where(DebateResponse.debate_session_id == session.id))).scalars().all()
    assert len(responses) == 1
    assert responses[0].author_model_id == MODEL_B


# --- 15. Execution timeout ----------------------------------------------------


async def test_execution_deadline_marks_session_partial(db: AsyncSession, auth: AuthContext, monkeypatch):
    import asyncio

    async def _slow_response():
        await asyncio.sleep(1.0)
        return _challenge_llm(MODEL_B, [])

    class SlowProvider(ScriptedProvider):
        async def complete(self, *, system, user, model, max_tokens):
            if model == "openai/gpt-4.1-mini":
                return await _slow_response()
            return await super().complete(system=system, user=user, model=model, max_tokens=max_tokens)

    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    provider = SlowProvider(round2_by_slug={"openai/gpt-4.1": _challenge_llm(MODEL_A, [])})
    session = await _run_execute_debate_session(
        db,
        monkeypatch,
        turn=turn,
        participants=None,
        provider=provider,
        auth=auth,
        per_call_timeout_seconds=10.0,
        overall_deadline_seconds=0.05,
    )

    reloaded = (await db.execute(select(DebateSession).where(DebateSession.id == session.id).execution_options(populate_existing=True))).scalar_one()
    assert reloaded.status == DebateSessionStatus.PARTIAL


# --- 16/17. No mutation of original Council responses or Verdict -----------


async def test_execution_never_mutates_original_model_answers_or_verdict(db: AsyncSession, auth: AuthContext, monkeypatch):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    answers_before = {
        a.model_id: (a.text, a.status, a.confidence)
        for a in (await db.execute(select(ModelAnswer).where(ModelAnswer.turn_id == turn.id))).scalars().all()
    }
    verdict_before = (await db.execute(select(Verdict).where(Verdict.turn_id == turn.id).execution_options(populate_existing=True))).scalar_one()
    verdict_snapshot_before = (verdict_before.text, verdict_before.reason, verdict_before.cost_usd)
    turn_status_before = turn.status

    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_llm(MODEL_A, [{"challenge_id": "c1", "target_model_id": MODEL_B, "issue": "x", "reasoning": "y", "severity": "high"}]),
            "openai/gpt-4.1-mini": _challenge_llm(MODEL_B, []),
        },
        round3_by_slug={
            "openai/gpt-4.1-mini": _response_llm(MODEL_B, [{"challenge_id": "c1", "challenger_model_id": MODEL_A, "stance": "concedes", "explanation": "ok", "updated_position": "revised"}]),
        },
    )
    await _run_execute_debate_session(db, monkeypatch, turn=turn, participants=None, provider=provider, auth=auth)

    answers_after = {
        a.model_id: (a.text, a.status, a.confidence)
        for a in (
            await db.execute(
                select(ModelAnswer)
                .where(ModelAnswer.turn_id == turn.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    }
    assert answers_after == answers_before

    verdict_after = (await db.execute(select(Verdict).where(Verdict.turn_id == turn.id).execution_options(populate_existing=True))).scalar_one()
    assert (verdict_after.text, verdict_after.reason, verdict_after.cost_usd) == verdict_snapshot_before

    refreshed_turn = (await db.execute(select(Turn).where(Turn.id == turn.id).execution_options(populate_existing=True))).scalar_one()
    assert refreshed_turn.status == turn_status_before
    assert refreshed_turn.error_message is None


# --- 18. No interaction with Challenge/Lessons -------------------------------


async def test_execution_never_touches_verdict_lesson_table(db: AsyncSession, auth: AuthContext, monkeypatch):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_llm(MODEL_A, []),
            "openai/gpt-4.1-mini": _challenge_llm(MODEL_B, []),
        }
    )
    await _run_execute_debate_session(db, monkeypatch, turn=turn, participants=None, provider=provider, auth=auth)
    lessons = (await db.execute(select(VerdictLesson).where(VerdictLesson.turn_id == turn.id))).scalars().all()
    assert lessons == []


def test_debate_service_does_not_import_lesson_service():
    import app.services.debate_service as module

    assert not hasattr(module, "lesson_service")
    assert "lesson_service" not in module.__dict__
