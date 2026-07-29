"""Restart-safe Phase 6/7 work claims using PostgreSQL time and claim fencing."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScrapingExecution, ScrapingFacilityPhaseWorkJob
from app.services.scraping.facility_candidate_verification import canonical_hash


@dataclass(frozen=True)
class ClaimedFacilityWork:
    id: str
    work_kind: str
    claim_token: str
    source_document_id: str | None
    chunk_id: str | None
    facility_candidate_id: str | None
    attempt_count: int


def work_fingerprint(
    *, organization_id: str, execution_id: str, work_kind: str,
    source_document_id: str | None = None, chunk_id: str | None = None,
    facility_candidate_id: str | None = None, version: str = "phase6-7-v1",
) -> str:
    return canonical_hash({
        "organization_id": organization_id, "execution_id": execution_id,
        "work_kind": work_kind, "source_document_id": source_document_id,
        "chunk_id": chunk_id, "facility_candidate_id": facility_candidate_id,
        "version": version,
    })


async def create_job(
    db: AsyncSession, *, organization_id: str, execution_id: str, work_kind: str,
    source_document_id: str | None = None, chunk_id: str | None = None,
    facility_candidate_id: str | None = None, metadata: dict[str, Any] | None = None,
) -> str | None:
    fingerprint = work_fingerprint(
        organization_id=organization_id, execution_id=execution_id, work_kind=work_kind,
        source_document_id=source_document_id, chunk_id=chunk_id,
        facility_candidate_id=facility_candidate_id,
    )
    statement = insert(ScrapingFacilityPhaseWorkJob).values(
        organization_id=organization_id, execution_id=execution_id, work_kind=work_kind,
        fingerprint=fingerprint, source_document_id=source_document_id, chunk_id=chunk_id,
        facility_candidate_id=facility_candidate_id, status="pending",
        metadata_json=metadata or {},
    ).on_conflict_do_nothing(
        constraint="uq_facility_phase_job_fingerprint"
    ).returning(ScrapingFacilityPhaseWorkJob.id)
    return await db.scalar(statement)


async def claim_batch(
    db: AsyncSession, *, organization_id: str, execution_id: str,
    batch_size: int, lease_duration: timedelta,
    work_kinds: set[str] | None = None,
) -> list[ClaimedFacilityWork]:
    # Ownership is checked in the same transaction as the SKIP LOCKED claim.
    execution_exists = await db.scalar(select(ScrapingExecution.id).where(
        ScrapingExecution.id == execution_id,
        ScrapingExecution.organization_id == organization_id,
    ))
    if execution_exists is None:
        return []
    db_now = func.now()
    eligible = or_(
        and_(ScrapingFacilityPhaseWorkJob.status.in_(("pending", "retry_scheduled")),
             or_(ScrapingFacilityPhaseWorkJob.next_retry_at.is_(None),
                 ScrapingFacilityPhaseWorkJob.next_retry_at <= db_now)),
        and_(ScrapingFacilityPhaseWorkJob.status == "running",
             ScrapingFacilityPhaseWorkJob.lease_expires_at < db_now),
    )
    criteria = [
            ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
            ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
            eligible,
            ScrapingFacilityPhaseWorkJob.attempt_count < ScrapingFacilityPhaseWorkJob.max_attempts,
    ]
    if work_kinds:
        criteria.append(ScrapingFacilityPhaseWorkJob.work_kind.in_(work_kinds))
    rows = list((await db.execute(
        select(ScrapingFacilityPhaseWorkJob).where(*criteria)
        .order_by(ScrapingFacilityPhaseWorkJob.created_at, ScrapingFacilityPhaseWorkJob.id)
        .with_for_update(skip_locked=True).limit(batch_size)
    )).scalars())
    claimed: list[ClaimedFacilityWork] = []
    for row in rows:
        token = secrets.token_hex(32)
        row.status = "running"
        row.claim_token = token
        row.claimed_at = db_now
        row.lease_expires_at = db_now + lease_duration
        row.attempt_count += 1
        claimed.append(ClaimedFacilityWork(
            row.id, row.work_kind, token, row.source_document_id, row.chunk_id,
            row.facility_candidate_id, row.attempt_count,
        ))
    await db.commit()
    return claimed


async def complete_claim(
    db: AsyncSession, *, organization_id: str, execution_id: str,
    job_id: str, claim_token: str, metadata: dict[str, Any] | None = None,
) -> bool:
    result = await db.execute(update(ScrapingFacilityPhaseWorkJob).where(
        ScrapingFacilityPhaseWorkJob.id == job_id,
        ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
        ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
        ScrapingFacilityPhaseWorkJob.status == "running",
        ScrapingFacilityPhaseWorkJob.claim_token == claim_token,
        ScrapingFacilityPhaseWorkJob.lease_expires_at >= func.now(),
    ).values(
        status="succeeded", completed_at=func.now(), claim_token=None,
        claimed_at=None, lease_expires_at=None, metadata_json=metadata or {},
    ))
    await db.commit()
    return result.rowcount == 1


async def fail_claim(
    db: AsyncSession, *, organization_id: str, execution_id: str,
    job_id: str, claim_token: str, classification: str, safe_message: str,
    retryable: bool, retry_delay: timedelta,
) -> bool:
    row = await db.scalar(select(ScrapingFacilityPhaseWorkJob).where(
        ScrapingFacilityPhaseWorkJob.id == job_id,
        ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
        ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
        ScrapingFacilityPhaseWorkJob.status == "running",
        ScrapingFacilityPhaseWorkJob.claim_token == claim_token,
        ScrapingFacilityPhaseWorkJob.lease_expires_at >= func.now(),
    ).with_for_update())
    if row is None:
        await db.rollback()
        return False
    can_retry = retryable and row.attempt_count < row.max_attempts
    row.status = "retry_scheduled" if can_retry else "failed"
    row.next_retry_at = func.now() + retry_delay if can_retry else None
    row.completed_at = None if can_retry else func.now()
    row.failure_classification = classification[:80]
    row.safe_error_message = safe_message[:500]
    row.claim_token = None
    row.claimed_at = None
    row.lease_expires_at = None
    await db.commit()
    return True
