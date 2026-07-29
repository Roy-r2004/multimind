"""Phase 5 boundary adapter for durable Phase 6/7 work, without publication."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ScrapingFacilityCandidate,
    ScrapingSourceDocument,
    ScrapingSourceDocumentChunk,
)
from app.services.scraping.facility_phase_job_service import create_job


async def seed_document_preparation(
    db: AsyncSession, *, organization_id: str, execution_id: str,
) -> int:
    """Seed one bounded transaction; callers repeat until zero (no campaign ceiling)."""
    documents = list((await db.execute(
        select(ScrapingSourceDocument.id).where(
            ScrapingSourceDocument.organization_id == organization_id,
            ScrapingSourceDocument.execution_id == execution_id,
        ).order_by(ScrapingSourceDocument.id)
    )).scalars())
    created = 0
    for document_id in documents:
        created += bool(await create_job(
            db, organization_id=organization_id, execution_id=execution_id,
            work_kind="prepare_document", source_document_id=document_id,
        ))
    await db.commit()
    return created


async def seed_chunk_extraction(
    db: AsyncSession, *, organization_id: str, execution_id: str,
    source_document_id: str,
) -> int:
    chunks = list((await db.execute(select(ScrapingSourceDocumentChunk.id).where(
        ScrapingSourceDocumentChunk.organization_id == organization_id,
        ScrapingSourceDocumentChunk.execution_id == execution_id,
        ScrapingSourceDocumentChunk.source_document_id == source_document_id,
    ).order_by(ScrapingSourceDocumentChunk.chunk_index))).scalars())
    created = 0
    for chunk_id in chunks:
        created += bool(await create_job(
            db, organization_id=organization_id, execution_id=execution_id,
            work_kind="extract_chunk", source_document_id=source_document_id, chunk_id=chunk_id,
        ))
    await db.commit()
    return created


async def seed_candidate_verification(
    db: AsyncSession, *, organization_id: str, execution_id: str,
    chunk_id: str,
) -> int:
    candidates = list((await db.execute(select(ScrapingFacilityCandidate.id).where(
        ScrapingFacilityCandidate.organization_id == organization_id,
        ScrapingFacilityCandidate.execution_id == execution_id,
        ScrapingFacilityCandidate.chunk_id == chunk_id,
    ).order_by(ScrapingFacilityCandidate.id))).scalars())
    created = 0
    for candidate_id in candidates:
        created += bool(await create_job(
            db, organization_id=organization_id, execution_id=execution_id,
            work_kind="verify_candidate", facility_candidate_id=candidate_id,
        ))
    await db.commit()
    return created
