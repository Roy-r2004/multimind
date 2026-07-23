"""Facility extraction staging service with deterministic evidence verification."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import (
    FacilityCandidateEvidenceVerificationStatus,
    FacilityCandidateStagingStatus,
    FacilityExtractionAttemptStatus,
    ScrapingExecution,
    ScrapingFacilityCandidate,
    ScrapingFacilityCandidateEvidence,
    ScrapingFacilityExtractionAttempt,
    ScrapingSourceDocument,
    ScrapingSourceDocumentChunk,
    ScrapingSourceDocumentText,
    SourceDocumentTextPreparationStatus,
)
from app.services.scraping.document_text_preparation_service import (
    SourceDocumentPreparationContext,
    document_text_preparation_service,
)
from app.services.scraping.facility_extraction_provider import (
    ExtractedEvidenceValue,
    ExtractedFacility,
    FacilityExtractionOutput,
    FacilityExtractionProvider,
    FacilityExtractionProviderResult,
    FacilityStructuredOutputError,
)
from app.services.scraping.openrouter_facility_extraction_provider import (
    FacilityProviderError,
    OpenRouterFacilityExtractionProvider,
)

MAX_IDEMPOTENCY_KEY_LENGTH = 160


class FacilityExtractionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str
    execution_id: str
    source_document_id: str
    prepared_text_id: str | None = None
    chunk_id: str | None = None
    coverage_cell_id: str | None = None
    chunk_index: int | None = Field(default=None, ge=0)
    language_hint: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=MAX_IDEMPOTENCY_KEY_LENGTH)


class FacilityExtractionSummary(BaseModel):
    attempt_id: str | None = None
    source_document_id: str
    chunk_id: str | None = None
    provider: str | None = None
    model: str | None = None
    status: str
    extracted_candidate_count: int = 0
    accepted_evidence_count: int = 0
    rejected_evidence_count: int = 0
    document_relevant: bool | None = None
    failure_classification: str | None = None


class FacilityExtractionService:
    def __init__(self, provider: FacilityExtractionProvider | None = None) -> None:
        self.provider = provider or OpenRouterFacilityExtractionProvider()

    async def extract_one_chunk(
        self,
        db: AsyncSession,
        context: FacilityExtractionContext,
    ) -> FacilityExtractionSummary:
        attempt_key = _attempt_idempotency_key(context.idempotency_key, self.provider)
        existing = await self._existing_attempt(db, context, attempt_key)
        if existing is not None and existing.completed_at is not None:
            return await self._summary_for_attempt(db, existing)

        document = await self._load_document(db, context)
        prepared = await self._ensure_prepared(db, context, document)
        if prepared.preparation_status != SourceDocumentTextPreparationStatus.PREPARED:
            return FacilityExtractionSummary(
                source_document_id=document.id,
                status="failed",
                failure_classification=prepared.failure_classification or "empty_prepared_text",
            )
        chunk = await self._select_chunk(db, context, prepared)
        # Materialize before any flush/rollback — expired chunk attrs raise MissingGreenlet.
        chunk_id = chunk.id
        chunk_text = chunk.chunk_text
        chunk_hash = chunk.chunk_hash
        document_id = document.id
        prepared_id = prepared.id
        prepared_coverage_cell_id = prepared.coverage_cell_id
        prepared_language = prepared.detected_language
        attempt = existing or ScrapingFacilityExtractionAttempt(
            organization_id=context.organization_id,
            execution_id=context.execution_id,
            source_document_id=document_id,
            prepared_text_id=prepared_id,
            chunk_id=chunk_id,
            coverage_cell_id=context.coverage_cell_id or prepared_coverage_cell_id,
            provider=self.provider.provider_name,
            model=self.provider.model,
            prompt_version=self.provider.prompt_version,
            status=FacilityExtractionAttemptStatus.RUNNING,
            attempt_number=1,
            idempotency_key=attempt_key,
            requested_at=datetime.now(UTC),
            input_character_count=len(chunk_text),
            output_candidate_count=0,
            metadata_json={
                "chunk_hash_prefix": chunk_hash[:12],
                "schema_version": self.provider.schema_version,
            },
        )
        if existing is None:
            db.add(attempt)
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                existing = await self._existing_attempt(db, context, attempt_key)
                if existing is not None and existing.completed_at is not None:
                    return await self._summary_for_attempt(db, existing)
                raise

        try:
            provider_result = await self.provider.extract(
                chunk_text=chunk_text,
                language_hint=context.language_hint or prepared_language,
            )
            if isinstance(provider_result, FacilityExtractionProviderResult):
                output = provider_result.output
                provider_diagnostics = provider_result.diagnostics
                provider_request_id = provider_result.provider_request_id
            else:
                output = provider_result
                provider_diagnostics = {}
                provider_request_id = None
            accepted, rejected = await self._persist_output(
                db, attempt, chunk_id=chunk_id, chunk_text=chunk_text, output=output
            )
            attempt.status = FacilityExtractionAttemptStatus.SUCCEEDED
            attempt.completed_at = datetime.now(UTC)
            attempt.output_candidate_count = len(output.facilities)
            attempt.failure_classification = None
            attempt.safe_error_message = None
            attempt.provider_request_id = provider_request_id
            attempt.metadata_json = {
                "document_relevant": output.document_relevant,
                "chunk_hash_prefix": chunk_hash[:12],
                "schema_version": self.provider.schema_version,
                "structured_output": _safe_metadata(provider_diagnostics),
            }
            await db.commit()
            await db.refresh(attempt)
            return FacilityExtractionSummary(
                attempt_id=attempt.id,
                source_document_id=document_id,
                chunk_id=chunk_id,
                provider=attempt.provider,
                model=attempt.model,
                status=attempt.status.value,
                extracted_candidate_count=accepted,
                accepted_evidence_count=await self._evidence_count(db, attempt.id),
                rejected_evidence_count=rejected,
                document_relevant=output.document_relevant,
            )
        except FacilityProviderError as exc:
            attempt.status = FacilityExtractionAttemptStatus.FAILED
            attempt.completed_at = datetime.now(UTC)
            attempt.failure_classification = exc.classification
            attempt.safe_error_message = exc.safe_message
            attempt.metadata_json = {
                "retryable": exc.retryable,
                "schema_version": self.provider.schema_version,
            }
            await db.commit()
            return await self._summary_for_attempt(db, attempt)
        except FacilityStructuredOutputError as exc:
            attempt.status = FacilityExtractionAttemptStatus.FAILED
            attempt.completed_at = datetime.now(UTC)
            attempt.failure_classification = "invalid_structured_output"
            attempt.safe_error_message = exc.safe_message[:500]
            attempt.metadata_json = {
                "schema_version": self.provider.schema_version,
                "structured_output": _safe_metadata(exc.diagnostics),
            }
            await db.commit()
            return await self._summary_for_attempt(db, attempt)
        except ValidationError:
            attempt.status = FacilityExtractionAttemptStatus.FAILED
            attempt.completed_at = datetime.now(UTC)
            attempt.failure_classification = "invalid_structured_output"
            attempt.safe_error_message = "Facility extraction returned invalid structured output"
            attempt.metadata_json = {"schema_version": self.provider.schema_version}
            await db.commit()
            return await self._summary_for_attempt(db, attempt)

    async def list_prepared_texts(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScrapingSourceDocumentText]:
        await self._assert_execution_access(db, auth, execution_id)
        result = await db.execute(
            select(ScrapingSourceDocumentText)
            .where(
                ScrapingSourceDocumentText.organization_id == auth.org_id,
                ScrapingSourceDocumentText.execution_id == execution_id,
            )
            .order_by(ScrapingSourceDocumentText.created_at.desc())
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 500))
        )
        return list(result.scalars().all())

    async def list_chunks(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScrapingSourceDocumentChunk]:
        await self._assert_execution_access(db, auth, execution_id)
        result = await db.execute(
            select(ScrapingSourceDocumentChunk)
            .where(
                ScrapingSourceDocumentChunk.organization_id == auth.org_id,
                ScrapingSourceDocumentChunk.execution_id == execution_id,
            )
            .order_by(
                ScrapingSourceDocumentChunk.source_document_id,
                ScrapingSourceDocumentChunk.chunk_index,
            )
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 500))
        )
        return list(result.scalars().all())

    async def list_attempts(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScrapingFacilityExtractionAttempt]:
        await self._assert_execution_access(db, auth, execution_id)
        result = await db.execute(
            select(ScrapingFacilityExtractionAttempt)
            .where(
                ScrapingFacilityExtractionAttempt.organization_id == auth.org_id,
                ScrapingFacilityExtractionAttempt.execution_id == execution_id,
            )
            .order_by(ScrapingFacilityExtractionAttempt.requested_at.desc())
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 500))
        )
        return list(result.scalars().all())

    async def list_candidates(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScrapingFacilityCandidate]:
        await self._assert_execution_access(db, auth, execution_id)
        result = await db.execute(
            select(ScrapingFacilityCandidate)
            .where(
                ScrapingFacilityCandidate.organization_id == auth.org_id,
                ScrapingFacilityCandidate.execution_id == execution_id,
            )
            .order_by(ScrapingFacilityCandidate.created_at.desc())
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 500))
        )
        return list(result.scalars().all())

    async def list_evidence(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScrapingFacilityCandidateEvidence]:
        await self._assert_execution_access(db, auth, execution_id)
        result = await db.execute(
            select(ScrapingFacilityCandidateEvidence)
            .where(
                ScrapingFacilityCandidateEvidence.organization_id == auth.org_id,
                ScrapingFacilityCandidateEvidence.execution_id == execution_id,
            )
            .order_by(ScrapingFacilityCandidateEvidence.created_at.desc())
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 500))
        )
        return list(result.scalars().all())

    async def _persist_output(
        self,
        db: AsyncSession,
        attempt: ScrapingFacilityExtractionAttempt,
        *,
        chunk_id: str,
        chunk_text: str,
        output: FacilityExtractionOutput,
    ) -> tuple[int, int]:
        accepted = 0
        rejected = 0
        organization_id = attempt.organization_id
        execution_id = attempt.execution_id
        coverage_cell_id = attempt.coverage_cell_id
        source_document_id = attempt.source_document_id
        prepared_text_id = attempt.prepared_text_id
        attempt_id = attempt.id
        document_count = await db.scalar(
            select(func.count()).select_from(ScrapingFacilityCandidate).where(
                ScrapingFacilityCandidate.organization_id == organization_id,
                ScrapingFacilityCandidate.source_document_id == source_document_id,
            )
        ) or 0
        for facility in output.facilities[: get_settings().facility_extraction_max_candidates_per_chunk]:
            if document_count >= get_settings().facility_extraction_max_candidates_per_document:
                rejected += 1
                continue
            name_ev = _verify(facility.name, chunk_text)
            if name_ev is None:
                rejected += 1
                continue
            fingerprint = _candidate_fingerprint(source_document_id, facility.name.value)
            existing_candidate = await db.scalar(
                select(ScrapingFacilityCandidate.id).where(
                    ScrapingFacilityCandidate.organization_id == organization_id,
                    ScrapingFacilityCandidate.source_document_id == source_document_id,
                    ScrapingFacilityCandidate.chunk_id == chunk_id,
                    ScrapingFacilityCandidate.candidate_fingerprint == fingerprint,
                )
            )
            if existing_candidate is not None:
                continue
            candidate = ScrapingFacilityCandidate(
                organization_id=organization_id,
                execution_id=execution_id,
                coverage_cell_id=coverage_cell_id,
                source_document_id=source_document_id,
                prepared_text_id=prepared_text_id,
                chunk_id=chunk_id,
                extraction_attempt_id=attempt_id,
                raw_name=facility.name.value[:255],
                raw_payload=_bounded_payload(facility),
                model_confidence=facility.model_confidence,
                staging_status=FacilityCandidateStagingStatus.EXTRACTED,
                candidate_fingerprint=fingerprint,
            )
            db.add(candidate)
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                continue
            candidate_id = candidate.id
            await self._add_evidence(
                db,
                organization_id=organization_id,
                execution_id=execution_id,
                facility_candidate_id=candidate_id,
                source_document_id=source_document_id,
                prepared_text_id=prepared_text_id,
                chunk_id=chunk_id,
                field_name="name",
                raw_value=facility.name.value,
                verified=name_ev,
            )
            for field_name, item in _iter_optional_fields(facility):
                verified = _verify(item, chunk_text)
                if verified is None:
                    rejected += 1
                    continue
                await self._add_evidence(
                    db,
                    organization_id=organization_id,
                    execution_id=execution_id,
                    facility_candidate_id=candidate_id,
                    source_document_id=source_document_id,
                    prepared_text_id=prepared_text_id,
                    chunk_id=chunk_id,
                    field_name=field_name,
                    raw_value=item.value,
                    verified=verified,
                )
            accepted += 1
            document_count += 1
        return accepted, rejected

    async def _add_evidence(
        self,
        db: AsyncSession,
        *,
        organization_id: str,
        execution_id: str | None,
        facility_candidate_id: str,
        source_document_id: str,
        prepared_text_id: str | None,
        chunk_id: str,
        field_name: str,
        raw_value: Any,
        verified: tuple[str, int, int, str],
    ) -> None:
        quote, start, end, evidence_hash = verified
        row = ScrapingFacilityCandidateEvidence(
            organization_id=organization_id,
            execution_id=execution_id,
            facility_candidate_id=facility_candidate_id,
            source_document_id=source_document_id,
            prepared_text_id=prepared_text_id,
            chunk_id=chunk_id,
            field_name=field_name[:120],
            raw_value=raw_value,
            evidence_quote=quote,
            quote_start=start,
            quote_end=end,
            evidence_hash=evidence_hash,
            verification_status=FacilityCandidateEvidenceVerificationStatus.VERIFIED,
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()

    async def _load_document(self, db: AsyncSession, context: FacilityExtractionContext) -> ScrapingSourceDocument:
        result = await db.execute(
            select(ScrapingSourceDocument).where(
                ScrapingSourceDocument.id == context.source_document_id,
                ScrapingSourceDocument.organization_id == context.organization_id,
                ScrapingSourceDocument.execution_id == context.execution_id,
            )
        )
        document = result.scalar_one_or_none()
        if document is None:
            raise NotFoundError("ScrapingSourceDocument", context.source_document_id)
        return document

    async def _ensure_prepared(
        self, db: AsyncSession, context: FacilityExtractionContext, document: ScrapingSourceDocument
    ) -> ScrapingSourceDocumentText:
        if context.prepared_text_id:
            result = await db.execute(
                select(ScrapingSourceDocumentText).where(
                    ScrapingSourceDocumentText.id == context.prepared_text_id,
                    ScrapingSourceDocumentText.organization_id == context.organization_id,
                    ScrapingSourceDocumentText.source_document_id == document.id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise NotFoundError("ScrapingSourceDocumentText", context.prepared_text_id)
            return row
        await document_text_preparation_service.prepare(
            db,
            SourceDocumentPreparationContext(
                organization_id=context.organization_id,
                execution_id=context.execution_id,
                source_document_id=document.id,
                language_hint=context.language_hint,
            ),
        )
        result = await db.execute(
            select(ScrapingSourceDocumentText).where(
                ScrapingSourceDocumentText.organization_id == context.organization_id,
                ScrapingSourceDocumentText.source_document_id == document.id,
                ScrapingSourceDocumentText.source_content_hash == document.content_sha256,
            )
        )
        return result.scalar_one()

    async def _select_chunk(
        self, db: AsyncSession, context: FacilityExtractionContext, prepared: ScrapingSourceDocumentText
    ) -> ScrapingSourceDocumentChunk:
        await document_text_preparation_service.ensure_chunks(db, prepared)
        query = select(ScrapingSourceDocumentChunk).where(
            ScrapingSourceDocumentChunk.organization_id == context.organization_id,
            ScrapingSourceDocumentChunk.prepared_text_id == prepared.id,
        )
        if context.chunk_id:
            query = query.where(ScrapingSourceDocumentChunk.id == context.chunk_id)
        elif context.chunk_index is not None:
            query = query.where(ScrapingSourceDocumentChunk.chunk_index == context.chunk_index)
        else:
            query = query.where(ScrapingSourceDocumentChunk.chunk_index == 0)
        row = (await db.execute(query)).scalar_one_or_none()
        if row is None:
            raise NotFoundError(
                "ScrapingSourceDocumentChunk",
                context.chunk_id or str(context.chunk_index or 0),
            )
        return row

    async def _existing_attempt(
        self, db: AsyncSession, context: FacilityExtractionContext, attempt_key: str
    ) -> ScrapingFacilityExtractionAttempt | None:
        result = await db.execute(
            select(ScrapingFacilityExtractionAttempt).where(
                ScrapingFacilityExtractionAttempt.organization_id == context.organization_id,
                ScrapingFacilityExtractionAttempt.idempotency_key == attempt_key,
            )
        )
        return result.scalar_one_or_none()

    async def _summary_for_attempt(
        self, db: AsyncSession, attempt: ScrapingFacilityExtractionAttempt
    ) -> FacilityExtractionSummary:
        return FacilityExtractionSummary(
            attempt_id=attempt.id,
            source_document_id=attempt.source_document_id,
            chunk_id=attempt.chunk_id,
            provider=attempt.provider,
            model=attempt.model,
            status=attempt.status.value,
            extracted_candidate_count=attempt.output_candidate_count,
            accepted_evidence_count=await self._evidence_count(db, attempt.id),
            rejected_evidence_count=0,
            document_relevant=(attempt.metadata_json or {}).get("document_relevant"),
            failure_classification=attempt.failure_classification,
        )

    async def _evidence_count(self, db: AsyncSession, attempt_id: str) -> int:
        return await db.scalar(
            select(func.count()).select_from(ScrapingFacilityCandidateEvidence).join(
                ScrapingFacilityCandidate,
                ScrapingFacilityCandidate.id == ScrapingFacilityCandidateEvidence.facility_candidate_id,
            ).where(ScrapingFacilityCandidate.extraction_attempt_id == attempt_id)
        ) or 0

    async def _assert_execution_access(self, db: AsyncSession, auth: AuthContext, execution_id: str) -> None:
        exists = await db.scalar(
            select(ScrapingExecution.id).where(
                ScrapingExecution.id == execution_id,
                ScrapingExecution.organization_id == auth.org_id,
            )
        )
        if exists is None:
            raise NotFoundError("ScrapingExecution", execution_id)


def _verify(value: ExtractedEvidenceValue, chunk_text: str) -> tuple[str, int, int, str] | None:
    quote = value.evidence_quote.strip()
    if not quote or len(quote) > get_settings().facility_extraction_max_evidence_quote_characters:
        return None
    start = chunk_text.find(quote)
    if start < 0:
        normalized_chunk = chunk_text.replace("\r\n", "\n").replace("\r", "\n")
        normalized_quote = quote.replace("\r\n", "\n").replace("\r", "\n")
        start = normalized_chunk.find(normalized_quote)
        if start < 0:
            return None
        quote = normalized_quote
        chunk_text = normalized_chunk
    end = start + len(quote)
    if chunk_text[start:end] != quote:
        return None
    evidence_hash = hashlib.sha256(f"{value.value}\n{quote}\n{start}:{end}".encode("utf-8")).hexdigest()
    return quote, start, end, evidence_hash


def _iter_optional_fields(facility: ExtractedFacility):
    for field_name in ("facility_type", "operator"):
        item = getattr(facility, field_name)
        if item is not None:
            yield field_name, item
    for field_name in ("aliases", "addresses", "phones", "emails", "websites", "services", "license_or_registration"):
        for item in getattr(facility, field_name):
            yield field_name, item


def _candidate_fingerprint(source_document_id: str, name: str) -> str:
    normalized = " ".join(name.lower().split())
    return hashlib.sha256(f"{source_document_id}:{normalized}".encode("utf-8")).hexdigest()


def _attempt_idempotency_key(base_key: str, provider: FacilityExtractionProvider) -> str:
    version_material = f"{provider.provider_name}:{provider.model}:{provider.prompt_version}:{provider.schema_version}"
    digest = hashlib.sha256(version_material.encode("utf-8")).hexdigest()[:16]
    suffix = f":extract-v:{digest}"
    max_base = MAX_IDEMPOTENCY_KEY_LENGTH - len(suffix)
    return f"{base_key[:max_base]}{suffix}"


def _safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "parse_stage",
        "response_format_requested",
        "markdown_fence_present",
        "repair_attempted",
        "repair_failed",
        "validation_error_count",
        "validation_errors",
        "json_error",
        "facility_count",
    }
    safe: dict[str, Any] = {}
    for key, item in (value or {}).items():
        if key == "repair" and isinstance(item, dict):
            safe[key] = _safe_metadata(item)
        elif key in allowed:
            safe[key] = _bounded_metadata_value(item)
    return safe


def _bounded_metadata_value(value: Any) -> Any:
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, str):
        return value[:160]
    if isinstance(value, list):
        return [_bounded_metadata_value(item) for item in value[:8]]
    if isinstance(value, dict):
        return {str(key)[:80]: _bounded_metadata_value(item) for key, item in list(value.items())[:8]}
    return str(value)[:160]


def _bounded_payload(facility: ExtractedFacility) -> dict[str, Any]:
    return json.loads(facility.model_dump_json(exclude_none=True))  # Pydantic has already bounded fields.


facility_extraction_service = FacilityExtractionService()
