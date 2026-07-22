"""Scraping execution campaign endpoints."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import AsyncSessionLocal, get_db
from app.schemas.api import (
    ScrapingCoverageCellResponse,
    ScrapingEventResponse,
    ScrapingExecutionDetail,
    ScrapingExecutionSummary,
    ScrapingFacilitySummary,
    ScrapingTaskResponse,
    FacilityCandidateAuditResponse,
    FacilityCandidateEvidenceAuditResponse,
    FacilityCandidatePublicationAuditResponse,
    FacilityExtractionAttemptAuditResponse,
    PreparedSourceTextAuditResponse,
    SourceCandidateResponse,
    SourceDiscoveryQueryResponse,
    SourceDocumentResponse,
    SourceDocumentChunkAuditResponse,
    SourceRetrievalAttemptResponse,
)
from app.services.scraping.execution_export_service import MIME_XLSX, execution_export_service
from app.services.scraping.execution_service import execution_service
from app.services.scraping.facility_candidate_publication_service import (
    facility_candidate_publication_service,
)
from app.services.scraping.facility_extraction_service import facility_extraction_service
from app.services.scraping.source_discovery_service import source_discovery_service
from app.services.scraping.source_retrieval_service import source_retrieval_service

router = APIRouter()


def _preview(value: str, limit: int) -> str:
    compact = " ".join((value or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


@router.get("/{execution_id}", response_model=ScrapingExecutionDetail)
async def get_execution(
    execution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await execution_service.get_detail(db, auth, execution_id)


@router.get("/{execution_id}/tasks", response_model=list[ScrapingTaskResponse])
async def list_tasks(
    execution_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    execution_agent_id: str | None = None,
    task_type: str | None = None,
    coverage_cell_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await execution_service.list_tasks(
        db,
        auth,
        execution_id,
        status=status_filter,
        execution_agent_id=execution_agent_id,
        task_type=task_type,
        coverage_cell_id=coverage_cell_id,
        limit=limit,
        offset=offset,
    )


@router.get("/{execution_id}/coverage", response_model=list[ScrapingCoverageCellResponse])
async def list_coverage(
    execution_id: str,
    status_filter: str | None = Query(default=None, alias="status"),
    region: str | None = None,
    language: str | None = None,
    source_category: str | None = None,
    execution_agent_id: str | None = None,
    limit: int = 200,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await execution_service.list_coverage(
        db,
        auth,
        execution_id,
        status=status_filter,
        region=region,
        language=language,
        source_category=source_category,
        execution_agent_id=execution_agent_id,
        limit=limit,
        offset=offset,
    )


@router.get("/{execution_id}/events", response_model=list[ScrapingEventResponse])
async def list_events(
    execution_id: str,
    after_sequence: int | None = None,
    limit: int = 200,
    execution_agent_id: str | None = None,
    event_type: str | None = None,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await execution_service.list_events(
        db,
        auth,
        execution_id,
        after_sequence=after_sequence,
        limit=limit,
        execution_agent_id=execution_agent_id,
        event_type=event_type,
    )


@router.get("/{execution_id}/facilities", response_model=list[ScrapingFacilitySummary])
async def list_facilities(
    execution_id: str,
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await execution_service.list_facilities(
        db,
        auth,
        execution_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{execution_id}/source-discovery-queries",
    response_model=list[SourceDiscoveryQueryResponse],
)
async def list_source_discovery_queries(
    execution_id: str,
    coverage_cell_id: str | None = None,
    provider: str | None = None,
    source_category: str | None = None,
    language_code: str | None = None,
    region_code: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await source_discovery_service.list_queries(
        db,
        auth,
        execution_id,
        coverage_cell_id=coverage_cell_id,
        provider=provider,
        source_category=source_category,
        language_code=language_code,
        region_code=region_code,
        status=status_filter,
        limit=limit,
        offset=offset,
    )


@router.get("/{execution_id}/source-candidates", response_model=list[SourceCandidateResponse])
async def list_source_candidates(
    execution_id: str,
    coverage_cell_id: str | None = None,
    provider: str | None = None,
    source_category: str | None = None,
    language_code: str | None = None,
    region_code: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    domain: str | None = None,
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await source_discovery_service.list_candidates(
        db,
        auth,
        execution_id,
        coverage_cell_id=coverage_cell_id,
        provider=provider,
        source_category=source_category,
        language_code=language_code,
        region_code=region_code,
        status=status_filter,
        domain=domain,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{execution_id}/retrieval-attempts",
    response_model=list[SourceRetrievalAttemptResponse],
)
async def list_source_retrieval_attempts(
    execution_id: str,
    source_candidate_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    attempts = await source_retrieval_service.list_attempts(
        db,
        auth,
        execution_id,
        source_candidate_id=source_candidate_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return [
        SourceRetrievalAttemptResponse(
            id=row.id,
            organization_id=row.organization_id,
            execution_id=row.execution_id,
            source_candidate_id=row.source_candidate_id,
            coverage_cell_id=row.coverage_cell_id,
            task_id=row.task_id,
            status=row.status.value,
            requested_url=row.requested_url,
            final_url=row.final_url,
            redirect_count=row.redirect_count,
            http_status=row.http_status,
            content_type=row.content_type,
            declared_content_length=row.declared_content_length,
            bytes_received=row.bytes_received,
            robots_status=row.robots_status.value if row.robots_status else None,
            failure_classification=row.failure_classification,
            safe_error_message=row.safe_error_message,
            started_at=row.started_at,
            completed_at=row.completed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in attempts
    ]


@router.get(
    "/{execution_id}/source-documents",
    response_model=list[SourceDocumentResponse],
)
async def list_source_documents(
    execution_id: str,
    source_candidate_id: str | None = None,
    content_sha256: str | None = None,
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    documents = await source_retrieval_service.list_documents(
        db,
        auth,
        execution_id,
        source_candidate_id=source_candidate_id,
        content_sha256=content_sha256,
        limit=limit,
        offset=offset,
    )
    return [
        SourceDocumentResponse(
            id=row.id,
            organization_id=row.organization_id,
            execution_id=row.execution_id,
            source_candidate_id=row.source_candidate_id,
            retrieval_attempt_id=row.retrieval_attempt_id,
            final_url=row.final_url,
            content_type=row.content_type,
            charset=row.charset,
            content_sha256=row.content_sha256,
            byte_size=row.byte_size,
            retrieval_timestamp=row.retrieval_timestamp,
            metadata_json=row.metadata_json,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in documents
    ]


@router.get(
    "/{execution_id}/prepared-source-texts",
    response_model=list[PreparedSourceTextAuditResponse],
)
async def list_prepared_source_texts(
    execution_id: str,
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await facility_extraction_service.list_prepared_texts(
        db, auth, execution_id, limit=limit, offset=offset
    )
    return [
        PreparedSourceTextAuditResponse(
            id=row.id,
            source_document_id=row.source_document_id,
            source_candidate_id=row.source_candidate_id,
            coverage_cell_id=row.coverage_cell_id,
            parser_version=row.parser_version,
            title=row.title,
            status=row.preparation_status.value,
            failure_classification=row.failure_classification,
            character_count=row.character_count,
            original_character_count=row.original_character_count,
            truncated=row.truncated,
            prepared_text_hash_prefix=row.prepared_text_hash[:12],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.get(
    "/{execution_id}/source-document-chunks",
    response_model=list[SourceDocumentChunkAuditResponse],
)
async def list_source_document_chunks(
    execution_id: str,
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await facility_extraction_service.list_chunks(db, auth, execution_id, limit=limit, offset=offset)
    return [
        SourceDocumentChunkAuditResponse(
            id=row.id,
            source_document_id=row.source_document_id,
            prepared_text_id=row.prepared_text_id,
            coverage_cell_id=row.coverage_cell_id,
            chunk_index=row.chunk_index,
            character_start=row.character_start,
            character_end=row.character_end,
            character_count=len(row.chunk_text),
            chunk_hash_prefix=row.chunk_hash[:12],
            preview=_preview(row.chunk_text, 240),
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get(
    "/{execution_id}/facility-extraction-attempts",
    response_model=list[FacilityExtractionAttemptAuditResponse],
)
async def list_facility_extraction_attempts(
    execution_id: str,
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await facility_extraction_service.list_attempts(db, auth, execution_id, limit=limit, offset=offset)
    return [
        FacilityExtractionAttemptAuditResponse(
            id=row.id,
            source_document_id=row.source_document_id,
            prepared_text_id=row.prepared_text_id,
            chunk_id=row.chunk_id,
            coverage_cell_id=row.coverage_cell_id,
            provider=row.provider,
            model=row.model,
            prompt_version=row.prompt_version,
            status=row.status.value,
            attempt_number=row.attempt_number,
            requested_at=row.requested_at,
            completed_at=row.completed_at,
            input_character_count=row.input_character_count,
            output_candidate_count=row.output_candidate_count,
            failure_classification=row.failure_classification,
            safe_error_message=row.safe_error_message,
        )
        for row in rows
    ]


@router.get("/{execution_id}/facility-candidates", response_model=list[FacilityCandidateAuditResponse])
async def list_facility_candidates(
    execution_id: str,
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await facility_extraction_service.list_candidates(db, auth, execution_id, limit=limit, offset=offset)
    return [
        FacilityCandidateAuditResponse(
            id=row.id,
            coverage_cell_id=row.coverage_cell_id,
            source_document_id=row.source_document_id,
            prepared_text_id=row.prepared_text_id,
            chunk_id=row.chunk_id,
            extraction_attempt_id=row.extraction_attempt_id,
            raw_name=row.raw_name,
            model_confidence=float(row.model_confidence) if row.model_confidence is not None else None,
            staging_status=row.staging_status.value,
            candidate_fingerprint_prefix=row.candidate_fingerprint[:12],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.get(
    "/{execution_id}/facility-candidate-evidence",
    response_model=list[FacilityCandidateEvidenceAuditResponse],
)
async def list_facility_candidate_evidence(
    execution_id: str,
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await facility_extraction_service.list_evidence(db, auth, execution_id, limit=limit, offset=offset)
    return [
        FacilityCandidateEvidenceAuditResponse(
            id=row.id,
            facility_candidate_id=row.facility_candidate_id,
            source_document_id=row.source_document_id,
            prepared_text_id=row.prepared_text_id,
            chunk_id=row.chunk_id,
            field_name=row.field_name,
            raw_value_preview=_preview(str(row.raw_value), 240) if row.raw_value is not None else None,
            evidence_quote=_preview(row.evidence_quote, 1000),
            quote_start=row.quote_start,
            quote_end=row.quote_end,
            evidence_hash_prefix=row.evidence_hash[:12],
            verification_status=row.verification_status.value,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get(
    "/{execution_id}/facility-candidate-publications",
    response_model=list[FacilityCandidatePublicationAuditResponse],
)
async def list_facility_candidate_publications(
    execution_id: str,
    limit: int = 100,
    offset: int = 0,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    rows = await facility_candidate_publication_service.list_publications(
        db, auth, execution_id, limit=limit, offset=offset
    )
    return [
        FacilityCandidatePublicationAuditResponse(
            id=row.id,
            facility_candidate_id=row.facility_candidate_id,
            final_facility_id=row.final_facility_id,
            status=row.status.value,
            reason_code=row.reason_code,
            normalization_version=row.normalization_version,
            metadata_json=row.metadata_json,
            started_at=row.started_at,
            completed_at=row.completed_at,
            published_at=row.published_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


@router.get("/{execution_id}/export.xlsx")
async def export_execution_workbook(
    execution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    payload, filename = await execution_export_service.build_workbook(db, auth, execution_id)
    return Response(
        content=payload,
        media_type=MIME_XLSX,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{execution_id}/events/stream")
async def stream_events(
    request: Request,
    execution_id: str,
    after_sequence: int | None = None,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await execution_service.get_detail(db, auth, execution_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        last_sequence = after_sequence or 0
        heartbeat_tick = 0
        while True:
            if await request.is_disconnected():
                return
            async with AsyncSessionLocal() as stream_db:
                events = await execution_service.list_events(
                    stream_db,
                    auth,
                    execution_id,
                    after_sequence=last_sequence,
                    limit=100,
                )
            for event in events:
                last_sequence = max(last_sequence, event.sequence_number)
                payload = json.dumps(event.model_dump(mode="json"))
                yield f"id: {event.sequence_number}\nevent: {event.event_type}\ndata: {payload}\n\n"
            heartbeat_tick += 1
            if heartbeat_tick >= 15:
                heartbeat_tick = 0
                yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{execution_id}/cancel", response_model=ScrapingExecutionSummary)
async def cancel_execution(
    execution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await execution_service.cancel_execution(db, auth, execution_id)


@router.delete("/{execution_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_execution(
    execution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await execution_service.delete_execution(db, auth, execution_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
