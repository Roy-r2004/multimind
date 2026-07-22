"""Retrieve one persisted source candidate for manual Step 2A smoke testing."""

from __future__ import annotations

import argparse
import asyncio
from urllib.parse import urlsplit

from sqlalchemy import select

from app.db.models import ScrapingSourceCandidate
from app.db.session import AsyncSessionLocal
from app.services.scraping.source_retrieval_service import (
    SourceRetrievalContext,
    source_retrieval_service,
)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--idempotency-key", default=None)
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ScrapingSourceCandidate)
            .where(
                ScrapingSourceCandidate.organization_id == args.organization_id,
                ScrapingSourceCandidate.execution_id == args.execution_id,
            )
            .order_by(ScrapingSourceCandidate.discovered_at, ScrapingSourceCandidate.rank)
            .limit(1)
        )
        candidate = result.scalar_one_or_none()
        if candidate is None:
            print("attempt_status=no_candidate")
            return
        summary = await source_retrieval_service.retrieve(
            db,
            SourceRetrievalContext(
                organization_id=args.organization_id,
                execution_id=args.execution_id,
                source_candidate_id=candidate.id,
                coverage_cell_id=candidate.coverage_cell_id,
                idempotency_key=args.idempotency_key
                or f"manual-smoke:{args.execution_id}:{candidate.id}",
            ),
        )
        final_hostname = urlsplit(summary.final_url or "").hostname or ""
        print(f"attempt_status={summary.status}")
        print(f"final_hostname={final_hostname}")
        print(f"http_status={summary.http_status or ''}")
        print(f"content_type={summary.content_type or ''}")
        print(f"bytes_received={summary.bytes_received if summary.bytes_received is not None else ''}")
        print(f"content_hash_prefix={(summary.content_sha256 or '')[:12]}")


if __name__ == "__main__":
    asyncio.run(main())
