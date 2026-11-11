"""AI Debate Mode API (Phase 4) — isolated from Council/Verdict/Challenge routes.

Every endpoint here authenticates the caller and enforces organization
ownership through app.services.debate_service's Turn -> Chat join; none of
them ever reads or writes a Turn, ModelAnswer, or Verdict row, and none of
them lives under app/api/v1/chats.py, lessons.py, or verdicts.py.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from app.core.dependencies import AuthContext, get_auth_context, get_streaming_auth_context
from app.db.models import DebateSession
from app.db.session import get_db
from app.schemas.debate_api import (
    DebateSessionResponse,
    DebateStartResponse,
    DebateUsageSummary,
)
from app.services import debate_service
from app.services.debate_events import iter_debate_sse

router = APIRouter()


def _usage_summary(session: DebateSession) -> DebateUsageSummary:
    total_tokens_input = 0
    total_tokens_output = 0
    total_cost_usd = 0.0
    unknown = 0
    for row in (*session.challenges, *session.responses):
        if row.tokens_input is None and row.tokens_output is None and row.cost_usd is None:
            unknown += 1
            continue
        total_tokens_input += row.tokens_input or 0
        total_tokens_output += row.tokens_output or 0
        total_cost_usd += row.cost_usd or 0.0
    return DebateUsageSummary(
        total_tokens_input=total_tokens_input,
        total_tokens_output=total_tokens_output,
        total_cost_usd=total_cost_usd,
        calls_with_unknown_usage=unknown,
    )


def _to_response(session: DebateSession) -> DebateSessionResponse:
    return DebateSessionResponse(
        id=session.id,
        turn_id=session.turn_id,
        status=session.status.value,
        model_ids=session.model_ids,
        agreements=session.agreements,
        unchallenged_model_ids=session.unchallenged_model_ids,
        unresolved_disagreements=session.unresolved_disagreements,
        corrections=session.corrections,
        risks=session.risks,
        uncertainties=session.uncertainties,
        final_summary=session.final_summary,
        error_message=session.error_message,
        started_at=session.started_at,
        completed_at=session.completed_at,
        created_at=session.created_at,
        challenges=list(session.challenges),
        responses=list(session.responses),
        usage=_usage_summary(session),
    )


@router.post(
    "/chats/turns/{turn_id}/debate",
    response_model=DebateStartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_debate(
    turn_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Repeated calls while a debate is active return the existing session
    (``freshly_started=False``) instead of starting a second one."""
    session, freshly_started = await debate_service.start_and_dispatch_debate(db, auth, str(turn_id))
    full = await debate_service.get_debate_session_by_id(db, auth, session.id)
    return DebateStartResponse(session=_to_response(full), freshly_started=freshly_started)


@router.get("/chats/turns/{turn_id}/debate", response_model=DebateSessionResponse)
async def get_latest_debate_for_turn(
    turn_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    session = await debate_service.get_latest_debate_session(db, auth, str(turn_id))
    return _to_response(session)


@router.get("/debate-sessions/{session_id}", response_model=DebateSessionResponse)
async def get_debate_session(
    session_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    session = await debate_service.get_debate_session_by_id(db, auth, str(session_id))
    return _to_response(session)


@router.post("/debate-sessions/{session_id}/cancel", response_model=DebateSessionResponse)
async def cancel_debate_session(
    session_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await debate_service.cancel_debate_session(db, auth, str(session_id))
    full = await debate_service.get_debate_session_by_id(db, auth, str(session_id))
    return _to_response(full)


@router.get("/debate-sessions/{session_id}/stream")
async def stream_debate_session(
    session_id: UUID,
    request: Request,
    auth: AuthContext = Depends(get_streaming_auth_context),
):
    """Authenticated SSE stream, scoped to one session. Org ownership is
    checked once up front (never-ending responses can't keep re-checking a
    short-lived DB session — see get_streaming_auth_context) with a cheap
    existence+ownership read, independent of the long-lived subscription
    below. Any number of tabs may open this at once; each gets its own
    Redis subscription. Reconnecting clients should call the GET endpoint
    above first for the durable snapshot, then resume streaming here.
    """
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await debate_service.get_debate_session_by_id(db, auth, str(session_id))

    return StreamingResponse(
        iter_debate_sse(str(session_id), request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
