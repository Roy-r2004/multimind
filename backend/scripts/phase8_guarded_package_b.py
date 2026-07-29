"""Guarded, bounded Package B publication and Excel runner."""

from __future__ import annotations

import argparse
import asyncio
import json

from sqlalchemy import func, select

from app.db.models import (
    ScrapingExecution,
    ScrapingExecutionExport,
    ScrapingExecutionStatus,
    ScrapingFacilityCandidate,
    ScrapingFacilityCandidatePublication,
    ScrapingFacilityPhaseWorkJob,
)
from app.db.session import AsyncSessionLocal
from app.services.scraping.facility_package_b_service import (
    package_b_progress,
    publication_eligibility,
    seed_package_b_continuations,
)
from app.services.scraping.facility_phase_execution_service import run_work_slice


async def _owned_execution(organization_id: str, execution_id: str):
    async with AsyncSessionLocal() as db:
        execution = await db.scalar(select(ScrapingExecution).where(
            ScrapingExecution.id == execution_id,
            ScrapingExecution.organization_id == organization_id,
        ))
        if execution is None:
            raise SystemExit("ownership_mismatch")
        return execution.status.value


async def safe_summary(organization_id: str, execution_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        execution_status = await _owned_execution(
            organization_id, execution_id
        )
        candidate_ids = list((await db.scalars(
            select(ScrapingFacilityCandidate.id).where(
                ScrapingFacilityCandidate.organization_id == organization_id,
                ScrapingFacilityCandidate.execution_id == execution_id,
            ).order_by(ScrapingFacilityCandidate.id)
        )).all())
        reasons: dict[str, int] = {}
        eligible = 0
        existing_links = 0
        for candidate_id in candidate_ids:
            policy = await publication_eligibility(
                db, organization_id=organization_id, execution_id=execution_id,
                candidate_id=candidate_id,
            )
            reasons[policy.reason] = reasons.get(policy.reason, 0) + 1
            eligible += int(policy.eligible)
            existing_links += int(policy.existing_facility_id is not None)
        jobs = {
            f"{kind}:{status}": count
            for kind, status, count in (await db.execute(
                select(
                    ScrapingFacilityPhaseWorkJob.work_kind,
                    ScrapingFacilityPhaseWorkJob.status,
                    func.count(ScrapingFacilityPhaseWorkJob.id),
                ).where(
                    ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
                    ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
                    ScrapingFacilityPhaseWorkJob.work_kind.in_({
                        "publish_candidate", "generate_execution_export",
                        "finalize_execution",
                    }),
                ).group_by(
                    ScrapingFacilityPhaseWorkJob.work_kind,
                    ScrapingFacilityPhaseWorkJob.status,
                )
            )).all()
        }
        progress = await package_b_progress(
            db, organization_id=organization_id, execution_id=execution_id
        )
        export_status = await db.scalar(select(ScrapingExecutionExport.status).where(
            ScrapingExecutionExport.organization_id == organization_id,
            ScrapingExecutionExport.execution_id == execution_id,
        ))
        return {
            "organization_id": organization_id,
            "execution_id": execution_id,
            "execution_status": execution_status,
            "candidate_counts": {
                key: progress.counts[key]
                for key in ("candidates", "accepted", "needs_review", "rejected")
            },
            "eligible_publication_count": eligible,
            "eligibility_reasons": reasons,
            "existing_published_facility_links": existing_links,
            "publication_jobs_by_kind_and_status": jobs,
            "published_facilities": progress.counts["published"],
            "existing_facility_reuse": progress.counts["existing_facility_reused"],
            "export_status": export_status or "not_created",
            "completion_status": progress.state,
            "publication_invoked": False,
            "excel_invoked": False,
            "worker_started": False,
        }


async def run(args) -> None:
    status = await _owned_execution(args.organization_id, args.execution_id)
    if not (
        args.confirm_publication
        or args.confirm_excel
        or args.confirm_finalization
    ):
        print(json.dumps(
            await safe_summary(args.organization_id, args.execution_id),
            sort_keys=True, separators=(",", ":"),
        ))
        return
    if status != ScrapingExecutionStatus.PAUSED.value:
        raise SystemExit("execution_not_paused")
    if args.confirm_publication:
        async with AsyncSessionLocal() as db:
            seeded = await seed_package_b_continuations(
                db, organization_id=args.organization_id,
                execution_id=args.execution_id,
            )
            processed = await run_work_slice(
                db, organization_id=args.organization_id,
                execution_id=args.execution_id,
                work_kinds={"publish_candidate"},
            )
        action = {"publication_seed": seeded, "publication_slice": processed}
    elif args.confirm_excel:
        async with AsyncSessionLocal() as db:
            seeded = await seed_package_b_continuations(
                db, organization_id=args.organization_id,
                execution_id=args.execution_id,
            )
            processed = await run_work_slice(
                db, organization_id=args.organization_id,
                execution_id=args.execution_id,
                work_kinds={"generate_execution_export"},
            )
        action = {"export_seed": seeded, "export_slice": processed}
    else:
        async with AsyncSessionLocal() as db:
            seeded = await seed_package_b_continuations(
                db, organization_id=args.organization_id,
                execution_id=args.execution_id,
            )
            processed = await run_work_slice(
                db, organization_id=args.organization_id,
                execution_id=args.execution_id,
                work_kinds={"finalize_execution"},
            )
        action = {
            "finalization_seed": seeded,
            "finalization_slice": processed,
        }
    summary = await safe_summary(args.organization_id, args.execution_id)
    summary.update(action)
    summary["publication_invoked"] = bool(args.confirm_publication)
    summary["excel_invoked"] = bool(args.confirm_excel)
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--execution-id", required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--confirm-publication", action="store_true")
    action.add_argument("--confirm-excel", action="store_true")
    action.add_argument("--confirm-finalization", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
