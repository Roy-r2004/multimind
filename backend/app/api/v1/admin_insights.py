"""Extended admin endpoints — audit logs, user intelligence, org-wide content inspection."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, require_org_admin
from app.db.session import get_db
from app.schemas.api import (
    AdminAuditLogListResponse,
    AdminAuditLogResponse,
    AdminAuditStatsResponse,
    AdminBrainDetailResponse,
    AdminBrainSummaryResponse,
    AdminChatDetailResponse,
    AdminChatSummaryResponse,
    AdminLessonSummaryResponse,
    AdminProjectSummaryResponse,
    AdminUserDetailResponse,
    AdminUserSummaryResponse,
)
from app.services.admin_service import admin_service
from app.services.audit_service import audit_service

router = APIRouter()


def _audit_response(log) -> AdminAuditLogResponse:
    return AdminAuditLogResponse(
        id=log.id,
        org_id=log.org_id,
        actor_user_id=log.actor_user_id,
        actor_email=log.actor_email,
        actor_name=log.actor_name,
        action=log.action,
        category=log.category,
        severity=log.severity.value,
        resource_type=log.resource_type,
        resource_id=log.resource_id,
        target_user_id=log.target_user_id,
        target_user_email=log.target_user_email,
        summary=log.summary,
        metadata=log.metadata_,
        http_method=log.http_method,
        http_path=log.http_path,
        http_status=log.http_status,
        ip_address=log.ip_address,
        user_agent=log.user_agent,
        created_at=log.created_at,
    )


@router.get("/audit-logs", response_model=AdminAuditLogListResponse)
async def admin_audit_logs(
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
    q: str | None = None,
    category: str | None = None,
    action: str | None = None,
    actor_user_id: str | None = None,
    target_user_id: str | None = None,
    severity: str | None = None,
    from_dt: datetime | None = Query(default=None, alias="from"),
    to_dt: datetime | None = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
):
    logs, total = await audit_service.query_logs(
        db,
        auth.org_id,
        q=q,
        category=category,
        action=action,
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        severity=severity,
        from_dt=from_dt,
        to_dt=to_dt,
        page=page,
        limit=limit,
    )
    return AdminAuditLogListResponse(
        items=[_audit_response(log) for log in logs],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/audit-logs/stats", response_model=AdminAuditStatsResponse)
async def admin_audit_stats(
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    stats = await audit_service.stats(db, auth.org_id)
    return AdminAuditStatsResponse(**stats)


@router.get("/users", response_model=list[AdminUserSummaryResponse])
async def admin_users(
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = await admin_service.list_users(db, auth)
    return [AdminUserSummaryResponse(**row) for row in rows]


@router.get("/users/{user_id}", response_model=AdminUserDetailResponse)
async def admin_user_detail(
    user_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await admin_service.get_user(db, auth, user_id)
    return AdminUserDetailResponse(**row)


@router.get("/users/{user_id}/chats", response_model=list[AdminChatSummaryResponse])
async def admin_user_chats(
    user_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = await admin_service.list_user_chats(db, auth, user_id)
    return [AdminChatSummaryResponse(**row) for row in rows]


@router.get("/users/{user_id}/activity", response_model=AdminAuditLogListResponse)
async def admin_user_activity(
    user_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
):
    logs, total = await audit_service.query_logs(
        db,
        auth.org_id,
        actor_user_id=user_id,
        page=page,
        limit=limit,
    )
    return AdminAuditLogListResponse(
        items=[_audit_response(log) for log in logs],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/chats", response_model=list[AdminChatSummaryResponse])
async def admin_chats(
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
    user_id: str | None = None,
    q: str | None = None,
):
    rows = await admin_service.list_org_chats(db, auth, user_id=user_id, q=q)
    return [AdminChatSummaryResponse(**row) for row in rows]


@router.get("/chats/{chat_id}", response_model=AdminChatDetailResponse)
async def admin_chat_detail(
    chat_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await admin_service.get_chat_detail(db, auth, chat_id)
    return AdminChatDetailResponse(**row)


@router.get("/brains", response_model=list[AdminBrainSummaryResponse])
async def admin_brains(
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = await admin_service.list_brains(db, auth)
    return [AdminBrainSummaryResponse(**row) for row in rows]


@router.get("/brains/{user_id}", response_model=AdminBrainDetailResponse)
async def admin_brain_detail(
    user_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    row = await admin_service.get_brain(db, auth, user_id)
    return AdminBrainDetailResponse(**row)


@router.get("/lessons", response_model=list[AdminLessonSummaryResponse])
async def admin_lessons(
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = await admin_service.list_lessons(db, auth)
    return [AdminLessonSummaryResponse(**row) for row in rows]


@router.get("/projects", response_model=list[AdminProjectSummaryResponse])
async def admin_projects(
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = await admin_service.list_projects(db, auth)
    return [AdminProjectSummaryResponse(**row) for row in rows]


@router.get("/security/events", response_model=AdminAuditLogListResponse)
async def admin_security_events(
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
):
    logs, total = await audit_service.query_logs(
        db,
        auth.org_id,
        category="auth",
        page=page,
        limit=limit,
    )
    return AdminAuditLogListResponse(
        items=[_audit_response(log) for log in logs],
        total=total,
        page=page,
        limit=limit,
    )
