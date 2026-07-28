"""Restart-safe Phase 5F work seeding, fallback, and completion decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Phase5WorkStatus, ScrapingCrawlNode, ScrapingPhase5RetrievalResult,
    ScrapingPhase5WorkJob, ScrapingSourceCandidate, ScrapingSourceDocument,
)
from app.services.scraping.directory_expansion_service import (
    compute_prepared_content_fingerprint,
)
from app.services.scraping.phase5_contracts import Phase5WorkKind, prepare_phase5_job
from app.services.scraping.phase5_job_service import create_job_idempotently
from app.services.scraping.phase5_retrieval_service import fallback_work_kind
from app.services.scraping.blueprint_execution_plan_service import sha256_hex


@dataclass(frozen=True)
class Phase5SeedSummary:
    created: int
    existing: int
    examined: int


@dataclass(frozen=True)
class Phase5Readiness:
    ready_for_review: bool
    runnable: int
    active: int
    blocked: int
    unfinished_expansions: int
    unseeded_candidates: int
    next_retry_at: datetime | None


async def seed_initial_http_jobs(
    session: AsyncSession, *, organization_id: str, execution_id: str,
    requested_at: datetime, batch_size: int,
) -> Phase5SeedSummary:
    """Seed one bounded page of candidate-owned crawl nodes; repeated calls continue."""
    rows = (await session.execute(
        select(ScrapingSourceCandidate, ScrapingCrawlNode)
        .join(ScrapingCrawlNode, ScrapingCrawlNode.id == ScrapingSourceCandidate.crawl_node_id)
        .where(
            ScrapingSourceCandidate.organization_id == organization_id,
            ScrapingSourceCandidate.execution_id == execution_id,
            ScrapingCrawlNode.organization_id == organization_id,
            ScrapingCrawlNode.execution_id == execution_id,
            ~exists(select(ScrapingPhase5WorkJob.id).where(
                ScrapingPhase5WorkJob.organization_id == organization_id,
                ScrapingPhase5WorkJob.execution_id == execution_id,
                ScrapingPhase5WorkJob.crawl_node_id == ScrapingCrawlNode.id,
                ScrapingPhase5WorkJob.work_kind == "http_retrieval")),
        )
        .order_by(ScrapingSourceCandidate.id)
        .limit(batch_size)
    )).all()
    created = existing = 0
    for candidate, node in rows:
        prepared = prepare_phase5_job(
            organization_id=organization_id, execution_id=execution_id,
            source_candidate_id=candidate.id, crawl_node_id=node.id,
            original_url=node.canonical_url,
            source_classification=node.source_classification.value,
            work_kind=Phase5WorkKind.HTTP_RETRIEVAL, selected_tool="http",
            requested_at=requested_at)
        result = await create_job_idempotently(session, prepared)
        created += result.outcome == "created"
        existing += result.outcome == "existing"
    return Phase5SeedSummary(created=created, existing=existing, examined=len(rows))


async def create_expansion_for_retrieval(
    session: AsyncSession, *, retrieval_result_id: str,
    organization_id: str, execution_id: str, requested_at: datetime,
):
    retrieval = await session.scalar(select(ScrapingPhase5RetrievalResult).where(
        ScrapingPhase5RetrievalResult.id == retrieval_result_id,
        ScrapingPhase5RetrievalResult.organization_id == organization_id,
        ScrapingPhase5RetrievalResult.execution_id == execution_id))
    if retrieval is None or not retrieval.source_document_id:
        raise ValueError("directory expansion requires a durable retrieval document")
    document = await session.scalar(select(ScrapingSourceDocument).where(
        ScrapingSourceDocument.id == retrieval.source_document_id,
        ScrapingSourceDocument.organization_id == organization_id,
        ScrapingSourceDocument.execution_id == execution_id))
    retrieval_job = await session.scalar(select(ScrapingPhase5WorkJob).where(
        ScrapingPhase5WorkJob.id == retrieval.work_job_id,
        ScrapingPhase5WorkJob.organization_id == organization_id,
        ScrapingPhase5WorkJob.execution_id == execution_id))
    if document is None or retrieval_job is None:
        raise ValueError("retrieval provenance is incomplete")
    media_type = document.content_type.lower().split(";", 1)[0].strip()
    decoded, structured = document.content_text, None
    if media_type in {"application/json", "application/ld+json"}:
        structured = json.loads(document.content_text)
        decoded = None
    content_fingerprint = compute_prepared_content_fingerprint(
        content_type=document.content_type, decoded_content=decoded,
        structured_json=structured)
    prepared = prepare_phase5_job(
        organization_id=organization_id, execution_id=execution_id,
        source_candidate_id=retrieval_job.source_candidate_id,
        crawl_node_id=retrieval_job.crawl_node_id,
        original_url=retrieval.final_url or retrieval.requested_url,
        source_classification=retrieval_job.source_classification,
        work_kind=Phase5WorkKind.DIRECTORY_EXPANSION,
        selected_tool="directory_expansion", requested_at=requested_at,
        input_retrieval_result_id=retrieval.id,
        input_source_document_id=document.id,
        input_content_fingerprint=content_fingerprint,
        input_retrieval_method=Phase5WorkKind(retrieval.retrieval_method))
    return await create_job_idempotently(session, prepared)


async def create_typed_fallback_job(
    session: AsyncSession, *, expansion_job_id: str,
    organization_id: str, execution_id: str, requested_at: datetime,
):
    expansion = await session.scalar(select(ScrapingPhase5WorkJob).where(
        ScrapingPhase5WorkJob.id == expansion_job_id,
        ScrapingPhase5WorkJob.organization_id == organization_id,
        ScrapingPhase5WorkJob.execution_id == execution_id,
        ScrapingPhase5WorkJob.work_kind == "directory_expansion"))
    if expansion is None:
        raise ValueError("directory expansion job was not found")
    fallback = fallback_work_kind(expansion.expansion_outcome or "")
    if (
        fallback is Phase5WorkKind.FIRECRAWL_RETRIEVAL and
        expansion.input_retrieval_method == Phase5WorkKind.FIRECRAWL_RETRIEVAL.value
    ):
        return None
    if fallback is None:
        return None
    tool = {
        Phase5WorkKind.FIRECRAWL_RETRIEVAL: "firecrawl",
        Phase5WorkKind.PLAYWRIGHT_RETRIEVAL: "playwright",
    }[fallback]
    continuation = next((
        marker for marker in (expansion.continuation_markers_json or [])
        if marker.get("requires_browser_interaction") or marker.get("canonical_url")
    ), {})
    action_state = {
        "relationship": continuation.get("relationship"),
        "canonical_url": continuation.get("canonical_url"),
        "ordinal_hint": continuation.get("ordinal_hint"),
        "parent_expansion_job_id": expansion.id,
    }
    action_state_fingerprint = sha256_hex({
        "schema": "phase5_action_state_v1", **action_state})
    prepared = prepare_phase5_job(
        organization_id=organization_id, execution_id=execution_id,
        source_candidate_id=expansion.source_candidate_id,
        crawl_node_id=expansion.crawl_node_id, original_url=expansion.canonical_url,
        source_classification=expansion.source_classification,
        work_kind=fallback, selected_tool=tool, requested_at=requested_at,
        action_state_fingerprint=action_state_fingerprint)
    result = await create_job_idempotently(session, prepared)
    row = await session.get(ScrapingPhase5WorkJob, result.record_id)
    if row is not None:
        row.operational_metadata_json = {
            "action_state": action_state,
            "action_state_fingerprint": action_state_fingerprint,
        }
    return result


async def phase5_readiness(
    session: AsyncSession, *, organization_id: str, execution_id: str,
) -> Phase5Readiness:
    base = (
        ScrapingPhase5WorkJob.organization_id == organization_id,
        ScrapingPhase5WorkJob.execution_id == execution_id,
    )
    runnable = await session.scalar(select(func.count()).select_from(
        ScrapingPhase5WorkJob).where(
            *base,
            (ScrapingPhase5WorkJob.status == Phase5WorkStatus.PENDING) |
            ((ScrapingPhase5WorkJob.status == Phase5WorkStatus.RETRY_SCHEDULED) &
             (ScrapingPhase5WorkJob.next_retry_at <= func.now()))))
    next_retry_at = await session.scalar(select(func.min(
        ScrapingPhase5WorkJob.next_retry_at)).where(
            *base, ScrapingPhase5WorkJob.status == Phase5WorkStatus.RETRY_SCHEDULED,
            ScrapingPhase5WorkJob.next_retry_at > func.now()))
    active = await session.scalar(select(func.count()).select_from(
        ScrapingPhase5WorkJob).where(*base, ScrapingPhase5WorkJob.status == Phase5WorkStatus.RUNNING))
    blocked = await session.scalar(select(func.count()).select_from(
        ScrapingPhase5WorkJob).where(*base, ScrapingPhase5WorkJob.status == Phase5WorkStatus.BLOCKED))
    unfinished = await session.scalar(select(func.count()).select_from(
        ScrapingPhase5WorkJob).where(
            *base, ScrapingPhase5WorkJob.work_kind == "directory_expansion",
            ScrapingPhase5WorkJob.expansion_completed.is_(False),
            ScrapingPhase5WorkJob.status.notin_([
                Phase5WorkStatus.BLOCKED, Phase5WorkStatus.REJECTED,
                Phase5WorkStatus.FAILED, Phase5WorkStatus.CANCELLED])))
    unseeded = await session.scalar(select(func.count()).select_from(
        ScrapingSourceCandidate).where(
            ScrapingSourceCandidate.organization_id == organization_id,
            ScrapingSourceCandidate.execution_id == execution_id,
            ~exists(select(ScrapingPhase5WorkJob.id).where(
                ScrapingPhase5WorkJob.organization_id == organization_id,
                ScrapingPhase5WorkJob.execution_id == execution_id,
                ScrapingPhase5WorkJob.source_candidate_id == ScrapingSourceCandidate.id,
                ScrapingPhase5WorkJob.work_kind == "http_retrieval"))))
    runnable, active, blocked, unfinished, unseeded = map(
        int, (runnable or 0, active or 0, blocked or 0, unfinished or 0, unseeded or 0))
    return Phase5Readiness(
        ready_for_review=(
            runnable == active == unfinished == unseeded == 0 and
            next_retry_at is None),
        runnable=runnable, active=active, blocked=blocked,
        unfinished_expansions=unfinished, unseeded_candidates=unseeded,
        next_retry_at=next_retry_at)
