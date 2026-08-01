"""Authenticated user-scoped AI usage endpoints."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    UserUsageBreakdownResponse,
    UserUsageRecordsResponse,
    UserUsageSummaryResponse,
    UserUsageTimeseriesResponse,
    UsageBreakdownItem,
    UsageTimeseriesPoint,
    UserUsageRecordItem,
)
from app.services.usage_service import usage_service

router = APIRouter()


@router.get("/summary", response_model=UserUsageSummaryResponse)
async def usage_summary(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    data = await usage_service.user_summary(db, auth)
    return UserUsageSummaryResponse(**data)


@router.get("/timeseries", response_model=UserUsageTimeseriesResponse)
async def usage_timeseries(
    period: Literal["7d", "30d", "90d", "all"] = Query("30d"),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    points = await usage_service.user_timeseries(db, auth, period=period)
    return UserUsageTimeseriesResponse(
        period=period,
        points=[UsageTimeseriesPoint(**p) for p in points],
    )


@router.get("/breakdown", response_model=UserUsageBreakdownResponse)
async def usage_breakdown(
    group_by: Literal["model", "kind", "operation", "status", "project", "cost_source"] = Query(
        "model"
    ),
    period: Literal["7d", "30d", "90d", "all"] = Query("30d"),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    items = await usage_service.user_breakdown(db, auth, group_by=group_by, period=period)
    return UserUsageBreakdownResponse(
        group_by=group_by,
        period=period,
        items=[UsageBreakdownItem(**i) for i in items],
    )


@router.get("/records", response_model=UserUsageRecordsResponse)
async def usage_records(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    model_id: str | None = None,
    kind: str | None = None,
    operation: str | None = None,
    status: str | None = None,
    project_id: str | None = None,
    chat_id: str | None = None,
    mission_id: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    data = await usage_service.user_records(
        db,
        auth,
        date_from=date_from,
        date_to=date_to,
        model_id=model_id,
        kind=kind,
        operation=operation,
        status=status,
        project_id=project_id,
        chat_id=chat_id,
        mission_id=mission_id,
        page=page,
        limit=limit,
    )
    return UserUsageRecordsResponse(
        items=[UserUsageRecordItem(**item) for item in data["items"]],
        page=data["page"],
        limit=data["limit"],
        total=data["total"],
    )
