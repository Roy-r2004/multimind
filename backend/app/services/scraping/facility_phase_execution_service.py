"""Bounded Phase 6/7 worker slice. Publication is intentionally absent."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import ScrapingSourceDocumentChunk
from app.services.scraping.document_text_preparation_service import (
    SourceDocumentPreparationContext,
    document_text_preparation_service,
)
from app.services.scraping.facility_candidate_decision_service import (
    link_probable_duplicates,
    verify_candidate,
)
from app.services.scraping.facility_extraction_service import (
    FacilityExtractionContext,
    facility_extraction_service,
)
from app.services.scraping.facility_phase_job_service import (
    ClaimedFacilityWork,
    claim_batch,
    complete_claim,
    fail_claim,
    create_job,
)
from app.services.scraping.facility_phase_orchestration_service import (
    seed_candidate_verification,
    seed_chunk_extraction,
)


async def run_work_slice(
    db: AsyncSession, *, organization_id: str, execution_id: str,
) -> dict[str, int]:
    settings = get_settings()
    claims = await claim_batch(
        db, organization_id=organization_id, execution_id=execution_id,
        batch_size=settings.phase6_claim_batch_size,
        lease_duration=timedelta(seconds=settings.phase6_lease_seconds),
    )
    counts = {"claimed": len(claims), "succeeded": 0, "failed": 0, "stale": 0}
    for claim in claims:
        try:
            metadata = await _execute_claim(
                db, organization_id=organization_id, execution_id=execution_id, claim=claim
            )
            if await complete_claim(
                db, organization_id=organization_id, execution_id=execution_id,
                job_id=claim.id, claim_token=claim.claim_token, metadata=metadata,
            ):
                counts["succeeded"] += 1
            else:
                counts["stale"] += 1
        except Exception as exc:
            # Persist only a bounded classification; provider details/secrets never enter jobs.
            retryable = claim.work_kind in {"prepare_document", "extract_chunk"}
            written = await fail_claim(
                db, organization_id=organization_id, execution_id=execution_id,
                job_id=claim.id, claim_token=claim.claim_token,
                classification=type(exc).__name__.lower()[:80],
                safe_message="Facility pipeline work failed",
                retryable=retryable,
                retry_delay=timedelta(
                    seconds=settings.phase6_retry_base_seconds * (2 ** max(claim.attempt_count - 1, 0))
                ),
            )
            counts["failed" if written else "stale"] += 1
    return counts


async def _execute_claim(
    db: AsyncSession, *, organization_id: str, execution_id: str,
    claim: ClaimedFacilityWork,
) -> dict[str, int | str]:
    if claim.work_kind == "prepare_document" and claim.source_document_id:
        summary = await document_text_preparation_service.prepare(
            db, SourceDocumentPreparationContext(
                organization_id=organization_id, execution_id=execution_id,
                source_document_id=claim.source_document_id,
            )
        )
        seeded = await seed_chunk_extraction(
            db, organization_id=organization_id, execution_id=execution_id,
            source_document_id=claim.source_document_id,
        )
        return {"status": summary.preparation_status, "chunks_seeded": seeded}
    if claim.work_kind == "extract_chunk" and claim.chunk_id and claim.source_document_id:
        chunk = await db.scalar(select(ScrapingSourceDocumentChunk).where(
            ScrapingSourceDocumentChunk.id == claim.chunk_id,
            ScrapingSourceDocumentChunk.organization_id == organization_id,
            ScrapingSourceDocumentChunk.execution_id == execution_id,
            ScrapingSourceDocumentChunk.source_document_id == claim.source_document_id,
        ))
        if chunk is None:
            raise LookupError("owned chunk not found")
        summary = await facility_extraction_service.extract_one_chunk(
            db, FacilityExtractionContext(
                organization_id=organization_id, execution_id=execution_id,
                source_document_id=claim.source_document_id,
                prepared_text_id=chunk.prepared_text_id, chunk_id=claim.chunk_id,
                idempotency_key=f"phase6:{claim.id}",
                work_job_id=claim.id, claim_token=claim.claim_token,
            )
        )
        if summary.status == "stale_claim":
            return {"status": "stale_claim", "candidates_seeded": 0}
        if summary.status != "succeeded":
            raise RuntimeError(summary.failure_classification or "facility_extraction_failed")
        seeded = await seed_candidate_verification(
            db, organization_id=organization_id, execution_id=execution_id,
            chunk_id=claim.chunk_id,
        )
        return {"status": summary.status, "candidates_seeded": seeded}
    if claim.work_kind == "verify_candidate" and claim.facility_candidate_id:
        decision = await verify_candidate(
            db, organization_id=organization_id, execution_id=execution_id,
            candidate_id=claim.facility_candidate_id,
        )
        await create_job(
            db, organization_id=organization_id, execution_id=execution_id,
            work_kind="deduplicate_candidate",
            facility_candidate_id=claim.facility_candidate_id,
        )
        await db.commit()
        return {"status": decision.final_status}
    if claim.work_kind == "deduplicate_candidate" and claim.facility_candidate_id:
        count = await link_probable_duplicates(
            db, organization_id=organization_id, execution_id=execution_id,
            candidate_id=claim.facility_candidate_id,
        )
        await db.commit()
        return {"status": "deduplicated", "relationships_created": count}
    raise ValueError("incompatible facility work job")
