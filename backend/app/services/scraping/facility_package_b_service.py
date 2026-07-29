"""Python-authoritative Package B publication, export, and completion policy."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    FacilityCandidatePublicationStatus,
    FacilityCandidateEvidenceVerificationStatus,
    FacilityCandidateStagingStatus,
    FacilityExtractionAttemptStatus,
    ScrapingExecution,
    ScrapingExecutionExport,
    ScrapingExecutionStatus,
    ScrapingFacilityCandidate,
    ScrapingFacilityCandidateDecision,
    ScrapingFacilityCandidateDuplicate,
    ScrapingFacilityCandidateEvidence,
    ScrapingFacilityCandidatePublication,
    ScrapingFacilityExtractionAttempt,
    ScrapingFacilityPhaseWorkJob,
    ScrapingSourceDocument,
    ScrapingSourceDocumentText,
)
from app.services.scraping.execution_export_service import (
    MIME_XLSX,
    execution_export_service,
)
from app.services.scraping.execution_service import execution_service
from app.services.scraping.facility_candidate_publication_service import (
    FacilityCandidatePublicationContext,
    facility_candidate_publication_service,
)
from app.services.scraping.facility_phase_job_service import create_job

TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled"}
ACTIVE_JOB_STATUSES = {"pending", "running", "retry_scheduled"}


@dataclass(frozen=True)
class PublicationEligibility:
    eligible: bool
    reason: str
    candidate_id: str
    existing_facility_id: str | None = None


@dataclass(frozen=True)
class CompletionDecision:
    state: str
    terminal: bool
    progress_percent: int
    counts: dict[str, int]


async def publication_eligibility(
    db: AsyncSession, *, organization_id: str, execution_id: str,
    candidate_id: str,
) -> PublicationEligibility:
    candidate = await db.scalar(select(ScrapingFacilityCandidate).where(
        ScrapingFacilityCandidate.id == candidate_id,
        ScrapingFacilityCandidate.organization_id == organization_id,
        ScrapingFacilityCandidate.execution_id == execution_id,
    ))
    if candidate is None:
        return PublicationEligibility(False, "ownership_mismatch", candidate_id)
    if candidate.staging_status != FacilityCandidateStagingStatus.EXTRACTED:
        return PublicationEligibility(False, "candidate_state_invalid", candidate_id)
    attempt = await db.scalar(select(ScrapingFacilityExtractionAttempt).where(
        ScrapingFacilityExtractionAttempt.id == candidate.extraction_attempt_id,
        ScrapingFacilityExtractionAttempt.organization_id == organization_id,
        ScrapingFacilityExtractionAttempt.execution_id == execution_id,
    ))
    if attempt is None or attempt.status != FacilityExtractionAttemptStatus.SUCCEEDED:
        return PublicationEligibility(False, "candidate_state_invalid", candidate_id)
    evidence_count = int(await db.scalar(
        select(func.count()).select_from(ScrapingFacilityCandidateEvidence).where(
            ScrapingFacilityCandidateEvidence.organization_id == organization_id,
            ScrapingFacilityCandidateEvidence.execution_id == execution_id,
            ScrapingFacilityCandidateEvidence.facility_candidate_id == candidate_id,
            ScrapingFacilityCandidateEvidence.verification_status
            == FacilityCandidateEvidenceVerificationStatus.VERIFIED,
        )
    ) or 0)
    if evidence_count == 0:
        return PublicationEligibility(False, "candidate_evidence_missing", candidate_id)
    verified_name = int(await db.scalar(
        select(func.count()).select_from(ScrapingFacilityCandidateEvidence).where(
            ScrapingFacilityCandidateEvidence.organization_id == organization_id,
            ScrapingFacilityCandidateEvidence.execution_id == execution_id,
            ScrapingFacilityCandidateEvidence.facility_candidate_id == candidate_id,
            ScrapingFacilityCandidateEvidence.field_name == "name",
            ScrapingFacilityCandidateEvidence.verification_status
            == FacilityCandidateEvidenceVerificationStatus.VERIFIED,
        )
    ) or 0)
    if verified_name == 0:
        return PublicationEligibility(False, "candidate_evidence_missing", candidate_id)
    decision = await db.scalar(select(ScrapingFacilityCandidateDecision).where(
        ScrapingFacilityCandidateDecision.organization_id == organization_id,
        ScrapingFacilityCandidateDecision.execution_id == execution_id,
        ScrapingFacilityCandidateDecision.facility_candidate_id == candidate_id,
    ))
    if decision is None:
        return PublicationEligibility(False, "country_decision_missing", candidate_id)
    if decision.final_status == "needs_review":
        return PublicationEligibility(False, "needs_review", candidate_id)
    if decision.final_status == "rejected":
        return PublicationEligibility(False, "rejected", candidate_id)
    if (
        decision.final_status != "accepted"
        or decision.country_decision != "inside_requested_country"
    ):
        return PublicationEligibility(False, "candidate_state_invalid", candidate_id)
    dedup_job = await db.scalar(select(ScrapingFacilityPhaseWorkJob).where(
        ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
        ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
        ScrapingFacilityPhaseWorkJob.facility_candidate_id == candidate_id,
        ScrapingFacilityPhaseWorkJob.work_kind == "deduplicate_candidate",
    ))
    if dedup_job is None or dedup_job.status in ACTIVE_JOB_STATUSES:
        return PublicationEligibility(False, "deduplication_pending", candidate_id)
    if dedup_job.status != "succeeded":
        return PublicationEligibility(False, "deduplication_failed", candidate_id)
    possible = await db.scalar(select(ScrapingFacilityCandidateDuplicate.id).where(
        ScrapingFacilityCandidateDuplicate.organization_id == organization_id,
        ScrapingFacilityCandidateDuplicate.execution_id == execution_id,
        ScrapingFacilityCandidateDuplicate.relationship == "probable_duplicate",
        (
            (ScrapingFacilityCandidateDuplicate.left_candidate_id == candidate_id)
            | (ScrapingFacilityCandidateDuplicate.right_candidate_id == candidate_id)
        ),
    ).limit(1))
    if possible is not None:
        return PublicationEligibility(
            False, "possible_duplicate_requires_review", candidate_id
        )
    existing = await db.scalar(select(ScrapingFacilityCandidatePublication).where(
        ScrapingFacilityCandidatePublication.organization_id == organization_id,
        ScrapingFacilityCandidatePublication.execution_id == execution_id,
        ScrapingFacilityCandidatePublication.facility_candidate_id == candidate_id,
    ))
    if existing is not None and existing.status in {
        FacilityCandidatePublicationStatus.PUBLISHED,
        FacilityCandidatePublicationStatus.SKIPPED,
    }:
        return PublicationEligibility(
            False, "already_published", candidate_id, existing.final_facility_id
        )
    canonical_id = decision.canonical_candidate_id
    if canonical_id and canonical_id != candidate_id:
        canonical = await db.scalar(select(ScrapingFacilityCandidatePublication).where(
            ScrapingFacilityCandidatePublication.organization_id == organization_id,
            ScrapingFacilityCandidatePublication.execution_id == execution_id,
            ScrapingFacilityCandidatePublication.facility_candidate_id == canonical_id,
            ScrapingFacilityCandidatePublication.status
            == FacilityCandidatePublicationStatus.PUBLISHED,
        ))
        if canonical is None or canonical.final_facility_id is None:
            return PublicationEligibility(
                False, "exact_duplicate_waiting_for_canonical", candidate_id
            )
        return PublicationEligibility(
            True, "exact_duplicate_reused", candidate_id, canonical.final_facility_id
        )
    return PublicationEligibility(True, "eligible", candidate_id)


async def seed_publication_jobs(
    db: AsyncSession, *, organization_id: str, execution_id: str,
) -> dict[str, int]:
    candidate_ids = list((await db.scalars(
        select(ScrapingFacilityCandidate.id).where(
            ScrapingFacilityCandidate.organization_id == organization_id,
            ScrapingFacilityCandidate.execution_id == execution_id,
        ).order_by(ScrapingFacilityCandidate.id)
    )).all())
    counts = {"created": 0, "eligible": 0, "ineligible": 0}
    for candidate_id in candidate_ids:
        policy = await publication_eligibility(
            db, organization_id=organization_id, execution_id=execution_id,
            candidate_id=candidate_id,
        )
        if not policy.eligible:
            counts["ineligible"] += 1
            continue
        counts["eligible"] += 1
        counts["created"] += bool(await create_job(
            db, organization_id=organization_id, execution_id=execution_id,
            work_kind="publish_candidate", facility_candidate_id=candidate_id,
            metadata={"eligibility_reason": policy.reason},
        ))
    await db.commit()
    return counts


async def seed_package_b_continuations(
    db: AsyncSession, *, organization_id: str, execution_id: str,
) -> dict[str, int]:
    """Seed the next deterministic stage without treating a bounded slice as completion."""
    publication = await seed_publication_jobs(
        db, organization_id=organization_id, execution_id=execution_id
    )
    progress = await package_b_progress(
        db, organization_id=organization_id, execution_id=execution_id
    )
    created_export = 0
    created_finalization = 0
    publication_active = int(await db.scalar(
        select(func.count()).select_from(ScrapingFacilityPhaseWorkJob).where(
            ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
            ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
            ScrapingFacilityPhaseWorkJob.work_kind == "publish_candidate",
            ScrapingFacilityPhaseWorkJob.status.in_(ACTIVE_JOB_STATUSES),
        )
    ) or 0)
    if (
        publication_active == 0
        and progress.counts["active_jobs"] == 0
        and progress.counts["pending_verification"] == 0
        and progress.counts["pending_publication"] == 0
    ):
        has_results = bool(
            progress.counts["published"]
            or progress.counts["existing_facility_reused"]
        )
        if has_results and progress.counts["export_succeeded"] == 0:
            created_export = bool(await create_job(
                db, organization_id=organization_id, execution_id=execution_id,
                work_kind="generate_execution_export",
            ))
        elif not has_results or progress.counts["export_succeeded"]:
            created_finalization = bool(await create_job(
                db, organization_id=organization_id, execution_id=execution_id,
                work_kind="finalize_execution",
            ))
    await db.commit()
    return {
        **publication,
        "export_jobs_created": int(created_export),
        "finalization_jobs_created": int(created_finalization),
    }


async def publish_claimed_candidate(
    db: AsyncSession, *, organization_id: str, execution_id: str,
    candidate_id: str, job_id: str, claim_token: str,
) -> dict[str, str | None]:
    live_claim = await db.scalar(select(ScrapingFacilityPhaseWorkJob.id).where(
        ScrapingFacilityPhaseWorkJob.id == job_id,
        ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
        ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
        ScrapingFacilityPhaseWorkJob.facility_candidate_id == candidate_id,
        ScrapingFacilityPhaseWorkJob.work_kind == "publish_candidate",
        ScrapingFacilityPhaseWorkJob.status == "running",
        ScrapingFacilityPhaseWorkJob.claim_token == claim_token,
        ScrapingFacilityPhaseWorkJob.lease_expires_at >= func.now(),
    ))
    if live_claim is None:
        return {
            "status": "stale_claim", "reason": "stale_claim", "facility_id": None
        }
    policy = await publication_eligibility(
        db, organization_id=organization_id, execution_id=execution_id,
        candidate_id=candidate_id,
    )
    if not policy.eligible:
        return {"status": "ineligible", "reason": policy.reason, "facility_id": None}
    if policy.reason == "exact_duplicate_reused":
        statement = insert(ScrapingFacilityCandidatePublication).values(
            organization_id=organization_id,
            execution_id=execution_id,
            facility_candidate_id=candidate_id,
            final_facility_id=policy.existing_facility_id,
            normalization_version="package-b-v1",
            status=FacilityCandidatePublicationStatus.SKIPPED,
            reason_code="exact_duplicate_reused",
            metadata_json={"resolution": "existing_facility"},
            started_at=func.now(),
            completed_at=func.now(),
        ).on_conflict_do_nothing(
            constraint="uq_facility_candidate_publication_candidate"
        )
        await db.execute(statement)
        await db.commit()
        return {
            "status": "existing_facility_reused",
            "reason": policy.reason,
            "facility_id": policy.existing_facility_id,
        }
    summary = await facility_candidate_publication_service.publish_one_candidate(
        db,
        FacilityCandidatePublicationContext(
            organization_id=organization_id,
            execution_id=execution_id,
            facility_candidate_id=candidate_id,
        ),
    )
    return {
        "status": summary.status,
        "reason": summary.reason_code,
        "facility_id": summary.final_facility_id,
    }


async def package_b_progress(
    db: AsyncSession, *, organization_id: str, execution_id: str,
) -> CompletionDecision:
    async def count(model, *criteria) -> int:
        return int(await db.scalar(
            select(func.count()).select_from(model).where(*criteria)
        ) or 0)

    owner = (
        ScrapingFacilityCandidate.organization_id == organization_id,
        ScrapingFacilityCandidate.execution_id == execution_id,
    )
    candidates = await count(ScrapingFacilityCandidate, *owner)
    source_documents = await count(
        ScrapingSourceDocument,
        ScrapingSourceDocument.organization_id == organization_id,
        ScrapingSourceDocument.execution_id == execution_id,
    )
    prepared_documents = await count(
        ScrapingSourceDocumentText,
        ScrapingSourceDocumentText.organization_id == organization_id,
        ScrapingSourceDocumentText.execution_id == execution_id,
        ScrapingSourceDocumentText.preparation_status == "prepared",
    )
    accepted = await count(
        ScrapingFacilityCandidateDecision,
        ScrapingFacilityCandidateDecision.organization_id == organization_id,
        ScrapingFacilityCandidateDecision.execution_id == execution_id,
        ScrapingFacilityCandidateDecision.final_status == "accepted",
    )
    review = await count(
        ScrapingFacilityCandidateDecision,
        ScrapingFacilityCandidateDecision.organization_id == organization_id,
        ScrapingFacilityCandidateDecision.execution_id == execution_id,
        ScrapingFacilityCandidateDecision.final_status == "needs_review",
    )
    rejected = await count(
        ScrapingFacilityCandidateDecision,
        ScrapingFacilityCandidateDecision.organization_id == organization_id,
        ScrapingFacilityCandidateDecision.execution_id == execution_id,
        ScrapingFacilityCandidateDecision.final_status == "rejected",
    )
    published = await count(
        ScrapingFacilityCandidatePublication,
        ScrapingFacilityCandidatePublication.organization_id == organization_id,
        ScrapingFacilityCandidatePublication.execution_id == execution_id,
        ScrapingFacilityCandidatePublication.status
        == FacilityCandidatePublicationStatus.PUBLISHED,
    )
    reused = await count(
        ScrapingFacilityCandidatePublication,
        ScrapingFacilityCandidatePublication.organization_id == organization_id,
        ScrapingFacilityCandidatePublication.execution_id == execution_id,
        ScrapingFacilityCandidatePublication.reason_code == "exact_duplicate_reused",
    )
    failed_publications = await count(
        ScrapingFacilityCandidatePublication,
        ScrapingFacilityCandidatePublication.organization_id == organization_id,
        ScrapingFacilityCandidatePublication.execution_id == execution_id,
        ScrapingFacilityCandidatePublication.status
        == FacilityCandidatePublicationStatus.FAILED,
    )
    active_jobs = await count(
        ScrapingFacilityPhaseWorkJob,
        ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
        ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
        ScrapingFacilityPhaseWorkJob.status.in_(ACTIVE_JOB_STATUSES),
        ScrapingFacilityPhaseWorkJob.work_kind != "finalize_execution",
    )
    failed_jobs = await count(
        ScrapingFacilityPhaseWorkJob,
        ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
        ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
        ScrapingFacilityPhaseWorkJob.status == "failed",
    )
    export_succeeded = await count(
        ScrapingExecutionExport,
        ScrapingExecutionExport.organization_id == organization_id,
        ScrapingExecutionExport.execution_id == execution_id,
        ScrapingExecutionExport.status == "succeeded",
    )
    decided = accepted + review + rejected
    accepted_ids = list((await db.scalars(
        select(ScrapingFacilityCandidateDecision.facility_candidate_id).where(
            ScrapingFacilityCandidateDecision.organization_id == organization_id,
            ScrapingFacilityCandidateDecision.execution_id == execution_id,
            ScrapingFacilityCandidateDecision.final_status == "accepted",
        )
    )).all())
    pending_publication = 0
    eligible_publication = 0
    duplicate_review = 0
    for candidate_id in accepted_ids:
        policy = await publication_eligibility(
            db, organization_id=organization_id, execution_id=execution_id,
            candidate_id=candidate_id,
        )
        pending_publication += int(
            policy.eligible
            or policy.reason == "exact_duplicate_waiting_for_canonical"
        )
        eligible_publication += int(policy.eligible)
        duplicate_review += int(
            policy.reason == "possible_duplicate_requires_review"
        )
    counts = {
        "source_documents": source_documents,
        "prepared_documents": prepared_documents,
        "extracted_candidates": candidates,
        "candidates": candidates, "accepted": accepted, "needs_review": review,
        "rejected": rejected, "published": published, "existing_facility_reused": reused,
        "publication_failures": failed_publications, "active_jobs": active_jobs,
        "failed_jobs": failed_jobs, "export_succeeded": export_succeeded,
        "pending_verification": max(candidates - decided, 0),
        "pending_publication": pending_publication,
        "eligible_publication": eligible_publication,
        "possible_duplicate_review": duplicate_review,
    }
    if failed_jobs or failed_publications:
        state, terminal = "failed", True
    elif active_jobs or counts["pending_verification"] or counts["pending_publication"]:
        state, terminal = "in_progress", False
    elif (published or reused) and not export_succeeded:
        state, terminal = "in_progress", False
    elif review or duplicate_review:
        state, terminal = "completed_with_review_required", True
    elif published or reused:
        state, terminal = "completed_with_results", True
    else:
        state, terminal = "completed_no_results", True
    denominator = max(candidates + 1, 1)
    resolved = min(decided, candidates) + int(bool(export_succeeded))
    progress = 100 if terminal else min(99, int(100 * resolved / denominator))
    return CompletionDecision(state, terminal, progress, counts)


async def build_execution_export(
    db: AsyncSession, *, organization_id: str, execution_id: str,
) -> tuple[bytes, str]:
    return await execution_export_service.build_workbook_for_organization(
        db,
        organization_id=organization_id,
        execution_id=execution_id,
        allow_nonterminal=True,
    )


async def persist_execution_export(
    db: AsyncSession, *, organization_id: str, execution_id: str,
    payload: bytes, filename: str, job_id: str, claim_token: str,
) -> ScrapingExecutionExport | None:
    live_claim = await db.scalar(select(ScrapingFacilityPhaseWorkJob.id).where(
        ScrapingFacilityPhaseWorkJob.id == job_id,
        ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
        ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
        ScrapingFacilityPhaseWorkJob.work_kind == "generate_execution_export",
        ScrapingFacilityPhaseWorkJob.status == "running",
        ScrapingFacilityPhaseWorkJob.claim_token == claim_token,
        ScrapingFacilityPhaseWorkJob.lease_expires_at >= func.now(),
    ))
    if live_claim is None:
        await db.rollback()
        return None
    digest = hashlib.sha256(payload).hexdigest()
    statement = insert(ScrapingExecutionExport).values(
        organization_id=organization_id,
        execution_id=execution_id,
        export_kind="xlsx",
        status="succeeded",
        filename=filename,
        content_type=MIME_XLSX,
        artifact_sha256=digest,
        artifact_bytes=payload,
        completed_at=func.now(),
        metadata_json={"generator": "execution_export_service"},
    ).on_conflict_do_update(
        constraint="uq_scraping_execution_export_kind",
        set_={
            "status": "succeeded",
            "filename": filename,
            "content_type": MIME_XLSX,
            "artifact_sha256": digest,
            "artifact_bytes": payload,
            "completed_at": func.now(),
            "failure_classification": None,
            "metadata_json": {"generator": "execution_export_service"},
        },
    ).returning(ScrapingExecutionExport.id)
    export_id = await db.scalar(statement)
    await db.commit()
    return await db.get(ScrapingExecutionExport, export_id)


async def finalize_execution(
    db: AsyncSession, *, organization_id: str, execution_id: str,
    job_id: str, claim_token: str,
) -> CompletionDecision:
    live_claim = await db.scalar(select(ScrapingFacilityPhaseWorkJob.id).where(
        ScrapingFacilityPhaseWorkJob.id == job_id,
        ScrapingFacilityPhaseWorkJob.organization_id == organization_id,
        ScrapingFacilityPhaseWorkJob.execution_id == execution_id,
        ScrapingFacilityPhaseWorkJob.work_kind == "finalize_execution",
        ScrapingFacilityPhaseWorkJob.status == "running",
        ScrapingFacilityPhaseWorkJob.claim_token == claim_token,
        ScrapingFacilityPhaseWorkJob.lease_expires_at >= func.now(),
    ))
    if live_claim is None:
        raise RuntimeError("stale_claim")
    decision = await package_b_progress(
        db, organization_id=organization_id, execution_id=execution_id
    )
    execution = await db.scalar(select(ScrapingExecution).where(
        ScrapingExecution.id == execution_id,
        ScrapingExecution.organization_id == organization_id,
    ).with_for_update())
    if execution is None:
        raise LookupError("owned execution not found")
    execution.progress_percent = decision.progress_percent
    execution.latest_message = decision.state
    execution.current_stage = "package_b"
    if decision.terminal:
        execution.status = (
            ScrapingExecutionStatus.FAILED
            if decision.state == "failed"
            else ScrapingExecutionStatus.COMPLETED
        )
        execution.completed_at = datetime.now(UTC)
        await execution_service.emit_event(
            db,
            execution_id,
            "package_b_completed" if decision.state != "failed" else "package_b_failed",
            decision.state,
            metadata={
                "completion_state": decision.state,
                "counts": decision.counts,
                "python_authoritative": True,
            },
        )
    await db.commit()
    return decision
