"""Extract one persisted source document chunk for manual Step 3A smoke testing."""

from __future__ import annotations

import argparse
import asyncio
from urllib.parse import urlsplit

from sqlalchemy import select

from app.db.models import ScrapingFacilityCandidate, ScrapingSourceDocument, ScrapingSourceDocumentText
from app.db.session import AsyncSessionLocal
from app.services.scraping.document_text_preparation_service import (
    SourceDocumentPreparationContext,
    document_text_preparation_service,
)
from app.services.scraping.facility_extraction_service import (
    FacilityExtractionContext,
    facility_extraction_service,
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--source-document-id", required=True)
    parser.add_argument("--chunk-index", type=int, default=0)
    parser.add_argument("--language-hint", default=None)
    parser.add_argument("--idempotency-key", default=None)
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        document = await db.scalar(
            select(ScrapingSourceDocument).where(
                ScrapingSourceDocument.organization_id == args.organization_id,
                ScrapingSourceDocument.execution_id == args.execution_id,
                ScrapingSourceDocument.id == args.source_document_id,
            )
        )
        if document is None:
            print("preparation_status=document_not_found")
            return

        prepared = await document_text_preparation_service.prepare(
            db,
            SourceDocumentPreparationContext(
                organization_id=args.organization_id,
                execution_id=args.execution_id,
                source_document_id=args.source_document_id,
                language_hint=args.language_hint,
            ),
        )
        print(f"preparation_status={prepared.preparation_status}")
        print(f"prepared_character_count={prepared.character_count}")
        print(f"chunk_count={prepared.chunk_count}")
        print(f"selected_chunk_index={args.chunk_index}")

        if prepared.preparation_status != "prepared":
            print(f"failure_classification={prepared.failure_classification or ''}")
            return

        summary = await facility_extraction_service.extract_one_chunk(
            db,
            FacilityExtractionContext(
                organization_id=args.organization_id,
                execution_id=args.execution_id,
                source_document_id=args.source_document_id,
                chunk_index=args.chunk_index,
                language_hint=args.language_hint,
                idempotency_key=args.idempotency_key
                or f"manual-extract:{args.execution_id}:{args.source_document_id}:{args.chunk_index}",
            ),
        )
        candidates = (
            await db.execute(
                select(ScrapingFacilityCandidate)
                .where(ScrapingFacilityCandidate.extraction_attempt_id == summary.attempt_id)
                .order_by(ScrapingFacilityCandidate.created_at)
            )
        ).scalars().all() if summary.attempt_id else []
        prepared_row = await db.scalar(
            select(ScrapingSourceDocumentText).where(ScrapingSourceDocumentText.id == prepared.id)
        )
        print(f"source_hostname={urlsplit(document.final_url).hostname or ''}")
        print(f"prepared_hash_prefix={(prepared_row.prepared_text_hash if prepared_row else '')[:12]}")
        print(f"extraction_status={summary.status}")
        print(f"candidate_count={summary.extracted_candidate_count}")
        print(f"accepted_evidence_count={summary.accepted_evidence_count}")
        print(f"rejected_evidence_count={summary.rejected_evidence_count}")
        for candidate in candidates[:10]:
            print(f"candidate_name={candidate.raw_name}")


if __name__ == "__main__":
    asyncio.run(main())
