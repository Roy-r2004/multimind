"""Short-transaction persistence boundaries for Phase 5 work and results.

No function in this module performs DNS, HTTP, browser, or provider work.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Phase5WorkStatus,
    ScrapingDirectoryObservation,
    ScrapingExecution,
    ScrapingPhase5RetrievalResult,
    ScrapingPhase5WorkJob,
)
from app.services.scraping.source_discovery_claim_service import evaluate_claim_lifecycle
from app.services.scraping.phase5_contracts import (
    Phase5PersistenceResult,
    PreparedDirectoryObservation,
    PreparedPhase5Job,
    PreparedRetrievalResult,
    RetryableFailure,
    TerminalActionFailure,
)

MAX_CLAIM_BATCH = 50


@dataclass(frozen=True)
class ClaimedPhase5Job:
    id: str
    organization_id: str
    execution_id: str
    work_kind: str
    selected_tool: str
    canonical_url: str
    claim_token: str
    lease_expires_at: datetime


async def create_job_idempotently(
    session: AsyncSession, prepared: PreparedPhase5Job
) -> Phase5PersistenceResult:
    existing = await session.scalar(
        select(ScrapingPhase5WorkJob).where(
            ScrapingPhase5WorkJob.organization_id == prepared.organization_id,
            ScrapingPhase5WorkJob.execution_id == prepared.execution_id,
            ScrapingPhase5WorkJob.fingerprint == prepared.fingerprint,
        )
    )
    if existing:
        return Phase5PersistenceResult(outcome="existing", record_id=existing.id)
    row = ScrapingPhase5WorkJob(
        organization_id=prepared.organization_id,
        execution_id=prepared.execution_id,
        source_candidate_id=prepared.source_candidate_id,
        crawl_node_id=prepared.crawl_node_id,
        crawl_edge_id=prepared.crawl_edge_id,
        discovery_query_id=prepared.discovery_query_id,
        original_url=prepared.original_url,
        canonical_url=prepared.canonical_url,
        source_classification=prepared.source_classification,
        work_kind=prepared.work_kind,
        selected_tool=prepared.selected_tool,
        fingerprint=prepared.fingerprint,
        requested_at=prepared.requested_at,
        status=(Phase5WorkStatus.REJECTED if prepared.rejection_category
                else Phase5WorkStatus.PENDING),
        last_error_category=prepared.rejection_category,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(ScrapingPhase5WorkJob).where(
                ScrapingPhase5WorkJob.organization_id == prepared.organization_id,
                ScrapingPhase5WorkJob.execution_id == prepared.execution_id,
                ScrapingPhase5WorkJob.fingerprint == prepared.fingerprint,
            )
        )
        if existing is None:
            raise
        return Phase5PersistenceResult(outcome="existing", record_id=existing.id)
    return Phase5PersistenceResult(outcome="created", record_id=row.id)


async def claim_job(
    session: AsyncSession, *, job_id: str, organization_id: str,
    execution_id: str, now: datetime, lease_expires_at: datetime,
) -> str | None:
    """Claim one already selected job. Callers bound their batch before this boundary."""
    row = await session.scalar(
        select(ScrapingPhase5WorkJob).where(
            ScrapingPhase5WorkJob.id == job_id,
            ScrapingPhase5WorkJob.organization_id == organization_id,
            ScrapingPhase5WorkJob.execution_id == execution_id,
            ScrapingPhase5WorkJob.status.in_(
                [Phase5WorkStatus.PENDING, Phase5WorkStatus.RETRY_SCHEDULED]
            ),
        ).with_for_update()
    )
    if row is None or (row.next_retry_at is not None and row.next_retry_at > now):
        return None
    token = str(uuid.uuid4())
    row.status = Phase5WorkStatus.RUNNING
    row.claim_token = token
    row.claimed_at = now
    row.lease_expires_at = lease_expires_at
    row.started_at = row.started_at or now
    row.attempt_count += 1
    await session.flush()
    return token


async def claim_batch(
    session: AsyncSession, *, organization_id: str, execution_id: str,
    now: datetime, lease_duration: timedelta, batch_size: int,
    selected_tool: str | None = None,
) -> tuple[ClaimedPhase5Job, ...]:
    """Bounded PostgreSQL SKIP LOCKED claim; transaction is owned by the caller."""
    if not 1 <= batch_size <= MAX_CLAIM_BATCH:
        raise ValueError(f"batch_size must be between 1 and {MAX_CLAIM_BATCH}")
    if lease_duration <= timedelta(0):
        raise ValueError("lease_duration must be positive")
    execution = await session.scalar(select(ScrapingExecution).where(
        ScrapingExecution.id == execution_id,
        ScrapingExecution.organization_id == organization_id,
    ).with_for_update())
    if evaluate_claim_lifecycle(execution) is not None:
        return ()
    predicates = [
        ScrapingPhase5WorkJob.organization_id == organization_id,
        ScrapingPhase5WorkJob.execution_id == execution_id,
        ScrapingPhase5WorkJob.canonical_url.is_not(None),
        or_(
            ScrapingPhase5WorkJob.status == Phase5WorkStatus.PENDING,
            (
                (ScrapingPhase5WorkJob.status == Phase5WorkStatus.RETRY_SCHEDULED)
                & or_(ScrapingPhase5WorkJob.next_retry_at.is_(None),
                      ScrapingPhase5WorkJob.next_retry_at <= now)
            ),
        ),
    ]
    if selected_tool is not None:
        predicates.append(ScrapingPhase5WorkJob.selected_tool == selected_tool)
    rows = (await session.scalars(
        select(ScrapingPhase5WorkJob).where(and_(*predicates))
        .order_by(ScrapingPhase5WorkJob.requested_at, ScrapingPhase5WorkJob.id)
        .limit(batch_size).with_for_update(skip_locked=True)
    )).all()
    claimed: list[ClaimedPhase5Job] = []
    for row in rows:
        token = str(uuid.uuid4())
        row.status = Phase5WorkStatus.RUNNING
        row.claim_token = token
        row.claimed_at = now
        row.lease_expires_at = now + lease_duration
        row.started_at = row.started_at or now
        row.attempt_count += 1
        assert row.canonical_url is not None
        claimed.append(ClaimedPhase5Job(
            id=row.id, organization_id=row.organization_id,
            execution_id=row.execution_id, work_kind=row.work_kind.value,
            selected_tool=row.selected_tool, canonical_url=row.canonical_url,
            claim_token=token, lease_expires_at=row.lease_expires_at,
        ))
    await session.flush()
    return tuple(claimed)


async def recover_expired_claims(
    session: AsyncSession, *, organization_id: str, execution_id: str,
    now: datetime, batch_size: int = MAX_CLAIM_BATCH,
) -> int:
    if not 1 <= batch_size <= MAX_CLAIM_BATCH:
        raise ValueError(f"batch_size must be between 1 and {MAX_CLAIM_BATCH}")
    rows = (await session.scalars(
        select(ScrapingPhase5WorkJob).where(
            ScrapingPhase5WorkJob.organization_id == organization_id,
            ScrapingPhase5WorkJob.execution_id == execution_id,
            ScrapingPhase5WorkJob.status == Phase5WorkStatus.RUNNING,
            ScrapingPhase5WorkJob.lease_expires_at <= now,
        ).order_by(ScrapingPhase5WorkJob.lease_expires_at)
        .limit(batch_size).with_for_update(skip_locked=True)
    )).all()
    for row in rows:
        row.status = Phase5WorkStatus.RETRY_SCHEDULED
        row.next_retry_at = now
        row.last_error_category = "lease_expired"
        row.last_error_message = "The prior action lease expired."
        row.claim_token = row.claimed_at = row.lease_expires_at = None
    await session.flush()
    return len(rows)


async def persist_retrieval_result(
    session: AsyncSession, *, claim_token: str, result: PreparedRetrievalResult,
) -> Phase5PersistenceResult:
    job = await _locked_claim(session, result.job_id, result.organization_id,
                              result.execution_id, claim_token)
    if job is None:
        return Phase5PersistenceResult(outcome="stale_claim")
    if job.work_kind.value != result.retrieval_method.value:
        raise ValueError("retrieval result method does not match claimed work kind")
    existing = await session.scalar(select(ScrapingPhase5RetrievalResult).where(
        ScrapingPhase5RetrievalResult.organization_id == result.organization_id,
        ScrapingPhase5RetrievalResult.execution_id == result.execution_id,
        ScrapingPhase5RetrievalResult.work_job_id == job.id,
        ScrapingPhase5RetrievalResult.result_fingerprint == result.result_fingerprint))
    if existing:
        return Phase5PersistenceResult(outcome="existing", record_id=existing.id)
    row = ScrapingPhase5RetrievalResult(
        organization_id=result.organization_id, execution_id=result.execution_id,
        work_job_id=result.job_id, requested_url=result.requested_url,
        final_url=result.final_url, http_status=result.http_status,
        content_type=result.content_type, content_length=result.content_length,
        response_fingerprint=result.response_fingerprint,
        result_fingerprint=result.result_fingerprint,
        resource_role=result.resource_role,
        result_ordinal=result.result_ordinal,
        retrieval_method=result.retrieval_method.value,
        cache_status=result.cache_status, redirect_count=result.redirect_count,
        fetched_at=result.fetched_at, raw_storage_reference=result.raw_storage_reference,
        source_document_id=result.source_document_id,
        parent_crawl_edge_id=result.parent_crawl_edge_id,
        provider_request_id=result.provider_request_id,
        provider_result_status=result.provider_result_status,
        failure_category=result.failure_category,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(select(ScrapingPhase5RetrievalResult).where(
            ScrapingPhase5RetrievalResult.organization_id == result.organization_id,
            ScrapingPhase5RetrievalResult.execution_id == result.execution_id,
            ScrapingPhase5RetrievalResult.work_job_id == job.id,
            ScrapingPhase5RetrievalResult.result_fingerprint == result.result_fingerprint))
        if existing is None:
            raise
        return Phase5PersistenceResult(outcome="existing", record_id=existing.id)
    return Phase5PersistenceResult(outcome="persisted", record_id=row.id)


async def persist_directory_observation(
    session: AsyncSession, *, claim_token: str,
    observation: PreparedDirectoryObservation,
) -> Phase5PersistenceResult:
    job = await _locked_claim(session, observation.work_job_id,
                              observation.organization_id,
                              observation.execution_id, claim_token)
    if job is None:
        return Phase5PersistenceResult(outcome="stale_claim")
    if job.work_kind.value != "directory_expansion":
        raise ValueError("directory observations require directory_expansion work")
    existing = await session.scalar(select(ScrapingDirectoryObservation).where(
        ScrapingDirectoryObservation.organization_id == observation.organization_id,
        ScrapingDirectoryObservation.execution_id == observation.execution_id,
        ScrapingDirectoryObservation.observation_fingerprint
        == observation.observation_fingerprint))
    if existing:
        return Phase5PersistenceResult(outcome="existing", record_id=existing.id)
    row = ScrapingDirectoryObservation(**observation.model_dump())
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(select(ScrapingDirectoryObservation).where(
            ScrapingDirectoryObservation.organization_id == observation.organization_id,
            ScrapingDirectoryObservation.execution_id == observation.execution_id,
            ScrapingDirectoryObservation.observation_fingerprint
            == observation.observation_fingerprint))
        if existing is None:
            raise
        return Phase5PersistenceResult(outcome="existing", record_id=existing.id)
    return Phase5PersistenceResult(outcome="persisted", record_id=row.id)


async def complete_job(
    session: AsyncSession, *, job_id: str, organization_id: str,
    execution_id: str, claim_token: str, completed_at: datetime,
) -> Phase5PersistenceResult:
    job = await _locked_claim(session, job_id, organization_id, execution_id, claim_token)
    if job is None:
        return Phase5PersistenceResult(outcome="stale_claim")
    job.status = Phase5WorkStatus.SUCCEEDED
    job.completed_at = completed_at
    job.claim_token = job.claimed_at = job.lease_expires_at = None
    await session.flush()
    return Phase5PersistenceResult(outcome="persisted", record_id=job.id)


async def record_retryable_failure(
    session: AsyncSession, *, job_id: str, organization_id: str,
    execution_id: str, claim_token: str, failure: RetryableFailure,
) -> Phase5PersistenceResult:
    job = await _locked_claim(session, job_id, organization_id, execution_id, claim_token)
    if job is None:
        return Phase5PersistenceResult(outcome="stale_claim")
    job.status = Phase5WorkStatus.RETRY_SCHEDULED
    job.next_retry_at = failure.next_retry_at
    job.last_error_category = failure.category
    job.last_error_message = failure.public_message
    job.claim_token = job.claimed_at = job.lease_expires_at = None
    await session.flush()
    return Phase5PersistenceResult(outcome="persisted", record_id=job.id)


async def record_terminal_failure(
    session: AsyncSession, *, job_id: str, organization_id: str,
    execution_id: str, claim_token: str, failure: TerminalActionFailure,
    completed_at: datetime,
) -> Phase5PersistenceResult:
    job = await _locked_claim(session, job_id, organization_id, execution_id, claim_token)
    if job is None:
        return Phase5PersistenceResult(outcome="stale_claim")
    job.status = Phase5WorkStatus.FAILED
    job.completed_at = completed_at
    job.last_error_category = failure.category
    job.last_error_message = failure.public_message
    job.claim_token = job.claimed_at = job.lease_expires_at = None
    await session.flush()
    return Phase5PersistenceResult(outcome="persisted", record_id=job.id)


async def _locked_claim(session: AsyncSession, job_id: str, organization_id: str,
                        execution_id: str, claim_token: str) -> ScrapingPhase5WorkJob | None:
    return await session.scalar(select(ScrapingPhase5WorkJob).where(
        ScrapingPhase5WorkJob.id == job_id,
        ScrapingPhase5WorkJob.organization_id == organization_id,
        ScrapingPhase5WorkJob.execution_id == execution_id,
        ScrapingPhase5WorkJob.status == Phase5WorkStatus.RUNNING,
        ScrapingPhase5WorkJob.claim_token == claim_token,
        ScrapingPhase5WorkJob.lease_expires_at > func.now(),
    ).with_for_update())
