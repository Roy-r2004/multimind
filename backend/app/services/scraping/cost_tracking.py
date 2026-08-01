"""Helpers to record OpenRouter spend from scraping / maps services."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScrapingExecution, ScrapingMission, UsageKind
from app.llm.providers import LLMResponse
from app.services.cost_recorder import cost_recorder


@dataclass(frozen=True)
class ScrapingCostContext:
    """Attribution context for background / scraping OpenRouter calls."""

    org_id: str
    user_id: str | None = None
    mission_id: str | None = None
    execution_id: str | None = None


async def resolve_mission_owner_user_id(
    db: AsyncSession,
    *,
    mission_id: str | None = None,
    execution_id: str | None = None,
) -> tuple[str | None, str | None]:
    """Return ``(user_id, mission_id)`` from mission ownership when reliable.

    Prefer mission.created_by. If only an execution is known, resolve through
    the execution's mission. When ownership cannot be resolved, return
    ``(None, mission_id_or_none)`` so spend stays Admin-visible only.
    """
    resolved_mission_id = mission_id
    if resolved_mission_id is None and execution_id:
        execution = await db.get(ScrapingExecution, execution_id)
        if execution is not None:
            resolved_mission_id = execution.mission_id
    if not resolved_mission_id:
        return None, None
    mission = await db.get(ScrapingMission, resolved_mission_id)
    if mission is None:
        return None, resolved_mission_id
    return mission.created_by, resolved_mission_id


async def record_scraping_llm(
    db: AsyncSession | None,
    *,
    org_id: str | None,
    user_id: str | None,
    model_id: str,
    kind: UsageKind,
    operation: str,
    idempotency_key: str,
    response: LLMResponse | None = None,
    mission_id: str | None = None,
    execution_id: str | None = None,
    failed: bool = False,
    error_code: str | None = None,
    latency_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not org_id:
        return

    async def _write(session: AsyncSession) -> None:
        if failed or response is None:
            await cost_recorder.record_llm_failure(
                session,
                org_id=org_id,
                user_id=user_id,
                mission_id=mission_id,
                execution_id=execution_id,
                model_id=model_id,
                kind=kind,
                operation=operation,
                idempotency_key=idempotency_key,
                error_code=error_code or "scraping_llm_failed",
                latency_ms=latency_ms,
                metadata=metadata,
            )
            return
        await cost_recorder.record_llm_success(
            session,
            org_id=org_id,
            user_id=user_id,
            mission_id=mission_id,
            execution_id=execution_id,
            model_id=model_id,
            kind=kind,
            operation=operation,
            idempotency_key=idempotency_key,
            response=response,
            latency_ms=latency_ms,
            metadata=metadata,
        )

    if db is not None:
        await _write(db)
        return

    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await _write(session)
        await session.commit()
