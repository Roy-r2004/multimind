"""Resume extraction + publication on a failed execution that already has documents.

Usage (from backend/):
  python -m scripts.resume_facility_phases <execution_id> [--max-docs 120]
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.models import ScrapingExecution, ScrapingExecutionStatus, ScrapingRun
from app.db.session import AsyncSessionLocal
from app.services.scraping.execution_orchestrator import SourceDiscoveryExecutionOrchestrator
from app.services.scraping.execution_service import execution_service
from app.services.scraping.scale_profile import (
    resolve_dynamic_scale_profile,
    resolve_scale_profile,
    scale_profile_from_country_profile,
)
from app.core.config import get_settings


async def resume(execution_id: str, max_docs: int | None) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ScrapingExecution)
            .where(ScrapingExecution.id == execution_id)
            .options(
                selectinload(ScrapingExecution.blueprint),
                selectinload(ScrapingExecution.team_plan).selectinload(ScrapingRun.agents),
            )
        )
        execution = result.scalar_one_or_none()
        if execution is None:
            raise SystemExit(f"Execution not found: {execution_id}")

        orchestrator = SourceDiscoveryExecutionOrchestrator(db)
        settings = get_settings()
        restored = scale_profile_from_country_profile(
            execution.mode, settings, execution.country_profile_json
        )
        if restored is None:
            profile_json = execution.country_profile_json or {}
            regions = profile_json.get("administrative_regions") or []
            languages = profile_json.get("languages") or []
            categories = profile_json.get("source_categories") or []
            restored = resolve_dynamic_scale_profile(
                execution.mode,
                settings,
                cell_count=len(regions) * len(languages) * len(categories),
                expected_pages=profile_json.get("expected_pages"),
            )
        if max_docs is not None:
            docs = max(int(max_docs), 1)
            restored = replace(
                restored,
                extraction_max_documents=docs,
                extraction_max_chunks=max(docs * 3, 1),
                publication_max_candidates=max(docs * 4, 1),
            )
        orchestrator.scale_profile = restored or resolve_scale_profile(execution.mode, settings)

        execution.status = ScrapingExecutionStatus.RUNNING
        execution.error_message = None
        execution.completed_at = None
        execution.heartbeat_at = datetime.now(UTC)
        await execution_service.emit_event(
            db,
            execution.id,
            "execution_started",
            "Resuming facility extraction and publication after discovery/retrieval.",
            metadata={
                "resume": True,
                "extraction_max_documents": orchestrator.scale_profile.extraction_max_documents,
                "publication_max_candidates": orchestrator.scale_profile.publication_max_candidates,
            },
        )
        await db.commit()

        print(
            "Resuming",
            execution.id,
            "extract_docs=",
            orchestrator.scale_profile.extraction_max_documents,
            flush=True,
        )
        await orchestrator._run_facility_extraction_phase(execution)
        await orchestrator._check_cancelled(execution)
        await orchestrator._run_facility_publication_phase(execution)
        await orchestrator._refresh_metrics(execution)

        execution.status = ScrapingExecutionStatus.COMPLETED
        execution.completed_at = datetime.now(UTC)
        execution.error_message = None
        await execution_service.emit_event(
            db,
            execution.id,
            "execution_completed",
            "Resumed facility extraction and publication completed.",
        )
        await db.commit()
        print(
            "DONE",
            "sources=",
            execution.sources_discovered,
            "docs=",
            execution.documents_found,
            "facilities=",
            execution.records_verified,
            flush=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("execution_id")
    parser.add_argument(
        "--max-docs",
        type=int,
        default=120,
        help="Cap extraction docs for a faster demo (default 120).",
    )
    args = parser.parse_args()
    asyncio.run(resume(args.execution_id, args.max_docs))


if __name__ == "__main__":
    main()
