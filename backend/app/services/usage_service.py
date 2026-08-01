"""User-scoped and org-scoped AI usage aggregations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.exceptions import ForbiddenError
from app.db.models import Chat, CostRecord, Project, ScrapingMission

Period = Literal["7d", "30d", "90d", "all"]
GroupBy = Literal["model", "kind", "operation", "status", "project", "cost_source"]


def _period_start(period: Period) -> datetime | None:
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "7d":
        return day_start - timedelta(days=6)
    if period == "30d":
        return day_start - timedelta(days=29)
    if period == "90d":
        return day_start - timedelta(days=89)
    return None


class UsageService:
    def _user_base(self, auth: AuthContext) -> list[Any]:
        return [
            CostRecord.org_id == auth.org_id,
            CostRecord.user_id == auth.user.id,
        ]

    def _org_base(self, auth: AuthContext) -> list[Any]:
        return [CostRecord.org_id == auth.org_id]

    async def user_summary(self, db: AsyncSession, auth: AuthContext) -> dict[str, Any]:
        now = datetime.now(UTC)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = day_start - timedelta(days=day_start.weekday())
        month_start = day_start.replace(day=1)
        filters = self._user_base(auth)

        async def sum_since(since: datetime | None) -> tuple[float, int, int]:
            stmt = select(
                func.coalesce(func.sum(CostRecord.cost_usd), 0.0),
                func.coalesce(func.sum(CostRecord.tokens_input + CostRecord.tokens_output), 0),
                func.count(CostRecord.id),
            ).where(*filters)
            if since is not None:
                stmt = stmt.where(CostRecord.recorded_at >= since)
            row = (await db.execute(stmt)).one()
            return float(row[0]), int(row[1]), int(row[2])

        today_usd, today_tokens, _ = await sum_since(day_start)
        week_usd, _, _ = await sum_since(week_start)
        month_usd, month_tokens, _ = await sum_since(month_start)
        all_time_usd, all_time_tokens, tracked_calls = await sum_since(None)

        failed = await db.execute(
            select(func.count(CostRecord.id)).where(
                *filters,
                CostRecord.status == "failed",
            )
        )
        earliest = await db.execute(
            select(func.min(CostRecord.recorded_at)).where(*filters)
        )

        return {
            "today_usd": today_usd,
            "week_usd": week_usd,
            "month_usd": month_usd,
            "all_time_usd": all_time_usd,
            "today_tokens": today_tokens,
            "month_tokens": month_tokens,
            "all_time_tokens": all_time_tokens,
            "tracked_calls": tracked_calls,
            "failed_calls": int(failed.scalar_one() or 0),
            "earliest_recorded_at": earliest.scalar_one_or_none(),
            "historical_notice": True,
            "currency": "USD",
        }

    async def user_timeseries(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        period: Period = "30d",
    ) -> list[dict[str, Any]]:
        filters = self._user_base(auth)
        since = _period_start(period)
        day_col = func.date(CostRecord.recorded_at)
        stmt = (
            select(
                day_col.label("day"),
                func.coalesce(func.sum(CostRecord.cost_usd), 0.0),
                func.coalesce(func.sum(CostRecord.tokens_input + CostRecord.tokens_output), 0),
                func.count(CostRecord.id),
            )
            .where(*filters)
            .group_by(day_col)
            .order_by(day_col)
        )
        if since is not None:
            stmt = stmt.where(CostRecord.recorded_at >= since)
        rows = (await db.execute(stmt)).all()
        return [
            {
                "date": str(row[0]),
                "cost_usd": float(row[1]),
                "tokens": int(row[2]),
                "call_count": int(row[3]),
            }
            for row in rows
        ]

    async def user_breakdown(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        group_by: GroupBy = "model",
        period: Period = "30d",
    ) -> list[dict[str, Any]]:
        filters = self._user_base(auth)
        since = _period_start(period)
        column_map = {
            "model": CostRecord.model_id,
            "kind": CostRecord.kind,
            "operation": CostRecord.operation,
            "status": CostRecord.status,
            "project": CostRecord.project_id,
            "cost_source": CostRecord.cost_source,
        }
        col = column_map[group_by]
        stmt = (
            select(
                col.label("key"),
                func.coalesce(func.sum(CostRecord.cost_usd), 0.0),
                func.coalesce(func.sum(CostRecord.tokens_input + CostRecord.tokens_output), 0),
                func.count(CostRecord.id),
            )
            .where(*filters)
            .group_by(col)
            .order_by(func.sum(CostRecord.cost_usd).desc())
        )
        if since is not None:
            stmt = stmt.where(CostRecord.recorded_at >= since)
        rows = (await db.execute(stmt)).all()
        return [
            {
                "key": (row[0].value if hasattr(row[0], "value") else row[0]) or "unknown",
                "cost_usd": float(row[1]),
                "tokens": int(row[2]),
                "call_count": int(row[3]),
            }
            for row in rows
        ]

    async def _assert_user_chat(self, db: AsyncSession, auth: AuthContext, chat_id: str) -> None:
        chat = await db.get(Chat, chat_id)
        if (
            chat is None
            or chat.org_id != auth.org_id
            or chat.created_by != auth.user.id
        ):
            raise ForbiddenError("Chat not found or not owned by you")

    async def _assert_user_project(
        self, db: AsyncSession, auth: AuthContext, project_id: str
    ) -> None:
        project = await db.get(Project, project_id)
        if project is None or project.org_id != auth.org_id:
            raise ForbiddenError("Project not found")

    async def _assert_user_mission(
        self, db: AsyncSession, auth: AuthContext, mission_id: str
    ) -> None:
        mission = await db.get(ScrapingMission, mission_id)
        if (
            mission is None
            or mission.org_id != auth.org_id
            or mission.created_by != auth.user.id
        ):
            raise ForbiddenError("Mission not found or not owned by you")

    async def user_records(
        self,
        db: AsyncSession,
        auth: AuthContext,
        *,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        model_id: str | None = None,
        kind: str | None = None,
        operation: str | None = None,
        status: str | None = None,
        project_id: str | None = None,
        chat_id: str | None = None,
        mission_id: str | None = None,
        page: int = 1,
        limit: int = 25,
    ) -> dict[str, Any]:
        page = max(1, page)
        limit = min(100, max(1, limit))
        if chat_id:
            await self._assert_user_chat(db, auth, chat_id)
        if project_id:
            await self._assert_user_project(db, auth, project_id)
        if mission_id:
            await self._assert_user_mission(db, auth, mission_id)

        filters = self._user_base(auth)
        stmt: Select[Any] = select(CostRecord).where(*filters)
        count_stmt = select(func.count(CostRecord.id)).where(*filters)

        extra: list[Any] = []
        if date_from is not None:
            extra.append(CostRecord.recorded_at >= date_from)
        if date_to is not None:
            extra.append(CostRecord.recorded_at <= date_to)
        if model_id:
            extra.append(CostRecord.model_id == model_id)
        if kind:
            extra.append(CostRecord.kind == kind)
        if operation:
            extra.append(CostRecord.operation == operation)
        if status:
            extra.append(CostRecord.status == status)
        if project_id:
            extra.append(CostRecord.project_id == project_id)
        if chat_id:
            extra.append(CostRecord.chat_id == chat_id)
        if mission_id:
            extra.append(CostRecord.mission_id == mission_id)

        if extra:
            stmt = stmt.where(*extra)
            count_stmt = count_stmt.where(*extra)

        total = int((await db.execute(count_stmt)).scalar_one() or 0)
        rows = (
            await db.execute(
                stmt.order_by(CostRecord.recorded_at.desc())
                .offset((page - 1) * limit)
                .limit(limit)
            )
        ).scalars().all()

        items = [
            {
                "id": r.id,
                "recorded_at": r.recorded_at,
                "model_id": r.model_id,
                "provider": r.provider,
                "kind": r.kind.value if hasattr(r.kind, "value") else r.kind,
                "operation": r.operation,
                "status": r.status,
                "tokens_input": r.tokens_input,
                "tokens_output": r.tokens_output,
                "tokens_reasoning": r.tokens_reasoning,
                "tokens_cached_input": r.tokens_cached_input,
                "tokens_total": (r.tokens_input or 0) + (r.tokens_output or 0),
                "cost_usd": float(r.cost_usd or 0),
                "cost_source": r.cost_source,
                "latency_ms": r.latency_ms,
                "project_id": r.project_id,
                "chat_id": r.chat_id,
                "turn_id": r.turn_id,
                "mission_id": r.mission_id,
                "execution_id": r.execution_id,
                "request_id": r.request_id,
                "error_code": r.error_code,
            }
            for r in rows
        ]
        return {"items": items, "page": page, "limit": limit, "total": total}

    async def org_extras(self, db: AsyncSession, auth: AuthContext) -> dict[str, Any]:
        """Extra org-wide fields for Admin Usage (includes unattributable rows)."""
        filters = self._org_base(auth)
        all_time = await db.execute(
            select(func.coalesce(func.sum(CostRecord.cost_usd), 0.0)).where(*filters)
        )
        failed = await db.execute(
            select(func.count(CostRecord.id)).where(*filters, CostRecord.status == "failed")
        )
        by_user_rows = (
            await db.execute(
                select(
                    CostRecord.user_id,
                    func.coalesce(func.sum(CostRecord.cost_usd), 0.0),
                    func.count(CostRecord.id),
                )
                .where(*filters)
                .group_by(CostRecord.user_id)
                .order_by(func.sum(CostRecord.cost_usd).desc())
            )
        ).all()
        return {
            "all_time_usd": float(all_time.scalar_one() or 0),
            "failed_calls": int(failed.scalar_one() or 0),
            "by_user": [
                {
                    "user_id": row[0] or "unattributed",
                    "cost_usd": float(row[1]),
                    "call_count": int(row[2]),
                }
                for row in by_user_rows
            ],
        }


usage_service = UsageService()
