"""Phase 4: Debate Mode API/execution-lifecycle coverage.

Tests app.services.debate_service's Phase 4 additions directly (get-latest,
get-by-id, cancellation, the atomic execution claim, the stale-session
recovery cron, and dispatch) plus app.api.v1.debates' thin response-shaping
helpers — the same service-layer testing convention as test_debate_service.py
and test_saved_verdicts.py use throughout this codebase, rather than a full
HTTP TestClient. Isolation from Council/Verdict/Challenge is already proven
exhaustively in test_debate_service.py and test_debate_protected_files_unchanged.py;
this file covers what's new in Phase 4.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.debates import _to_response, _usage_summary
from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, NotFoundError
from app.db.models import DebateChallenge, DebateSession, DebateSessionStatus
from app.services import debate_service
from tests.test_debate_service import MODEL_A, MODEL_B, _make_turn, _other_org_auth

pytestmark = pytest.mark.asyncio


# --- 9/10. Get latest / get historical by id, deterministic ordering --------


async def test_get_latest_debate_session_returns_most_recently_started(
    db: AsyncSession, auth: AuthContext
):
    """Ordered by started_at (microsecond-precision, set in Python), not
    created_at (a DB server_default=func.now() coarse enough for two
    sessions created moments apart to tie) or id (random UUID4, not
    time-ordered)."""
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    first, _, _ = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()
    await db.execute(
        select(DebateSession)
        .where(DebateSession.id == first.id)
        .execution_options(populate_existing=True)
    )
    first.status = DebateSessionStatus.FAILED
    await db.commit()

    second, _, claimed = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()
    assert claimed is True
    assert second.id != first.id

    latest = await debate_service.get_latest_debate_session(db, auth, turn.id)
    assert latest.id == second.id


async def test_get_debate_session_by_id_fetches_a_historical_terminal_session(
    db: AsyncSession, auth: AuthContext
):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session, _, _ = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()
    session.status = DebateSessionStatus.COMPLETED
    await db.commit()

    fetched = await debate_service.get_debate_session_by_id(db, auth, session.id)
    assert fetched.id == session.id
    assert fetched.status == DebateSessionStatus.COMPLETED


async def test_get_debate_session_by_id_cross_org_is_not_found(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session, _, _ = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()

    other_auth = await _other_org_auth(db)
    with pytest.raises(NotFoundError):
        await debate_service.get_debate_session_by_id(db, other_auth, session.id)


async def test_get_latest_debate_for_turn_404s_when_none_exists(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    with pytest.raises(NotFoundError):
        await debate_service.get_latest_debate_session(db, auth, turn.id)


# --- 14/15/21. Cancellation ---------------------------------------------------


async def test_cancel_running_session_is_persisted_and_idempotent(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session, _, _ = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()

    cancelled_once = await debate_service.cancel_debate_session(db, auth, session.id)
    assert cancelled_once.status == DebateSessionStatus.CANCELLED
    assert cancelled_once.completed_at is not None

    cancelled_again = await debate_service.cancel_debate_session(db, auth, session.id)
    assert cancelled_again.status == DebateSessionStatus.CANCELLED


async def test_cancel_a_completed_session_is_rejected_not_silently_ignored(
    db: AsyncSession, auth: AuthContext
):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session, _, _ = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()
    session.status = DebateSessionStatus.COMPLETED
    await db.commit()

    with pytest.raises(ConflictError):
        await debate_service.cancel_debate_session(db, auth, session.id)


async def test_cancellation_never_touches_turn_or_verdict(db: AsyncSession, auth: AuthContext):
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session, _, _ = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()

    from app.db.models import Turn, Verdict

    turn_before = (await db.execute(select(Turn).where(Turn.id == turn.id))).scalar_one()
    turn_status_before = turn_before.status
    verdict_before = (await db.execute(select(Verdict).where(Verdict.turn_id == turn.id))).scalar_one()
    verdict_text_before = verdict_before.text

    await debate_service.cancel_debate_session(db, auth, session.id)

    turn_after = (
        await db.execute(select(Turn).where(Turn.id == turn.id).execution_options(populate_existing=True))
    ).scalar_one()
    verdict_after = (
        await db.execute(
            select(Verdict).where(Verdict.turn_id == turn.id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert turn_after.status == turn_status_before
    assert verdict_after.text == verdict_text_before


async def test_a_late_persist_after_cancellation_is_silently_skipped(db: AsyncSession, auth: AuthContext, monkeypatch):
    """Cancellation must prevent further debate WRITES, not just flip a flag
    nobody checks. _persist_challenge_result already guards on `status ==
    RUNNING`; this proves cancellation is what actually disarms it."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session, _, _ = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()

    monkeypatch.setattr(debate_service, "publish_debate_event", _noop)
    factory = async_sessionmaker(bind=db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(debate_service, "AsyncSessionLocal", factory)

    await debate_service.cancel_debate_session(db, auth, session.id)

    await debate_service._persist_challenge_result(
        org_id=session.org_id,
        turn_id=turn.id,
        chat_id=turn.chat_id,
        session_id=session.id,
        model_id=MODEL_A,
        data={"challenges": [], "tokens_input": 5, "tokens_output": 5, "cost_usd": 0.001, "error": None},
    )

    rows = (
        await db.execute(select(DebateChallenge).where(DebateChallenge.debate_session_id == session.id))
    ).scalars().all()
    assert rows == []


# --- Atomic execution claim (duplicate job delivery) -------------------------


async def test_claim_execution_slot_is_single_winner(db: AsyncSession, auth: AuthContext, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session, _, _ = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(debate_service, "AsyncSessionLocal", factory)

    first = await debate_service._claim_execution_slot(session.id)
    second = await debate_service._claim_execution_slot(session.id)
    assert first is True
    assert second is False


# --- Dispatch: repeated starts never trigger a second execution -------------


async def test_start_and_dispatch_only_enqueues_on_the_freshly_claimed_call(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    calls: list[str] = []

    async def fake_enqueue(session_id: str) -> None:
        calls.append(session_id)

    monkeypatch.setattr(debate_service, "enqueue_debate_execution", fake_enqueue)

    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session_a, started_a = await debate_service.start_and_dispatch_debate(db, auth, turn.id)
    session_b, started_b = await debate_service.start_and_dispatch_debate(db, auth, turn.id)

    assert started_a is True
    assert started_b is False
    assert session_a.id == session_b.id
    assert calls == [session_a.id]


# --- Recovery: stale RUNNING sessions after a worker/API restart ------------


async def test_recover_debate_sessions_fails_only_sessions_stale_past_threshold(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    stale_session, _, _ = await debate_service.start_debate_session(db, auth, turn.id)
    stale_session.started_at = datetime.now(UTC) - timedelta(hours=1)
    await db.commit()

    other_turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    fresh_session, _, _ = await debate_service.start_debate_session(db, auth, other_turn.id)
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(debate_service, "AsyncSessionLocal", factory)
    monkeypatch.setattr(debate_service, "publish_debate_event", _noop)
    settings = debate_service.get_settings()
    monkeypatch.setattr(settings, "debate_execution_stale_seconds", 60)

    await debate_service.recover_debate_sessions({})

    reloaded_stale = (
        await db.execute(
            select(DebateSession).where(DebateSession.id == stale_session.id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    reloaded_fresh = (
        await db.execute(
            select(DebateSession).where(DebateSession.id == fresh_session.id).execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert reloaded_stale.status == DebateSessionStatus.FAILED
    assert reloaded_stale.error_message is not None
    assert reloaded_fresh.status == DebateSessionStatus.RUNNING


# --- Cost tracking: unknown vs. zero ------------------------------------------


async def test_usage_summary_distinguishes_unknown_from_zero_cost(db: AsyncSession, auth: AuthContext, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session, _, _ = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()

    monkeypatch.setattr(debate_service, "publish_debate_event", _noop)
    factory = async_sessionmaker(bind=db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(debate_service, "AsyncSessionLocal", factory)

    # MODEL_A actually returned usage (a real, if zero-cost, provider response).
    await debate_service._persist_challenge_result(
        org_id=session.org_id, turn_id=turn.id, chat_id=turn.chat_id, session_id=session.id,
        model_id=MODEL_A, data={"challenges": [], "tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0, "error": None},
    )
    # MODEL_B failed before the provider ever responded — usage is unknown, not zero.
    await debate_service._persist_challenge_result(
        org_id=session.org_id, turn_id=turn.id, chat_id=turn.chat_id, session_id=session.id,
        model_id=MODEL_B, data={"challenges": None, "tokens_input": None, "tokens_output": None, "cost_usd": None, "error": "timeout"},
    )

    full = await debate_service.get_debate_session_by_id(db, auth, session.id)
    usage = _usage_summary(full)
    assert usage.calls_with_unknown_usage == 1
    assert usage.total_tokens_input == 0
    assert usage.total_cost_usd == 0.0


async def test_to_response_exposes_unchallenged_model_ids_not_agreements(db: AsyncSession, auth: AuthContext):
    """Phase 4's findings-semantics fix, round-tripped through the API shape."""
    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session, _, _ = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()
    session.status = DebateSessionStatus.COMPLETED
    session.agreements = []
    session.unchallenged_model_ids = [MODEL_A, MODEL_B]
    await db.commit()

    full = await debate_service.get_debate_session_by_id(db, auth, session.id)
    response = _to_response(full)
    assert response.agreements == []
    assert response.unchallenged_model_ids == [MODEL_A, MODEL_B]


async def _noop(*args, **kwargs) -> None:
    return None
