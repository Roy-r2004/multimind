"""AI Debate Mode persistence service — isolated from Council/Verdict services.

Two entry points, deliberately split so Phase 4 can wire either one into any
transport without changing this file:

- ``start_debate_session`` — fast, request-scoped (uses the caller's ``db``):
  validates eligibility and idempotently claims a ``DebateSession`` row. A
  future API endpoint calls this synchronously to decide whether execution
  should begin.
- ``execute_debate_session`` — slow, standalone (opens its own sessions
  throughout, never touches the caller's ``db``): runs ``DebateOrchestrator``
  and persists each challenge/response the moment it completes, each in its
  own short transaction. This boundary is exactly what lets Phase 4 run it
  from either a spawned ``asyncio.Task`` or a future ARQ job (see the
  "Execution boundary" note below) without any change here.

Every read of Turn/ModelAnswer/Verdict in this module is read-only. The only
tables this module ever writes to are DebateSession, DebateChallenge,
DebateResponse, and CostRecord (kind=UsageKind.DEBATE).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.db.models import (
    Chat,
    CostRecord,
    DebateChallenge,
    DebateChallengeStatus,
    DebateResponse,
    DebateResponseStatus,
    DebateSession,
    DebateSessionStatus,
    ModelAnswer,
    ModelAnswerStatus,
    Turn,
    TurnStatus,
    UsageKind,
    Verdict,
)
from app.db.session import AsyncSessionLocal
from app.llm.catalog import get_model
from app.llm.debate_orchestrator import (
    DebateOrchestrator,
    DebateParticipant,
    DebateRunConfig,
    DebateRunResult,
    build_final_summary,
)
from app.llm.providers import get_provider_registry
from app.services.debate_events import publish_debate_event

logger = get_logger(__name__)

# Debate execution runs on its OWN dedicated ARQ worker/queue (app/debate/worker.py,
# settings.debate_worker_queue_name) — fully independent of the scraping worker's
# queue, concurrency, timeouts, and recovery cadence. `run_debate_session_job` and
# `recover_debate_sessions` are defined here (not in app/debate/worker.py) because
# they need the same persistence helpers the request-scoped "start" path uses; that
# module only wires them onto their own queue/schedule.
DEBATE_ARQ_JOB_NAME = "run_debate_session_job"

ACTIVE_DEBATE_SESSION_STATUSES = (DebateSessionStatus.PENDING, DebateSessionStatus.RUNNING)
DEBATE_ACTIVE_UNIQUE_CONSTRAINT = "uq_debate_sessions_active_turn"
# Bounded retries: Phase 1's schema allows a fresh session once the previous
# one reaches a terminal state, but "bounded" per Phase 3 §6 means a cap, not
# unlimited attempts. Enforced here in Python (no DebateSession column for
# it) rather than adding a migration for something that isn't a correctness
# issue — see Phase 3's final report for this trade-off.
DEBATE_MAX_ATTEMPTS_PER_TURN = 3


class DebateNotEligibleError(ConflictError):
    """Raised when a Turn does not yet satisfy Debate Mode's preconditions."""


# --- Eligibility (read-only) -------------------------------------------------


async def _load_eligible_turn(db: AsyncSession, auth: AuthContext, turn_id: str) -> Turn:
    result = await db.execute(
        select(Turn)
        .join(Chat, Chat.id == Turn.chat_id)
        .where(Turn.id == turn_id, Turn.deleted_at.is_(None), Chat.org_id == auth.org_id)
    )
    turn = result.scalar_one_or_none()
    if turn is None:
        raise NotFoundError("Turn", turn_id)
    if turn.status not in (TurnStatus.COMPLETED, TurnStatus.PARTIAL):
        raise DebateNotEligibleError(
            f"Turn {turn_id} has not completed (status={turn.status.value}); "
            "Debate Mode requires a finished Council turn."
        )
    return turn


async def _load_finalized_verdict(db: AsyncSession, turn_id: str) -> Verdict:
    result = await db.execute(select(Verdict).where(Verdict.turn_id == turn_id))
    verdict = result.scalar_one_or_none()
    if verdict is None:
        raise DebateNotEligibleError(
            f"Turn {turn_id} has no finalized Verdict; Debate Mode runs only after "
            "the original Verdict exists."
        )
    return verdict


async def _load_participants(db: AsyncSession, turn_id: str) -> list[DebateParticipant]:
    """Participating model IDs come ONLY from the original Turn's completed
    ModelAnswer rows — never from re-resolving the turn's (possibly since
    edited or deleted) ModelSet."""
    result = await db.execute(
        select(ModelAnswer).where(
            ModelAnswer.turn_id == turn_id,
            # VARCHAR semantics for this legacy status column — mirrors
            # app.llm.orchestrator's identical
            # func.lower(cast(..., String)) == <value> workaround exactly.
            # ModelAnswer.status is declared Enum(native_enum=True) with no
            # values_callable, so (a) the column stores the enum member's
            # NAME ("COMPLETED"), not its lowercase .value, and (b) comparing
            # directly against the enum member makes SQLAlchemy emit a bind
            # cast to a Postgres type ("modelanswerstatus") that no
            # migration ever actually created — raising UndefinedObjectError
            # against real PostgreSQL. SQLite's dialect has no native enums
            # and is far more lenient about case, so neither failure mode
            # was visible against the in-memory test database.
            func.lower(cast(ModelAnswer.status, String)) == ModelAnswerStatus.COMPLETED.value,
        )
    )
    answers = result.scalars().all()
    if len(answers) < 2:
        raise DebateNotEligibleError(
            f"Turn {turn_id} has {len(answers)} completed Council answer(s); "
            "Debate Mode requires at least two."
        )
    participants: list[DebateParticipant] = []
    for answer in answers:
        try:
            name = get_model(answer.model_id).name
        except Exception:
            name = answer.model_id
        participants.append(
            DebateParticipant(model_id=answer.model_id, model_name=name, answer_text=answer.text or "")
        )
    return participants


async def check_debate_eligibility(
    db: AsyncSession, auth: AuthContext, turn_id: str
) -> tuple[Turn, Verdict, list[DebateParticipant]]:
    """Read-only precondition check (Phase 3 §2). Raises DebateNotEligibleError
    or NotFoundError; never raises for a reason that would require writing
    anything."""
    turn = await _load_eligible_turn(db, auth, turn_id)
    verdict = await _load_finalized_verdict(db, turn_id)
    participants = await _load_participants(db, turn_id)
    return turn, verdict, participants


# --- Session claiming (idempotent, request-scoped) --------------------------


async def _get_active_session(db: AsyncSession, turn_id: str) -> DebateSession | None:
    result = await db.execute(
        select(DebateSession).where(
            DebateSession.turn_id == turn_id,
            DebateSession.status.in_(ACTIVE_DEBATE_SESSION_STATUSES),
        )
    )
    return result.scalar_one_or_none()


async def _count_sessions_for_turn(db: AsyncSession, turn_id: str) -> int:
    result = await db.execute(select(DebateSession.id).where(DebateSession.turn_id == turn_id))
    return len(result.scalars().all())


def _is_active_session_unique_violation(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    constraint_name = getattr(orig, "constraint_name", None)
    diag = getattr(orig, "diag", None)
    if constraint_name is None and diag is not None:
        constraint_name = getattr(diag, "constraint_name", None)
    if constraint_name == DEBATE_ACTIVE_UNIQUE_CONSTRAINT:
        return True
    message = str(orig or exc).lower()
    if DEBATE_ACTIVE_UNIQUE_CONSTRAINT in message:
        return True
    # SQLite reports "UNIQUE constraint failed: debate_sessions.turn_id"
    # (table.column), not the index name — PostgreSQL reports the index
    # name via constraint_name above, so this branch is the SQLite fallback.
    return "unique constraint failed" in message and "debate_sessions.turn_id" in message


async def start_debate_session(
    db: AsyncSession, auth: AuthContext, turn_id: str
) -> tuple[DebateSession, list[DebateParticipant], bool]:
    """Validate eligibility and idempotently claim a DebateSession.

    Returns ``(session, participants, freshly_claimed)``. When
    ``freshly_claimed`` is False, an active session already existed (a
    concurrent request raced this one, or the caller is retrying) and the
    caller must NOT start a second execution for it.
    """
    turn, _verdict, participants = await check_debate_eligibility(db, auth, turn_id)

    existing = await _get_active_session(db, turn_id)
    if existing is not None:
        return existing, participants, False

    attempts = await _count_sessions_for_turn(db, turn_id)
    if attempts >= DEBATE_MAX_ATTEMPTS_PER_TURN:
        raise DebateNotEligibleError(
            f"Turn {turn_id} has already reached the maximum of "
            f"{DEBATE_MAX_ATTEMPTS_PER_TURN} debate attempts."
        )

    session = DebateSession(
        org_id=auth.org_id,
        turn_id=turn_id,
        status=DebateSessionStatus.RUNNING,
        model_ids=[p.model_id for p in participants],
        started_at=datetime.now(UTC),
    )
    try:
        async with db.begin_nested():
            db.add(session)
            await db.flush()
    except IntegrityError as exc:
        if not _is_active_session_unique_violation(exc):
            raise
        existing = await _get_active_session(db, turn_id)
        if existing is not None:
            return existing, participants, False
        raise
    return session, participants, True


# --- Read access (request-scoped) -------------------------------------------


async def _debate_session_query_base():
    """Join through Turn -> Chat so org ownership is always enforced by the
    same join Council/Verdict reads use, never by trusting a caller-supplied
    org_id on DebateSession alone. Eager-loads challenges/responses since
    every caller (both GET endpoints) needs them."""
    return (
        select(DebateSession)
        .join(Turn, Turn.id == DebateSession.turn_id)
        .join(Chat, Chat.id == Turn.chat_id)
        .options(selectinload(DebateSession.challenges), selectinload(DebateSession.responses))
    )


async def get_latest_debate_session(
    db: AsyncSession, auth: AuthContext, turn_id: str
) -> DebateSession:
    """Deterministic ordering: most recently STARTED session for the turn.

    Sorts by ``started_at`` (set in Python with microsecond precision by
    ``start_debate_session``, always non-null) rather than ``created_at``
    (a DB ``server_default=func.now()`` whose resolution can be coarse
    enough for two sessions created moments apart in rapid retries to tie) —
    and never by ``id``, since ids are random UUID4s, not time-ordered, so
    using one as a tiebreaker would make "latest" nondeterministic exactly
    when it matters most: distinguishing back-to-back retry attempts.
    """
    base = await _debate_session_query_base()
    result = await db.execute(
        base.where(DebateSession.turn_id == turn_id, Chat.org_id == auth.org_id)
        .order_by(DebateSession.started_at.desc(), DebateSession.created_at.desc())
        .limit(1)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise NotFoundError("DebateSession for turn", turn_id)
    return session


async def get_debate_session_by_id(
    db: AsyncSession, auth: AuthContext, session_id: str
) -> DebateSession:
    base = await _debate_session_query_base()
    result = await db.execute(
        base.where(DebateSession.id == session_id, Chat.org_id == auth.org_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise NotFoundError("DebateSession", session_id)
    return session


# --- Cancellation (request-scoped, idempotent) -------------------------------


async def cancel_debate_session(
    db: AsyncSession, auth: AuthContext, session_id: str
) -> DebateSession:
    """Stop only this debate session. Never touches Turn/ModelAnswer/Verdict.

    Idempotent: cancelling an already-cancelled session returns success with
    no change. Cancelling a session that already reached a different
    terminal state (completed/partial/failed) is rejected — there is nothing
    live left to stop, and silently no-op'ing there would hide a stale
    client's mistaken belief that a finished debate could still be stopped.
    """
    session = await get_debate_session_by_id(db, auth, session_id)
    if session.status == DebateSessionStatus.CANCELLED:
        return session
    if session.status not in ACTIVE_DEBATE_SESSION_STATUSES:
        raise ConflictError(
            f"DebateSession {session_id} already reached a terminal state "
            f"({session.status.value}); there is nothing to cancel."
        )
    result = await db.execute(
        update(DebateSession)
        .where(
            DebateSession.id == session_id,
            DebateSession.status.in_(ACTIVE_DEBATE_SESSION_STATUSES),
        )
        .values(status=DebateSessionStatus.CANCELLED, completed_at=datetime.now(UTC))
        .returning(DebateSession.id)
    )
    cancelled = result.scalar_one_or_none()
    await db.commit()
    if cancelled is None:
        # Lost a race with execution reaching a terminal state first.
        return await get_debate_session_by_id(db, auth, session_id)
    await publish_debate_event(session_id, "debate_cancelled", {})
    await db.refresh(session)
    return session


# --- Execution (standalone; opens its own sessions throughout) --------------


async def _claim_execution_slot(session_id: str) -> bool:
    """Atomic single-execution claim. Returns True only for whichever caller
    (an ARQ delivery, an inline asyncio task, or the recovery cron re-running
    a stale session) gets there first — duplicate job delivery for the same
    session_id can therefore never run the engine concurrently twice."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(DebateSession)
            .where(DebateSession.id == session_id, DebateSession.execution_claimed_at.is_(None))
            .values(execution_claimed_at=datetime.now(UTC))
            .returning(DebateSession.id)
        )
        claimed = result.scalar_one_or_none()
        await db.commit()
        return claimed is not None


async def _session_is_running(db: AsyncSession, session_id: str) -> bool:
    result = await db.execute(
        select(DebateSession.id).where(
            DebateSession.id == session_id, DebateSession.status == DebateSessionStatus.RUNNING
        )
    )
    return result.scalar_one_or_none() is not None


async def _persist_challenge_result(
    *, org_id: str, turn_id: str, chat_id: str | None, session_id: str, model_id: str, data: dict[str, Any]
) -> None:
    async with AsyncSessionLocal() as db:
        if not await _session_is_running(db, session_id):
            return  # a late result after the session already finalized/cancelled
        error = data.get("error")
        status = DebateChallengeStatus.FAILED if error else DebateChallengeStatus.COMPLETED
        row = DebateChallenge(
            org_id=org_id,
            debate_session_id=session_id,
            author_model_id=model_id,
            status=status,
            challenges=data.get("challenges"),
            error_message=error,
            tokens_input=data.get("tokens_input"),
            tokens_output=data.get("tokens_output"),
            cost_usd=data.get("cost_usd"),
        )
        db.add(row)
        if data.get("tokens_input") or data.get("tokens_output") or data.get("cost_usd"):
            db.add(
                CostRecord(
                    org_id=org_id,
                    chat_id=chat_id,
                    turn_id=turn_id,
                    model_id=model_id,
                    kind=UsageKind.DEBATE,
                    tokens_input=data.get("tokens_input") or 0,
                    tokens_output=data.get("tokens_output") or 0,
                    cost_usd=data.get("cost_usd") or 0.0,
                )
            )
        await db.commit()


async def _persist_response_result(
    *, org_id: str, turn_id: str, chat_id: str | None, session_id: str, model_id: str, data: dict[str, Any]
) -> None:
    async with AsyncSessionLocal() as db:
        if not await _session_is_running(db, session_id):
            return
        error = data.get("error")
        status = DebateResponseStatus.FAILED if error else DebateResponseStatus.COMPLETED
        row = DebateResponse(
            org_id=org_id,
            debate_session_id=session_id,
            author_model_id=model_id,
            status=status,
            responses=data.get("responses"),
            error_message=error,
            tokens_input=data.get("tokens_input"),
            tokens_output=data.get("tokens_output"),
            cost_usd=data.get("cost_usd"),
        )
        db.add(row)
        if data.get("tokens_input") or data.get("tokens_output") or data.get("cost_usd"):
            db.add(
                CostRecord(
                    org_id=org_id,
                    chat_id=chat_id,
                    turn_id=turn_id,
                    model_id=model_id,
                    kind=UsageKind.DEBATE,
                    tokens_input=data.get("tokens_input") or 0,
                    tokens_output=data.get("tokens_output") or 0,
                    cost_usd=data.get("cost_usd") or 0.0,
                )
            )
        await db.commit()


_TERMINAL_EVENT_BY_STATUS = {
    DebateSessionStatus.COMPLETED: "debate_completed",
    DebateSessionStatus.PARTIAL: "debate_partial",
    DebateSessionStatus.FAILED: "debate_failed",
}


async def _finalize_session(session_id: str, result: DebateRunResult) -> None:
    async with AsyncSessionLocal() as db:
        if not await _session_is_running(db, session_id):
            return
        findings = result.findings
        if result.all_calls_failed:
            final_status = DebateSessionStatus.FAILED
        elif result.timed_out or any(not r.outcome.succeeded for r in result.challenge_results) or any(
            not r.outcome.succeeded for r in result.response_results
        ):
            final_status = DebateSessionStatus.PARTIAL
        else:
            final_status = DebateSessionStatus.COMPLETED
        result_proxy = await db.execute(
            update(DebateSession)
            .where(DebateSession.id == session_id, DebateSession.status == DebateSessionStatus.RUNNING)
            .values(
                status=final_status,
                agreements=[item.model_dump() for item in findings.agreements],
                unchallenged_model_ids=list(findings.unchallenged_model_ids),
                unresolved_disagreements=[item.model_dump() for item in findings.unresolved_disagreements],
                corrections=[item.model_dump() for item in findings.corrections],
                risks=[item.model_dump() for item in findings.risks],
                uncertainties=[item.model_dump() for item in findings.uncertainties],
                final_summary=build_final_summary(findings),
                completed_at=datetime.now(UTC),
            )
            .returning(DebateSession.id)
        )
        finalized = result_proxy.scalar_one_or_none()
        await db.commit()
    if finalized is not None:
        await publish_debate_event(session_id, _TERMINAL_EVENT_BY_STATUS[final_status], {})


async def _mark_session_failed(session_id: str, error_message: str) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(DebateSession)
            .where(DebateSession.id == session_id, DebateSession.status == DebateSessionStatus.RUNNING)
            .values(
                status=DebateSessionStatus.FAILED,
                error_message=error_message,
                completed_at=datetime.now(UTC),
            )
            .returning(DebateSession.id)
        )
        failed = result.scalar_one_or_none()
        await db.commit()
    if failed is not None:
        await publish_debate_event(session_id, "debate_failed", {"error": error_message})


async def execute_debate_session(
    *,
    org_id: str,
    turn_id: str,
    chat_id: str | None,
    session_id: str,
    user_message: str,
    participants: list[DebateParticipant],
    max_concurrency: int | None = None,
    per_call_timeout_seconds: float | None = None,
    overall_deadline_seconds: float | None = None,
) -> None:
    """Run the engine and persist results incrementally. Standalone: opens its
    own DB sessions throughout and never shares one across concurrent writes
    (each on_event callback below gets a fresh, independent AsyncSessionLocal
    scoped to exactly one row, which is what makes this safe to run alongside
    the orchestrator's internal concurrency). See the module docstring for why
    this function takes no ``db`` parameter at all.

    Claims ``session_id``'s single execution slot atomically before doing
    anything else: if another caller (a duplicate ARQ delivery, a racing
    inline task, or the recovery cron) already claimed it, this call returns
    immediately without touching the database again or calling any provider.
    """
    if not await _claim_execution_slot(session_id):
        logger.info("debate_execution_already_claimed", session_id=session_id)
        return

    await publish_debate_event(session_id, "debate_started", {"participant_count": len(participants)})

    config_kwargs: dict[str, Any] = {}
    if max_concurrency is not None:
        config_kwargs["max_concurrency"] = max_concurrency
    if per_call_timeout_seconds is not None:
        config_kwargs["per_call_timeout_seconds"] = per_call_timeout_seconds
    if overall_deadline_seconds is not None:
        config_kwargs["overall_deadline_seconds"] = overall_deadline_seconds
    config = DebateRunConfig(user_message=user_message, participants=participants, **config_kwargs)
    orchestrator = DebateOrchestrator(get_provider_registry())

    async def on_event(event: str, data: dict[str, Any]) -> None:
        if event == "debate_challenge_started" or event == "debate_response_started":
            await publish_debate_event(session_id, event, data)
            return
        if event == "debate_challenge_completed" or event == "debate_challenge_failed":
            await _persist_challenge_result(
                org_id=org_id,
                turn_id=turn_id,
                chat_id=chat_id,
                session_id=session_id,
                model_id=data["model_id"],
                data=data,
            )
            await publish_debate_event(session_id, event, data)
        elif event == "debate_response_completed" or event == "debate_response_failed":
            await _persist_response_result(
                org_id=org_id,
                turn_id=turn_id,
                chat_id=chat_id,
                session_id=session_id,
                model_id=data["model_id"],
                data=data,
            )
            await publish_debate_event(session_id, event, data)
        elif event == "debate_findings_ready":
            await publish_debate_event(session_id, "debate_findings_ready", {})

    try:
        result = await orchestrator.run(config, on_event=on_event)
    except Exception as exc:  # defensive: the engine itself should not raise
        logger.error("debate_execution_crashed", session_id=session_id, error=str(exc))
        await _mark_session_failed(session_id, str(exc))
        return
    await _finalize_session(session_id, result)


# --- Dispatch and durable execution (Phase 4) --------------------------------


async def _load_session_for_execution(
    session_id: str,
) -> tuple[DebateSession, str, str | None, list[DebateParticipant]] | None:
    """Reload everything ``execute_debate_session`` needs from just a
    ``session_id`` — the only argument an ARQ job or the recovery cron can be
    handed, since job payloads cross a Redis queue rather than sharing
    memory with the request that enqueued them. Returns ``None`` if the
    session (or its Turn) no longer qualifies, e.g. a Turn deleted after the
    session was claimed."""
    async with AsyncSessionLocal() as db:
        session = await db.get(DebateSession, session_id)
        if session is None:
            return None
        turn = await db.get(Turn, session.turn_id)
        if turn is None:
            return None
        result = await db.execute(
            select(ModelAnswer).where(
                ModelAnswer.turn_id == session.turn_id,
                ModelAnswer.model_id.in_(session.model_ids),
                # See the identical func.lower(cast(..., String)) note in _load_participants above.
                func.lower(cast(ModelAnswer.status, String)) == ModelAnswerStatus.COMPLETED.value,
            )
        )
        answers = {a.model_id: a for a in result.scalars().all()}
        participants: list[DebateParticipant] = []
        for model_id in session.model_ids:
            answer = answers.get(model_id)
            if answer is None:
                continue
            try:
                name = get_model(model_id).name
            except Exception:
                name = model_id
            participants.append(
                DebateParticipant(model_id=model_id, model_name=name, answer_text=answer.text or "")
            )
        if len(participants) < 2:
            return None
        user_message = turn.user_message or ""
        return session, user_message, turn.chat_id, participants


async def _run_debate_session_standalone(session_id: str) -> None:
    loaded = await _load_session_for_execution(session_id)
    if loaded is None:
        await _mark_session_failed(
            session_id, "debate session or its underlying turn/answers were no longer available"
        )
        return
    session, user_message, chat_id, participants = loaded
    await execute_debate_session(
        org_id=session.org_id,
        turn_id=session.turn_id,
        chat_id=chat_id,
        session_id=session.id,
        user_message=user_message,
        participants=participants,
    )


async def run_debate_session_job(ctx: dict, session_id: str) -> None:
    """ARQ job entrypoint. Registered on the existing scraping worker's
    ``WorkerSettings.functions`` (see app/scraping/worker.py) rather than a
    dedicated Debate worker container."""
    del ctx  # ARQ passes worker context; unused here.
    await _run_debate_session_standalone(session_id)


async def enqueue_debate_execution(session_id: str) -> None:
    """Dispatch execution independently of the HTTP request that started it.

    Enqueues onto the dedicated Debate queue (``settings.debate_worker_queue_name``,
    consumed only by ``app.debate.worker`` — never the scraping worker's queue)
    when available, falling back to an inline ``asyncio.Task`` (same process) in
    development or if Redis is unreachable. Either path calls the exact same
    ``execute_debate_session`` engine; ``_claim_execution_slot`` inside it is what
    actually prevents a duplicate enqueue from running the session twice, not
    which path was taken here.

    ``settings.debate_inline_execution`` has three states:

    - ``None`` (default): auto-detect — inline in ``development``, else try the
      dedicated queue and silently fall back to inline if that fails. Convenient
      for plain ``pytest`` or a Redis-less dev shell.
    - ``False`` (explicit): the dedicated worker is required. A failed enqueue is
      NOT swallowed into a silent inline fallback — it's logged loudly and
      re-raised, so a misconfigured deployment fails visibly instead of quietly
      running debates without the durability a dedicated worker provides.
    - ``True`` (explicit): always run inline, skipping the queue entirely.
    """
    settings = get_settings()
    require_dedicated_worker = settings.debate_inline_execution is False
    inline = (
        settings.debate_inline_execution
        if settings.debate_inline_execution is not None
        else settings.environment == "development"
    )
    queued_on_redis = False
    if not inline:
        try:
            from urllib.parse import urlparse
            from uuid import uuid4

            from arq import create_pool
            from arq.connections import RedisSettings

            parsed = urlparse(settings.redis_url)
            redis = await create_pool(
                RedisSettings(
                    host=parsed.hostname or "localhost",
                    port=parsed.port or 6379,
                    database=int((parsed.path or "/0").lstrip("/") or "0"),
                    password=parsed.password,
                )
            )
            await redis.enqueue_job(
                DEBATE_ARQ_JOB_NAME,
                session_id,
                _queue_name=settings.debate_worker_queue_name,
                _job_id=f"{DEBATE_ARQ_JOB_NAME}:{session_id}:{uuid4().hex[:12]}",
            )
            await redis.close()
            queued_on_redis = True
        except Exception:
            if require_dedicated_worker:
                logger.error(
                    "debate_enqueue_failed_no_inline_fallback",
                    session_id=session_id,
                    queue=settings.debate_worker_queue_name,
                    exc_info=True,
                )
                raise
            logger.warning("debate_enqueue_redis_failed", session_id=session_id, exc_info=True)
    if inline or not queued_on_redis:
        asyncio.create_task(_run_debate_session_standalone(session_id))


async def start_and_dispatch_debate(
    db: AsyncSession, auth: AuthContext, turn_id: str
) -> tuple[DebateSession, bool]:
    """The single call an API endpoint makes: claim (or reuse) a session,
    then dispatch execution independently of this request/response cycle.
    Repeated calls while a debate is active return the same existing
    session and never dispatch a second execution."""
    session, _participants, freshly_claimed = await start_debate_session(db, auth, turn_id)
    await db.commit()
    if freshly_claimed:
        await enqueue_debate_execution(session.id)
    return session, freshly_claimed


async def recover_debate_sessions(ctx: dict) -> None:
    """Startup + periodic cron: a debate session stuck RUNNING well past the
    engine's own overall deadline means the worker or API process that was
    executing it died or restarted mid-run. Recovery here does NOT silently
    resume or re-run it — resuming a partially-executed debate safely would
    require knowing exactly which provider calls already happened, which
    this stateless engine does not track — it finalizes the session as
    FAILED so the user can see what happened and, if they choose, start a
    fresh attempt (still subject to DEBATE_MAX_ATTEMPTS_PER_TURN)."""
    del ctx
    threshold = datetime.now(UTC) - timedelta(seconds=get_settings().debate_execution_stale_seconds)
    stale_ids: list[str] = []
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(DebateSession.id).where(
                DebateSession.status == DebateSessionStatus.RUNNING,
                DebateSession.started_at.is_not(None),
                DebateSession.started_at < threshold,
            )
        )
        stale_ids = list(result.scalars().all())
    for session_id in stale_ids:
        logger.warning("debate_session_recovered_as_failed", session_id=session_id)
        await _mark_session_failed(
            session_id,
            "debate execution did not complete before the stale-session recovery threshold "
            "(worker or API restart) — start a new debate attempt if you still want one",
        )
