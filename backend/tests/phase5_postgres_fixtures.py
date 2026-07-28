"""Shared PostgreSQL fixtures for Phase 5 claim/persistence contract tests."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    ScrapingSourceDocument,
    ScrapingSourceRetrievalAttempt,
    SourceRetrievalAttemptStatus,
)
from app.services.scraping.directory_expansion_service import (
    compute_prepared_content_fingerprint,
)
from app.services.scraping.phase5_contracts import (
    Phase5PersistenceResult,
    Phase5WorkKind,
    PreparedRetrievalResult,
    prepare_phase5_job,
    retrieval_result_fingerprint,
)
from app.services.scraping.phase5_job_service import (
    ClaimedPhase5Job,
    claim_batch,
    complete_job,
    create_job_idempotently,
    persist_retrieval_result,
)
from app.services.scraping.phase5_retrieval_service import prepare_resource


@dataclass(frozen=True)
class Phase5RetrievalSeedBundle:
    organization_id: str
    execution_id: str
    crawl_node_id: str
    retrieval_job_id: str
    claim: ClaimedPhase5Job
    source_document_id: str
    retrieval_result_id: str
    content_fingerprint: str


async def fetch_database_now(session: AsyncSession) -> datetime:
    return await session.scalar(select(func.clock_timestamp()))


async def assert_claim_live(session: AsyncSession, claim: ClaimedPhase5Job) -> None:
    assert claim.claim_token is not None
    db_now = await fetch_database_now(session)
    assert claim.lease_expires_at > db_now


def assert_retrieval_persistence(
    result: Phase5PersistenceResult,
    *,
    allow_existing: bool = False,
) -> str:
    if allow_existing:
        assert result.outcome in {"persisted", "existing"}
    else:
        assert result.outcome == "persisted"
    assert result.record_id is not None
    return result.record_id


def assert_concurrent_retrieval_persistence(
    results: tuple[Phase5PersistenceResult, ...],
) -> str:
    assert len(results) == 2
    outcomes = {item.outcome for item in results}
    assert outcomes <= {"persisted", "existing"}
    assert len({item.record_id for item in results}) == 1
    record_id = results[0].record_id
    assert record_id is not None
    assert results[1].record_id == record_id
    return record_id


async def _persist_owned_source_document(
    session: AsyncSession,
    *,
    organization_id: str,
    execution_id: str,
    source_candidate_id: str,
    final_url: str,
    content_type: str,
    content_text: str,
    fetched_at: datetime,
    idempotency_suffix: str,
) -> ScrapingSourceDocument:
    attempt_key = f"phase5-fixture:{idempotency_suffix}"
    attempt = await session.scalar(select(ScrapingSourceRetrievalAttempt).where(
        ScrapingSourceRetrievalAttempt.organization_id == organization_id,
        ScrapingSourceRetrievalAttempt.idempotency_key == attempt_key))
    if attempt is None:
        attempt = ScrapingSourceRetrievalAttempt(
            organization_id=organization_id,
            execution_id=execution_id,
            source_candidate_id=source_candidate_id,
            status=SourceRetrievalAttemptStatus.SUCCEEDED,
            requested_url=final_url,
            final_url=final_url,
            redirect_count=0,
            http_status=200,
            content_type=content_type,
            bytes_received=len(content_text.encode()),
            started_at=fetched_at,
            completed_at=fetched_at,
            idempotency_key=attempt_key,
            metadata_json={"fixture": "phase5_postgres"},
        )
        session.add(attempt)
        await session.flush()
    document = await session.scalar(select(ScrapingSourceDocument).where(
        ScrapingSourceDocument.retrieval_attempt_id == attempt.id))
    if document is None:
        encoded = content_text.encode()
        document = ScrapingSourceDocument(
            organization_id=organization_id,
            execution_id=execution_id,
            source_candidate_id=source_candidate_id,
            retrieval_attempt_id=attempt.id,
            final_url=final_url,
            content_type=content_type,
            content_sha256=hashlib.sha256(encoded).hexdigest(),
            content_text=content_text,
            byte_size=len(encoded),
            retrieval_timestamp=fetched_at,
            metadata_json={"storage": "immutable_source_document"},
        )
        session.add(document)
        await session.flush()
    return document


async def seed_phase5_retrieval_bundle(
    maker: async_sessionmaker[AsyncSession],
    *,
    organization_id: str,
    execution_id: str,
    crawl_node_id: str,
    source_candidate_id: str,
    listing_url: str,
    structured_json: dict[str, Any] | list[Any] | None = None,
    content_type: str = "application/json",
    decoded_content: str | None = None,
    resource_url: str | None = None,
    result_ordinal: int = 0,
    lease_duration: timedelta = timedelta(minutes=5),
    selected_tool: str = "http",
    retrieval_method: Phase5WorkKind = Phase5WorkKind.HTTP_RETRIEVAL,
    complete_retrieval_job: bool = True,
) -> Phase5RetrievalSeedBundle:
    """Seed retrieval job → DB-clock claim → matching document → persisted result."""
    resource_url = resource_url or listing_url
    if structured_json is not None:
        durable_text = json.dumps(structured_json, separators=(",", ":"))
        content_type = "application/json"
        decoded_content = None
    elif decoded_content is not None:
        durable_text = decoded_content
    else:
        raise ValueError("structured_json or decoded_content is required")
    content_fingerprint = compute_prepared_content_fingerprint(
        content_type=content_type,
        decoded_content=decoded_content,
        structured_json=structured_json,
    )

    async with maker.begin() as session:
        now = await fetch_database_now(session)
        retrieval_job = prepare_phase5_job(
            organization_id=organization_id,
            execution_id=execution_id,
            crawl_node_id=crawl_node_id,
            original_url=listing_url,
            source_classification="directory",
            work_kind=retrieval_method,
            selected_tool=selected_tool,
            requested_at=now,
        )
        retrieval_created = await create_job_idempotently(session, retrieval_job)
        claimed = await claim_batch(
            session,
            organization_id=organization_id,
            execution_id=execution_id,
            now=now,
            lease_duration=lease_duration,
            batch_size=1,
            selected_tool=selected_tool,
        )
        assert len(claimed) == 1
        claim = claimed[0]
        await assert_claim_live(session, claim)
        document = await _persist_owned_source_document(
            session,
            organization_id=organization_id,
            execution_id=execution_id,
            source_candidate_id=source_candidate_id,
            final_url=listing_url,
            content_type=content_type,
            content_text=durable_text,
            fetched_at=now,
            idempotency_suffix=f"{crawl_node_id}:{listing_url}",
        )
        resource = prepare_resource(
            requested_url=resource_url,
            final_url=resource_url,
            content_type=content_type,
            body=durable_text.encode(),
            retrieval_method=retrieval_method,
            requested_at=now,
            fetched_at=now,
            resource_role="page",
            result_ordinal=result_ordinal,
        )
        retrieval_result = PreparedRetrievalResult(
            job_id=retrieval_created.record_id,
            organization_id=organization_id,
            execution_id=execution_id,
            requested_url=resource_url,
            final_url=resource_url,
            http_status=200,
            content_type=content_type,
            content_length=len(durable_text.encode()),
            response_fingerprint=resource.response_fingerprint,
            result_fingerprint=retrieval_result_fingerprint(
                organization_id=organization_id,
                execution_id=execution_id,
                work_job_id=retrieval_created.record_id,
                retrieval_method=retrieval_method,
                resource_url=resource_url,
                resource_role="page",
                result_ordinal=result_ordinal,
            ),
            resource_role="page",
            result_ordinal=result_ordinal,
            retrieval_method=retrieval_method,
            fetched_at=now,
            source_document_id=document.id,
        )
        persisted = await persist_retrieval_result(
            session, claim_token=claim.claim_token, result=retrieval_result)
        retrieval_result_id = assert_retrieval_persistence(persisted)
        if complete_retrieval_job:
            await complete_job(
                session,
                job_id=claim.id,
                organization_id=organization_id,
                execution_id=execution_id,
                claim_token=claim.claim_token,
                completed_at=now,
            )
    return Phase5RetrievalSeedBundle(
        organization_id=organization_id,
        execution_id=execution_id,
        crawl_node_id=crawl_node_id,
        retrieval_job_id=retrieval_created.record_id,
        claim=claim,
        source_document_id=document.id,
        retrieval_result_id=retrieval_result_id,
        content_fingerprint=content_fingerprint,
    )
