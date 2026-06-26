"""Enterprise audit logging — immutable activity trail for compliance and admin oversight."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db.models import AuditLog, AuditSeverity, User

SKIP_PATH_PREFIXES = ("/docs", "/redoc", "/openapi.json", "/health")
CATEGORY_MAP: dict[str, str] = {
    "auth": "auth",
    "admin": "admin",
    "chats": "chat",
    "lessons": "lesson",
    "brain": "brain",
    "models": "models",
    "projects": "projects",
    "templates": "templates",
    "model-sets": "models",
    "costs": "billing",
    "share": "share",
}


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def _derive_category(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1":
        return CATEGORY_MAP.get(parts[2], "api")
    return "api"


def _derive_action(method: str, path: str) -> str:
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "v1":
        resource = parts[2].replace("-", "_")
        if len(parts) == 3:
            return f"{resource}.{method.lower()}"
        tail = parts[3]
        if tail in {"overview", "members", "usage", "search", "summary", "pricing", "session"}:
            return f"{resource}.{tail}"
        if len(parts) >= 4 and parts[3] == "turns":
            return f"chat.turn.{method.lower()}"
        return f"{resource}.{method.lower()}"
    return f"http.{method.lower()}"


def _derive_summary(method: str, path: str, status: int, actor_email: str) -> str:
    who = actor_email or "anonymous"
    return f"{who} {method} {path} -> {status}"


async def _resolve_actor_from_request(request: Request, db: AsyncSession) -> tuple[str | None, str, str, str | None]:
    auth_header = request.headers.get("authorization", "")
    org_id = request.headers.get("x-org-id")
    if not auth_header.lower().startswith("bearer "):
        return None, "", "", org_id

    token = auth_header.split(" ", 1)[1]
    try:
        payload = decode_access_token(token)
    except ValueError:
        return None, "", "", org_id

    user_id = payload.get("sub")
    if not user_id:
        return None, "", "", org_id

    result = await db.execute(select(User).where(User.id == str(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        return str(user_id), "", "", org_id
    return user.id, user.email, user.full_name, org_id


class AuditService:
    async def record(
        self,
        db: AsyncSession,
        *,
        org_id: str | None,
        action: str,
        category: str,
        summary: str,
        actor_user_id: str | None = None,
        actor_email: str = "",
        actor_name: str = "",
        severity: AuditSeverity = AuditSeverity.INFO,
        resource_type: str | None = None,
        resource_id: str | None = None,
        target_user_id: str | None = None,
        target_user_email: str | None = None,
        metadata: dict[str, Any] | None = None,
        http_method: str | None = None,
        http_path: str | None = None,
        http_status: int | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            id=str(uuid.uuid4()),
            org_id=org_id,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            actor_name=actor_name,
            action=action,
            category=category,
            severity=severity,
            resource_type=resource_type,
            resource_id=resource_id,
            target_user_id=target_user_id,
            target_user_email=target_user_email,
            summary=summary,
            metadata_=metadata,
            http_method=http_method,
            http_path=http_path,
            http_status=http_status,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.add(entry)
        await db.flush()
        return entry

    async def record_http(self, db: AsyncSession, request: Request, status_code: int) -> None:
        path = request.url.path
        if not path.startswith("/api/v1"):
            return
        if any(path.startswith(prefix) for prefix in SKIP_PATH_PREFIXES):
            return
        if request.method == "OPTIONS":
            return

        actor_id, actor_email, actor_name, org_id = await _resolve_actor_from_request(request, db)
        method = request.method
        category = _derive_category(path)
        action = _derive_action(method, path)
        severity = AuditSeverity.WARNING if status_code >= 400 else AuditSeverity.INFO
        if status_code >= 500:
            severity = AuditSeverity.CRITICAL

        query = dict(request.query_params)
        metadata: dict[str, Any] = {}
        if query:
            metadata["query"] = query

        await self.record(
            db,
            org_id=org_id,
            actor_user_id=actor_id,
            actor_email=actor_email,
            actor_name=actor_name,
            action=action,
            category=category,
            severity=severity,
            summary=_derive_summary(method, path, status_code, actor_email),
            metadata=metadata or None,
            http_method=method,
            http_path=path,
            http_status=status_code,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )

    async def record_auth_sign_in(
        self,
        db: AsyncSession,
        *,
        org_id: str,
        user: User,
        ip_address: str | None = None,
        user_agent: str | None = None,
        success: bool = True,
    ) -> None:
        await self.record(
            db,
            org_id=org_id,
            actor_user_id=user.id,
            actor_email=user.email,
            actor_name=user.full_name,
            action="auth.sign_in" if success else "auth.sign_in_failed",
            category="auth",
            severity=AuditSeverity.INFO if success else AuditSeverity.WARNING,
            summary=f"{user.email} signed in" if success else f"Failed sign-in attempt for {user.email}",
            resource_type="user",
            resource_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def record_admin_member(
        self,
        db: AsyncSession,
        *,
        org_id: str,
        actor: User,
        action: str,
        target_user: User,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self.record(
            db,
            org_id=org_id,
            actor_user_id=actor.id,
            actor_email=actor.email,
            actor_name=actor.full_name,
            action=action,
            category="admin",
            severity=AuditSeverity.WARNING,
            resource_type="user",
            resource_id=target_user.id,
            target_user_id=target_user.id,
            target_user_email=target_user.email,
            summary=summary,
            metadata=metadata,
        )

    async def query_logs(
        self,
        db: AsyncSession,
        org_id: str,
        *,
        q: str | None = None,
        category: str | None = None,
        action: str | None = None,
        actor_user_id: str | None = None,
        target_user_id: str | None = None,
        severity: str | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[list[AuditLog], int]:
        statement = select(AuditLog).where(AuditLog.org_id == org_id)
        count_stmt = select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org_id)

        if category:
            statement = statement.where(AuditLog.category == category)
            count_stmt = count_stmt.where(AuditLog.category == category)
        if action:
            statement = statement.where(AuditLog.action == action)
            count_stmt = count_stmt.where(AuditLog.action == action)
        if actor_user_id:
            statement = statement.where(AuditLog.actor_user_id == actor_user_id)
            count_stmt = count_stmt.where(AuditLog.actor_user_id == actor_user_id)
        if target_user_id:
            statement = statement.where(AuditLog.target_user_id == target_user_id)
            count_stmt = count_stmt.where(AuditLog.target_user_id == target_user_id)
        if severity:
            try:
                sev = AuditSeverity(severity)
                statement = statement.where(AuditLog.severity == sev)
                count_stmt = count_stmt.where(AuditLog.severity == sev)
            except ValueError:
                pass
        if from_dt:
            statement = statement.where(AuditLog.created_at >= from_dt)
            count_stmt = count_stmt.where(AuditLog.created_at >= from_dt)
        if to_dt:
            statement = statement.where(AuditLog.created_at <= to_dt)
            count_stmt = count_stmt.where(AuditLog.created_at <= to_dt)
        if q:
            pattern = f"%{q.strip()}%"
            filt = or_(
                AuditLog.summary.ilike(pattern),
                AuditLog.actor_email.ilike(pattern),
                AuditLog.actor_name.ilike(pattern),
                AuditLog.action.ilike(pattern),
                AuditLog.target_user_email.ilike(pattern),
            )
            statement = statement.where(filt)
            count_stmt = count_stmt.where(filt)

        total = int((await db.execute(count_stmt)).scalar_one() or 0)
        offset = max(0, (page - 1) * limit)
        statement = (
            statement.order_by(AuditLog.created_at.desc()).offset(offset).limit(min(limit, 200))
        )
        rows = list((await db.execute(statement)).scalars().all())
        return rows, total

    async def stats(self, db: AsyncSession, org_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        day_ago = now - timedelta(hours=24)
        week_ago = now - timedelta(days=7)

        total = await db.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.org_id == org_id)
        )
        last_24h = await db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.org_id == org_id, AuditLog.created_at >= day_ago)
        )
        last_7d = await db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.org_id == org_id, AuditLog.created_at >= week_ago)
        )
        critical = await db.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.org_id == org_id, AuditLog.severity == AuditSeverity.CRITICAL)
        )

        by_category = await db.execute(
            select(AuditLog.category, func.count())
            .where(AuditLog.org_id == org_id, AuditLog.created_at >= week_ago)
            .group_by(AuditLog.category)
            .order_by(func.count().desc())
        )
        top_actions = await db.execute(
            select(AuditLog.action, func.count())
            .where(AuditLog.org_id == org_id, AuditLog.created_at >= week_ago)
            .group_by(AuditLog.action)
            .order_by(func.count().desc())
            .limit(10)
        )

        return {
            "total": int(total or 0),
            "last_24h": int(last_24h or 0),
            "last_7d": int(last_7d or 0),
            "critical": int(critical or 0),
            "by_category": [{"category": c, "count": n} for c, n in by_category.all()],
            "top_actions": [{"action": a, "count": n} for a, n in top_actions.all()],
        }


audit_service = AuditService()
