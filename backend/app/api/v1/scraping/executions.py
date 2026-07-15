"""Scraping execution campaign endpoints."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import AsyncSessionLocal, get_db
from app.schemas.api import (
    ScrapingCoverageCellResponse,
    ScrapingEventResponse,
    ScrapingExecutionDetail,
    ScrapingExecutionSummary,
    ScrapingTaskResponse,
)
from app.services.scraping.execution_service import execution_service

router = APIRouter()


@router.get("/{execution_id}", response_model=ScrapingExecutionDetail)
async def get_execution(
    execution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await execution_service.get_detail(db, auth, execution_id)


@router.get("/{execution_id}/tasks", response_model=list[ScrapingTaskResponse])
async def list_tasks(
    execution_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    execution_agent_id: str | None = None,
    task_type: str | None = None,
    coverage_cell_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await execution_service.list_tasks(
        db,
        auth,
        execution_id,
        status=status_filter,
        execution_agent_id=execution_agent_id,
        task_type=task_type,
        coverage_cell_id=coverage_cell_id,
        limit=limit,
        offset=offset,
    )


@router.get("/{execution_id}/coverage", response_model=list[ScrapingCoverageCellResponse])
async def list_coverage(
    execution_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    region: str | None = None,
    language: str | None = None,
    source_category: str | None = None,
    execution_agent_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await execution_service.list_coverage(
        db,
        auth,
        execution_id,
        status=status_filter,
        region=region,
        language=language,
        source_category=source_category,
        execution_agent_id=execution_agent_id,
        limit=limit,
        offset=offset,
    )


@router.get("/{execution_id}/events", response_model=list[ScrapingEventResponse])
async def list_events(
    execution_id: str,
    after_sequence: int | None = None,
    limit: int = 200,
    execution_agent_id: str | None = None,
    event_type: str | None = None,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await execution_service.list_events(
        db,
        auth,
        execution_id,
        after_sequence=after_sequence,
        limit=limit,
        execution_agent_id=execution_agent_id,
        event_type=event_type,
    )


@router.get("/{execution_id}/events/stream")
async def stream_events(
    request: Request,
    execution_id: str,
    after_sequence: int | None = None,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await execution_service.get_detail(db, auth, execution_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        last_sequence = after_sequence or 0
        heartbeat_tick = 0
        while True:
            if await request.is_disconnected():
                return
            async with AsyncSessionLocal() as stream_db:
                events = await execution_service.list_events(
                    stream_db,
                    auth,
                    execution_id,
                    after_sequence=last_sequence,
                    limit=100,
                )
            for event in events:
                last_sequence = max(last_sequence, event.sequence_number)
                payload = json.dumps(event.model_dump(mode="json"))
                yield f"id: {event.sequence_number}\nevent: {event.event_type}\ndata: {payload}\n\n"
            heartbeat_tick += 1
            if heartbeat_tick >= 15:
                heartbeat_tick = 0
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{execution_id}/cancel", response_model=ScrapingExecutionSummary)
async def cancel_execution(
    execution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await execution_service.cancel_execution(db, auth, execution_id)


@router.delete("/{execution_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_execution(
    execution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await execution_service.delete_execution(db, auth, execution_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
