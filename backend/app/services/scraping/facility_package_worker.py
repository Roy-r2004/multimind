"""Registered restart-safe worker entry point for the real facility pipeline."""

from __future__ import annotations

from sqlalchemy import func, select

from app.db.models import (
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingFacilityPhaseWorkJob,
)
from app.db.session import AsyncSessionLocal
from app.services.scraping.execution_service import execution_service
from app.services.scraping.facility_package_b_service import (
    package_b_progress,
    seed_package_b_continuations,
)
from app.services.scraping.facility_phase_execution_service import run_work_slice
from app.services.scraping.facility_phase_orchestration_service import (
    seed_document_preparation,
)


async def run_facility_package_pipeline(ctx: dict, execution_id: str) -> None:
    """Run one bounded real Package A/B slice and requeue from persisted state."""
    del ctx
    async with AsyncSessionLocal() as db:
        execution = await db.scalar(select(ScrapingExecution).where(
            ScrapingExecution.id == execution_id
        ))
        if (
            execution is None
            or execution.mode != "real"
            or execution.status not in {
                ScrapingExecutionStatus.QUEUED,
                ScrapingExecutionStatus.RUNNING,
            }
        ):
            return
        organization_id = execution.organization_id
        await seed_document_preparation(
            db, organization_id=organization_id, execution_id=execution_id
        )
        await seed_package_b_continuations(
            db, organization_id=organization_id, execution_id=execution_id
        )
        await run_work_slice(
            db, organization_id=organization_id, execution_id=execution_id
        )
        progress = await package_b_progress(
            db, organization_id=organization_id, execution_id=execution_id
        )
        next_retry_at = await db.scalar(select(
            func.min(ScrapingFacilityPhaseWorkJob.next_retry_at)
        ).where(
            ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
            ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
            ScrapingFacilityPhaseWorkJob.status == "retry_scheduled",
        ))
    if not progress.terminal:
        await execution_service.enqueue_execution(
            execution_id,
            job_name="run_facility_package_pipeline",
            job_id=f"facility-package:{execution_id}",
            defer_until=next_retry_at,
        )
