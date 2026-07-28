"""User-run guarded Phase 5 smoke. Preview-only unless confirmations are supplied."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import (
    ScrapingCrawlNode, ScrapingExecution, ScrapingExecutionStatus,
    ScrapingSourceCandidate,
)
from app.db.session import AsyncSessionLocal
from app.services.scraping.directory_expansion_service import (
    identify_and_prepare_directory, persist_prepared_expansion,
    reload_prepared_directory_content,
)
from app.services.scraping.phase5_contracts import Phase5WorkKind, prepare_phase5_job
from app.services.scraping.phase5_job_service import (
    claim_job, create_job_idempotently, persist_retrieval_resources,
)
from app.services.scraping.phase5_orchestration_service import create_expansion_for_retrieval
from app.services.scraping.phase5_retrieval_service import NormalHttpRetriever


async def main(args) -> None:
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(ScrapingCrawlNode, ScrapingSourceCandidate)
            .join(ScrapingSourceCandidate,
                  ScrapingSourceCandidate.crawl_node_id == ScrapingCrawlNode.id)
            .where(
                ScrapingCrawlNode.id == args.crawl_node_id,
                ScrapingCrawlNode.organization_id == args.organization_id,
                ScrapingCrawlNode.execution_id == args.execution_id,
                ScrapingSourceCandidate.organization_id == args.organization_id,
                ScrapingSourceCandidate.execution_id == args.execution_id)
            .limit(1))).first()
    if row is None:
        raise SystemExit("Selected owned crawl node/source candidate was not found.")
    node, candidate = row
    print({
        "mode": "preview" if not args.confirm_http else "confirmed_http",
        "execution_id": args.execution_id,
        "crawl_node_id": args.crawl_node_id,
        "automatic_continuation": False,
        "phase6": False,
        "firecrawl_confirmed": args.confirm_firecrawl,
        "playwright_confirmed": args.confirm_playwright,
    })
    if not args.confirm_http:
        return
    now = datetime.now(UTC)
    job = prepare_phase5_job(
        organization_id=args.organization_id, execution_id=args.execution_id,
        source_candidate_id=candidate.id, crawl_node_id=node.id,
        original_url=node.canonical_url,
        source_classification=node.source_classification.value,
        work_kind=Phase5WorkKind.HTTP_RETRIEVAL,
        selected_tool="http", requested_at=now)
    async with AsyncSessionLocal.begin() as session:
        created = await create_job_idempotently(session, job)
        token = await claim_job(
            session, job_id=created.record_id, organization_id=args.organization_id,
            execution_id=args.execution_id, now=now,
            lease_expires_at=now + timedelta(minutes=5))
    if token is None:
        raise SystemExit("Selected HTTP job is not claimable.")
    claimed = type("Claim", (), {
        "id": created.record_id, "organization_id": args.organization_id,
        "execution_id": args.execution_id, "claim_token": token,
    })()
    result = await NormalHttpRetriever().retrieve(
        url=node.canonical_url, requested_at=now, fetched_at=datetime.now(UTC))
    if result.outcome != "succeeded":
        raise SystemExit(f"HTTP smoke stopped safely: {result.failure_category}")
    async with AsyncSessionLocal.begin() as session:
        persisted = await persist_retrieval_resources(
            session, claimed_job=claimed, resources=result.resources,
            completed_at=datetime.now(UTC))
        expansion_created = await create_expansion_for_retrieval(
            session, retrieval_result_id=persisted[0].record_id,
            organization_id=args.organization_id, execution_id=args.execution_id,
            requested_at=datetime.now(UTC))
        expansion_token = await claim_job(
            session, job_id=expansion_created.record_id,
            organization_id=args.organization_id, execution_id=args.execution_id,
            now=datetime.now(UTC),
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5))
    async with AsyncSessionLocal() as session:
        expansion_claim = await session.get(
            __import__("app.db.models", fromlist=["ScrapingPhase5WorkJob"])
            .ScrapingPhase5WorkJob, expansion_created.record_id)
        expansion_claim.claim_token = expansion_token
        source = await reload_prepared_directory_content(
            session, claimed_job=expansion_claim, observed_at=datetime.now(UTC))
    prepared = identify_and_prepare_directory(source)
    async with AsyncSessionLocal.begin() as session:
        proof = await persist_prepared_expansion(session, prepared)
        execution = await session.scalar(select(ScrapingExecution).where(
            ScrapingExecution.id == args.execution_id,
            ScrapingExecution.organization_id == args.organization_id).with_for_update())
        if execution is None:
            raise SystemExit("Execution ownership changed before smoke completion.")
        execution.status = ScrapingExecutionStatus.PAUSED
        execution.paused_at = datetime.now(UTC)
        execution.completed_at = None
        execution.current_stage = "phase5_smoke_review"
        execution.latest_message = "Guarded Phase 5 smoke paused for review."
    print({
        "http_resources": len(result.resources),
        "expansion_outcome": prepared.outcome.value,
        "observations": proof.observation_count,
        "nodes": proof.node_count,
        "edges": proof.edge_count,
        "execution_paused_after_proof": True,
        "phase6_started": False,
        "historical_mock_stages_started": False,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--organization-id", required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--crawl-node-id", required=True)
    parser.add_argument("--confirm-http", action="store_true")
    parser.add_argument("--confirm-firecrawl", action="store_true")
    parser.add_argument("--confirm-playwright", action="store_true")
    asyncio.run(main(parser.parse_args()))
