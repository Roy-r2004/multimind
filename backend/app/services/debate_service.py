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

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

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

logger = get_logger(__name__)

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
            ModelAnswer.turn_id == turn_id, ModelAnswer.status == ModelAnswerStatus.COMPLETED
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


# --- Execution (standalone; opens its own sessions throughout) --------------


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
            tokens_input=data.get("tokens_input", 0),
            tokens_output=data.get("tokens_output", 0),
            cost_usd=data.get("cost_usd", 0.0),
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
                    tokens_input=data.get("tokens_input", 0),
                    tokens_output=data.get("tokens_output", 0),
                    cost_usd=data.get("cost_usd", 0.0),
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
            tokens_input=data.get("tokens_input", 0),
            tokens_output=data.get("tokens_output", 0),
            cost_usd=data.get("cost_usd", 0.0),
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
                    tokens_input=data.get("tokens_input", 0),
                    tokens_output=data.get("tokens_output", 0),
                    cost_usd=data.get("cost_usd", 0.0),
                )
            )
        await db.commit()


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
        await db.execute(
            update(DebateSession)
            .where(DebateSession.id == session_id, DebateSession.status == DebateSessionStatus.RUNNING)
            .values(
                status=final_status,
                agreements=[item.model_dump() for item in findings.agreements],
                unresolved_disagreements=[item.model_dump() for item in findings.unresolved_disagreements],
                corrections=[item.model_dump() for item in findings.corrections],
                risks=[item.model_dump() for item in findings.risks],
                final_summary=build_final_summary(findings),
                completed_at=datetime.now(UTC),
            )
        )
        await db.commit()


async def _mark_session_failed(session_id: str, error_message: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(DebateSession)
            .where(DebateSession.id == session_id, DebateSession.status == DebateSessionStatus.RUNNING)
            .values(
                status=DebateSessionStatus.FAILED,
                error_message=error_message,
                completed_at=datetime.now(UTC),
            )
        )
        await db.commit()


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
    """
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
        if event == "debate_challenge_completed" or event == "debate_challenge_failed":
            await _persist_challenge_result(
                org_id=org_id,
                turn_id=turn_id,
                chat_id=chat_id,
                session_id=session_id,
                model_id=data["model_id"],
                data=data,
            )
        elif event == "debate_response_completed" or event == "debate_response_failed":
            await _persist_response_result(
                org_id=org_id,
                turn_id=turn_id,
                chat_id=chat_id,
                session_id=session_id,
                model_id=data["model_id"],
                data=data,
            )

    try:
        result = await orchestrator.run(config, on_event=on_event)
    except Exception as exc:  # defensive: the engine itself should not raise
        logger.error("debate_execution_crashed", session_id=session_id, error=str(exc))
        await _mark_session_failed(session_id, str(exc))
        return
    await _finalize_session(session_id, result)
