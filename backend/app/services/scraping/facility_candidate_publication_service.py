"""Publish one verified staged facility candidate into the final rehabilitation dataset."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import (
    FacilityCandidateEvidenceVerificationStatus,
    FacilityCandidatePublicationStatus,
    FacilityCandidateStagingStatus,
    FacilityExtractionAttemptStatus,
    RehabilitationFacility,
    RehabilitationFacilityAlias,
    RehabilitationFacilityContact,
    RehabilitationFacilityLocation,
    RehabilitationFacilitySourceLink,
    RehabilitationFieldEvidence,
    RehabilitationSource,
    RehabilitationUnresolvedField,
    ScrapingExecution,
    ScrapingFacilityCandidate,
    ScrapingFacilityCandidateEvidence,
    ScrapingFacilityCandidatePublication,
    ScrapingFacilityExtractionAttempt,
    ScrapingSourceCandidate,
    ScrapingSourceDiscoveryQuery,
    ScrapingSourceDocument,
    ScrapingSourceDocumentChunk,
    ScrapingSourceDocumentText,
    ScrapingSourceRetrievalAttempt,
)
from app.services.scraping.countries import resolve_country

NORMALIZATION_VERSION = "facility-publication-v1"
VERIFICATION_STATUS = "verified_from_staging"
CONFIDENCE_WITH_EXACT_EVIDENCE = Decimal("0.8000")
MAX_METADATA_EVIDENCE_ITEMS = 100


@dataclass(frozen=True)
class FacilityCandidatePublicationContext:
    organization_id: str
    execution_id: str
    facility_candidate_id: str


@dataclass(frozen=True)
class FacilityCandidatePublicationSummary:
    publication_id: str
    candidate_id: str
    status: str
    reason_code: str | None = None
    final_facility_id: str | None = None
    normalized_facility_name: str | None = None
    country_code: str | None = None
    aliases_created: int = 0
    locations_created: int = 0
    contacts_created: int = 0
    sources_linked: int = 0
    field_evidence_created: int = 0
    unresolved_fields_created: int = 0
    reused_existing_publication: bool = False


@dataclass(frozen=True)
class _LoadedCandidate:
    execution: ScrapingExecution
    candidate: ScrapingFacilityCandidate
    attempt: ScrapingFacilityExtractionAttempt
    source_document: ScrapingSourceDocument
    prepared_text: ScrapingSourceDocumentText
    chunk: ScrapingSourceDocumentChunk


@dataclass(frozen=True)
class _PublicationPlan:
    normalized_name: str
    raw_name: str
    country_code: str
    country_name: str
    country_source: str
    explicit_country_value: str | None
    facility_type: str
    primary_address: str | None
    primary_region: str | None
    primary_city: str | None
    primary_website: str | None
    aliases: list[ScrapingFacilityCandidateEvidence]
    locations: list[ScrapingFacilityCandidateEvidence]
    contacts: list[tuple[str, str, str | None, ScrapingFacilityCandidateEvidence]]
    unresolved: list[tuple[str, str, str]]
    evidence: list[ScrapingFacilityCandidateEvidence]


class FacilityCandidatePublicationService:
    async def publish_one_candidate(
        self,
        db: AsyncSession,
        context: FacilityCandidatePublicationContext,
    ) -> FacilityCandidatePublicationSummary:
        existing = await self._existing_publication(db, context)
        if (
            existing is not None
            and existing.status
            in {
                FacilityCandidatePublicationStatus.PUBLISHED,
                FacilityCandidatePublicationStatus.SKIPPED,
            }
        ):
            return await self._summary_for_publication(db, existing, reused=True)

        try:
            loaded = await self._load_candidate(db, context)
        except ValidationError:
            return await self._create_or_mark_skipped(db, context, "ownership_mismatch")
        if existing is None:
            existing = ScrapingFacilityCandidatePublication(
                organization_id=context.organization_id,
                execution_id=context.execution_id,
                facility_candidate_id=context.facility_candidate_id,
                normalization_version=NORMALIZATION_VERSION,
                status=FacilityCandidatePublicationStatus.PENDING,
                started_at=datetime.now(UTC),
                metadata_json={},
            )
            db.add(existing)
            try:
                await db.flush()
            except IntegrityError:
                await db.rollback()
                existing = await self._existing_publication(db, context)
                if existing is not None:
                    return await self._summary_for_publication(db, existing, reused=True)
                raise
        else:
            existing.status = FacilityCandidatePublicationStatus.PENDING
            existing.reason_code = None
            existing.started_at = datetime.now(UTC)
            existing.completed_at = None

        try:
            plan = await self._build_plan(db, loaded)
            if isinstance(plan, str):
                return await self._mark_skipped(db, existing, plan)

            source = await self._get_or_create_source(db, loaded)
            facility = await self._create_facility(db, loaded, plan)
            existing.final_facility_id = facility.id
            await self._after_facility_created(db, facility)
            aliases = await self._add_aliases(db, facility, plan)
            locations = await self._add_locations(db, facility, plan)
            contacts = await self._add_contacts(db, facility, plan)
            source_links = await self._link_source(db, facility, source)
            evidence, evidence_metadata = await self._add_field_evidence(
                db, facility, source, loaded, plan
            )
            unresolved = await self._add_unresolved_fields(db, facility, source, plan)

            existing.status = FacilityCandidatePublicationStatus.PUBLISHED
            existing.reason_code = None
            existing.completed_at = datetime.now(UTC)
            existing.published_at = existing.completed_at
            existing.metadata_json = _safe_metadata(
                {
                    "normalization_version": NORMALIZATION_VERSION,
                    "country_source": plan.country_source,
                    "explicit_country_value": plan.explicit_country_value,
                    "source_document_id": loaded.source_document.id,
                    "prepared_text_id": loaded.prepared_text.id,
                    "chunk_id": loaded.chunk.id,
                    "source_id": source.id,
                    "raw_model_confidence": (
                        float(loaded.candidate.model_confidence)
                        if loaded.candidate.model_confidence is not None
                        else None
                    ),
                    "evidence_mappings": evidence_metadata[:MAX_METADATA_EVIDENCE_ITEMS],
                    "counts": {
                        "aliases_created": aliases,
                        "locations_created": locations,
                        "contacts_created": contacts,
                        "sources_linked": source_links,
                        "field_evidence_created": evidence,
                        "unresolved_fields_created": unresolved,
                    },
                }
            )
            await db.commit()
            await db.refresh(existing)
            return await self._summary_for_publication(db, existing, reused=False)
        except Exception as exc:
            await db.rollback()
            failed = await self._record_failed_publication(db, context, _safe_reason(exc))
            return await self._summary_for_publication(db, failed, reused=False)

    async def list_publications(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScrapingFacilityCandidatePublication]:
        await self._assert_execution_access(db, auth, execution_id)
        result = await db.execute(
            select(ScrapingFacilityCandidatePublication)
            .where(
                ScrapingFacilityCandidatePublication.organization_id == auth.org_id,
                ScrapingFacilityCandidatePublication.execution_id == execution_id,
            )
            .order_by(ScrapingFacilityCandidatePublication.created_at.desc())
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 500))
        )
        return list(result.scalars().all())

    async def _load_candidate(
        self, db: AsyncSession, context: FacilityCandidatePublicationContext
    ) -> _LoadedCandidate:
        candidate = await db.scalar(
            select(ScrapingFacilityCandidate).where(
                ScrapingFacilityCandidate.id == context.facility_candidate_id,
                ScrapingFacilityCandidate.organization_id == context.organization_id,
                ScrapingFacilityCandidate.execution_id == context.execution_id,
            )
        )
        if candidate is None:
            raise NotFoundError("ScrapingFacilityCandidate", context.facility_candidate_id)

        execution = await db.scalar(
            select(ScrapingExecution).where(
                ScrapingExecution.id == context.execution_id,
                ScrapingExecution.organization_id == context.organization_id,
            )
        )
        attempt = await db.scalar(
            select(ScrapingFacilityExtractionAttempt).where(
                ScrapingFacilityExtractionAttempt.id == candidate.extraction_attempt_id,
                ScrapingFacilityExtractionAttempt.organization_id == context.organization_id,
                ScrapingFacilityExtractionAttempt.execution_id == context.execution_id,
            )
        )
        document = await db.scalar(
            select(ScrapingSourceDocument).where(
                ScrapingSourceDocument.id == candidate.source_document_id,
                ScrapingSourceDocument.organization_id == context.organization_id,
                ScrapingSourceDocument.execution_id == context.execution_id,
            )
        )
        prepared = await db.scalar(
            select(ScrapingSourceDocumentText).where(
                ScrapingSourceDocumentText.id == candidate.prepared_text_id,
                ScrapingSourceDocumentText.organization_id == context.organization_id,
                ScrapingSourceDocumentText.execution_id == context.execution_id,
            )
        )
        chunk = await db.scalar(
            select(ScrapingSourceDocumentChunk).where(
                ScrapingSourceDocumentChunk.id == candidate.chunk_id,
                ScrapingSourceDocumentChunk.organization_id == context.organization_id,
                ScrapingSourceDocumentChunk.execution_id == context.execution_id,
            )
        )
        if execution is None:
            raise NotFoundError("ScrapingExecution", context.execution_id)
        if attempt is None or document is None or prepared is None or chunk is None:
            raise ValidationError("Candidate publication ownership mismatch")
        return _LoadedCandidate(execution, candidate, attempt, document, prepared, chunk)

    async def _build_plan(
        self, db: AsyncSession, loaded: _LoadedCandidate
    ) -> _PublicationPlan | str:
        candidate = loaded.candidate
        if candidate.staging_status != FacilityCandidateStagingStatus.EXTRACTED:
            return "unsupported_candidate_shape"
        if loaded.attempt.status != FacilityExtractionAttemptStatus.SUCCEEDED:
            return "extraction_not_succeeded"

        evidence = await self._verified_evidence(db, candidate.id)
        name_evidence = [row for row in evidence if row.field_name == "name"]
        if not name_evidence:
            return "missing_verified_name"
        normalized_name = _normalize_name(str(name_evidence[0].raw_value or candidate.raw_name))
        if not normalized_name:
            return "invalid_normalized_name"

        country = _resolve_publication_country(loaded.execution, evidence)
        if isinstance(country, str):
            return country
        country_code, country_name, country_source, explicit_country_value = country

        aliases = [row for row in evidence if row.field_name == "aliases"]
        if _meaningfully_different(candidate.raw_name, normalized_name):
            aliases = [*aliases, name_evidence[0]]

        addresses = [row for row in evidence if row.field_name == "addresses"]
        contacts, unresolved = _build_contacts(evidence)
        primary_address = _normalize_text_value(addresses[0].raw_value) if addresses else None
        primary_website = next(
            (value for contact_type, value, _, _ in contacts if contact_type == "website"),
            None,
        )
        facility_type = _normalize_text_value(
            next((row.raw_value for row in evidence if row.field_name == "facility_type"), None)
        )
        return _PublicationPlan(
            normalized_name=normalized_name,
            raw_name=candidate.raw_name,
            country_code=country_code,
            country_name=country_name,
            country_source=country_source,
            explicit_country_value=explicit_country_value,
            facility_type=(facility_type or "rehabilitation_or_addiction_treatment")[:80],
            primary_address=primary_address,
            primary_region=None,
            primary_city=None,
            primary_website=primary_website,
            aliases=aliases,
            locations=addresses,
            contacts=contacts,
            unresolved=unresolved,
            evidence=evidence,
        )

    async def _get_or_create_source(
        self, db: AsyncSession, loaded: _LoadedCandidate
    ) -> RehabilitationSource:
        canonical_url = _bounded_url(loaded.source_document.final_url)
        existing = await db.scalar(
            select(RehabilitationSource).where(
                RehabilitationSource.execution_id == loaded.execution.id,
                RehabilitationSource.canonical_url == canonical_url,
            )
        )
        if existing is not None:
            return existing

        source_candidate = await db.scalar(
            select(ScrapingSourceCandidate).where(
                ScrapingSourceCandidate.id == loaded.source_document.source_candidate_id
            )
        )
        discovery_query = None
        if source_candidate is not None:
            discovery_query = await db.scalar(
                select(ScrapingSourceDiscoveryQuery).where(
                    ScrapingSourceDiscoveryQuery.id == source_candidate.discovery_query_id
                )
            )
        retrieval_attempt = await db.scalar(
            select(ScrapingSourceRetrievalAttempt).where(
                ScrapingSourceRetrievalAttempt.id == loaded.source_document.retrieval_attempt_id
            )
        )
        source = RehabilitationSource(
            execution_id=loaded.execution.id,
            coverage_cell_id=loaded.candidate.coverage_cell_id,
            task_id=retrieval_attempt.task_id if retrieval_attempt is not None else None,
            original_url=canonical_url,
            canonical_url=canonical_url,
            domain=(urlsplit(canonical_url).hostname or "")[:255],
            source_category=(
                source_candidate.source_category
                if source_candidate is not None
                else "retrieved_source"
            )[:120],
            discovery_query=discovery_query.query_text if discovery_query is not None else None,
            page_title=(
                loaded.prepared_text.title
                or (source_candidate.title if source_candidate else None)
            ),
            language_code=loaded.prepared_text.detected_language
            or (source_candidate.language_code if source_candidate is not None else None),
            region=source_candidate.region_name if source_candidate is not None else None,
            fetch_status="fetched",
            http_status=retrieval_attempt.http_status if retrieval_attempt is not None else None,
            content_type=(loaded.source_document.content_type or "")[:120],
            content_hash=loaded.source_document.content_sha256,
            retrieved_at=loaded.source_document.retrieval_timestamp,
            is_mock=False,
        )
        db.add(source)
        await db.flush()
        return source

    async def _create_facility(
        self, db: AsyncSession, loaded: _LoadedCandidate, plan: _PublicationPlan
    ) -> RehabilitationFacility:
        facility = RehabilitationFacility(
            execution_id=loaded.execution.id,
            organization_id=loaded.execution.organization_id,
            stable_key=_stable_key(loaded.candidate.id, plan.normalized_name, plan.country_code),
            canonical_name=plan.normalized_name[:255],
            original_language_name=plan.raw_name[:255],
            description=None,
            facility_type=plan.facility_type,
            organization_type="not_classified",
            operational_status="not_verified",
            country_code=plan.country_code,
            country_name=plan.country_name,
            primary_region=plan.primary_region,
            primary_city=plan.primary_city,
            primary_address=plan.primary_address,
            latitude=None,
            longitude=None,
            primary_website=plan.primary_website,
            verification_status=VERIFICATION_STATUS,
            confidence_score=CONFIDENCE_WITH_EXACT_EVIDENCE,
            duplicate_status="not_evaluated",
            human_review_status="required",
            is_mock=False,
            last_verified_at=datetime.now(UTC),
        )
        db.add(facility)
        await db.flush()
        return facility

    async def _after_facility_created(
        self, db: AsyncSession, facility: RehabilitationFacility
    ) -> None:
        return None

    async def _add_aliases(
        self, db: AsyncSession, facility: RehabilitationFacility, plan: _PublicationPlan
    ) -> int:
        count = 0
        seen: set[tuple[str, str]] = set()
        for row in plan.aliases:
            alias = _normalize_name(str(row.raw_value or ""))
            if not alias or alias == facility.canonical_name:
                continue
            key = (alias.casefold(), "extracted_alias")
            if key in seen:
                continue
            seen.add(key)
            db.add(
                RehabilitationFacilityAlias(
                    facility_id=facility.id,
                    name=alias[:255],
                    language_code=None,
                    alias_type="extracted_alias",
                    is_primary=False,
                    is_mock=False,
                )
            )
            count += 1
        await db.flush()
        return count

    async def _add_locations(
        self, db: AsyncSession, facility: RehabilitationFacility, plan: _PublicationPlan
    ) -> int:
        count = 0
        seen: set[str] = set()
        for row in plan.locations:
            address = _normalize_text_value(row.raw_value)
            if not address or address.casefold() in seen:
                continue
            seen.add(address.casefold())
            db.add(
                RehabilitationFacilityLocation(
                    facility_id=facility.id,
                    location_type="extracted_address",
                    location_name=address[:255],
                    country_code=facility.country_code,
                    country_name=facility.country_name,
                    full_address=address,
                    is_primary=count == 0,
                    verification_status=VERIFICATION_STATUS,
                    confidence_score=CONFIDENCE_WITH_EXACT_EVIDENCE,
                    is_mock=False,
                )
            )
            count += 1
        await db.flush()
        return count

    async def _add_contacts(
        self, db: AsyncSession, facility: RehabilitationFacility, plan: _PublicationPlan
    ) -> int:
        count = 0
        seen: set[tuple[str, str]] = set()
        primary_contact_types: set[str] = set()
        for contact_type, value, normalized, _row in plan.contacts:
            key = (contact_type, value.casefold())
            if key in seen:
                continue
            seen.add(key)
            is_primary = contact_type not in primary_contact_types
            primary_contact_types.add(contact_type)
            db.add(
                RehabilitationFacilityContact(
                    facility_id=facility.id,
                    contact_type=contact_type,
                    label=None,
                    value=value[:512],
                    normalized_value=(normalized or value)[:512],
                    is_primary=is_primary,
                    available_24_7=False,
                    verification_status=VERIFICATION_STATUS,
                    confidence_score=CONFIDENCE_WITH_EXACT_EVIDENCE,
                    is_mock=False,
                )
            )
            count += 1
        await db.flush()
        return count

    async def _link_source(
        self, db: AsyncSession, facility: RehabilitationFacility, source: RehabilitationSource
    ) -> int:
        existing = await db.scalar(
            select(RehabilitationFacilitySourceLink.id).where(
                RehabilitationFacilitySourceLink.facility_id == facility.id,
                RehabilitationFacilitySourceLink.source_id == source.id,
                RehabilitationFacilitySourceLink.relationship_type == "extraction_source",
            )
        )
        if existing is not None:
            return 0
        db.add(
            RehabilitationFacilitySourceLink(
                facility_id=facility.id,
                source_id=source.id,
                relationship_type="extraction_source",
                is_primary=True,
            )
        )
        await db.flush()
        return 1

    async def _add_field_evidence(
        self,
        db: AsyncSession,
        facility: RehabilitationFacility,
        source: RehabilitationSource,
        loaded: _LoadedCandidate,
        plan: _PublicationPlan,
    ) -> tuple[int, list[dict[str, Any]]]:
        count = 0
        metadata: list[dict[str, Any]] = []
        field_counts: dict[str, int] = {}
        published_contact_evidence_ids = {
            row.id for _type, _value, _normalized, row in plan.contacts
        }
        for row in plan.evidence:
            if row.field_name in {"services", "license_or_registration", "operator"}:
                continue
            if (
                row.field_name in {"phones", "emails", "websites"}
                and row.id not in published_contact_evidence_ids
            ):
                continue
            field_path = _field_path(row, field_counts)
            db.add(
                RehabilitationFieldEvidence(
                    facility_id=facility.id,
                    source_id=source.id,
                    field_path=field_path,
                    extracted_value=_normalize_text_value(row.raw_value)[:512],
                    evidence_text=row.evidence_quote[:1000],
                    page_title=source.page_title,
                    source_url_snapshot=source.canonical_url,
                    language_code=source.language_code,
                    extraction_method="openrouter_facility_extractor_v2",
                    verification_status=VERIFICATION_STATUS,
                    confidence_score=CONFIDENCE_WITH_EXACT_EVIDENCE,
                    is_mock=False,
                )
            )
            metadata.append(
                {
                    "field_path": field_path,
                    "staging_evidence_id": row.id,
                    "field_name": row.field_name,
                    "source_document_id": row.source_document_id,
                    "prepared_text_id": row.prepared_text_id,
                    "chunk_id": row.chunk_id,
                    "quote_start": row.quote_start,
                    "quote_end": row.quote_end,
                    "evidence_hash_prefix": row.evidence_hash[:12],
                    "verification_status": row.verification_status.value,
                }
            )
            count += 1
        await db.flush()
        return count, metadata

    async def _add_unresolved_fields(
        self,
        db: AsyncSession,
        facility: RehabilitationFacility,
        source: RehabilitationSource,
        plan: _PublicationPlan,
    ) -> int:
        count = 0
        seen: set[tuple[str, str]] = set()
        for field_path, status, reason in plan.unresolved:
            key = (field_path, status)
            if key in seen:
                continue
            seen.add(key)
            db.add(
                RehabilitationUnresolvedField(
                    facility_id=facility.id,
                    field_path=field_path[:255],
                    unresolved_status=status[:80],
                    reason=reason[:500],
                    recommended_follow_up=None,
                    source_id=source.id,
                    is_mock=False,
                )
            )
            count += 1
        await db.flush()
        return count

    async def _mark_skipped(
        self, db: AsyncSession, publication: ScrapingFacilityCandidatePublication, reason: str
    ) -> FacilityCandidatePublicationSummary:
        publication.status = FacilityCandidatePublicationStatus.SKIPPED
        publication.reason_code = reason[:80]
        publication.completed_at = datetime.now(UTC)
        publication.metadata_json = _safe_metadata({"reason_code": reason})
        await db.commit()
        await db.refresh(publication)
        return await self._summary_for_publication(db, publication, reused=False)

    async def _create_or_mark_skipped(
        self,
        db: AsyncSession,
        context: FacilityCandidatePublicationContext,
        reason: str,
    ) -> FacilityCandidatePublicationSummary:
        publication = await self._existing_publication(db, context)
        if publication is None:
            publication = ScrapingFacilityCandidatePublication(
                organization_id=context.organization_id,
                execution_id=context.execution_id,
                facility_candidate_id=context.facility_candidate_id,
                normalization_version=NORMALIZATION_VERSION,
                status=FacilityCandidatePublicationStatus.PENDING,
                started_at=datetime.now(UTC),
                metadata_json={},
            )
            db.add(publication)
            await db.flush()
        return await self._mark_skipped(db, publication, reason)

    async def _record_failed_publication(
        self,
        db: AsyncSession,
        context: FacilityCandidatePublicationContext,
        reason: str,
    ) -> ScrapingFacilityCandidatePublication:
        existing = await self._existing_publication(db, context)
        if existing is None:
            existing = ScrapingFacilityCandidatePublication(
                organization_id=context.organization_id,
                execution_id=context.execution_id,
                facility_candidate_id=context.facility_candidate_id,
                normalization_version=NORMALIZATION_VERSION,
                status=FacilityCandidatePublicationStatus.FAILED,
            )
            db.add(existing)
        existing.status = FacilityCandidatePublicationStatus.FAILED
        existing.reason_code = reason[:80]
        existing.final_facility_id = None
        existing.completed_at = datetime.now(UTC)
        existing.metadata_json = _safe_metadata({"reason_code": reason})
        await db.commit()
        await db.refresh(existing)
        return existing

    async def _existing_publication(
        self, db: AsyncSession, context: FacilityCandidatePublicationContext
    ) -> ScrapingFacilityCandidatePublication | None:
        return await db.scalar(
            select(ScrapingFacilityCandidatePublication).where(
                ScrapingFacilityCandidatePublication.organization_id == context.organization_id,
                ScrapingFacilityCandidatePublication.execution_id == context.execution_id,
                ScrapingFacilityCandidatePublication.facility_candidate_id
                == context.facility_candidate_id,
            )
        )

    async def _verified_evidence(
        self, db: AsyncSession, candidate_id: str
    ) -> list[ScrapingFacilityCandidateEvidence]:
        rows = (
            await db.execute(
                select(ScrapingFacilityCandidateEvidence)
                .where(
                    ScrapingFacilityCandidateEvidence.facility_candidate_id == candidate_id,
                    ScrapingFacilityCandidateEvidence.verification_status
                    == FacilityCandidateEvidenceVerificationStatus.VERIFIED,
                )
                .order_by(
                    ScrapingFacilityCandidateEvidence.created_at,
                    ScrapingFacilityCandidateEvidence.id,
                )
            )
        ).scalars().all()
        return list(rows)

    async def _summary_for_publication(
        self,
        db: AsyncSession,
        publication: ScrapingFacilityCandidatePublication,
        *,
        reused: bool,
    ) -> FacilityCandidatePublicationSummary:
        facility = None
        if publication.final_facility_id:
            facility = await db.scalar(
                select(RehabilitationFacility).where(
                    RehabilitationFacility.id == publication.final_facility_id
                )
            )
        counts = (publication.metadata_json or {}).get("counts", {})
        return FacilityCandidatePublicationSummary(
            publication_id=publication.id,
            candidate_id=publication.facility_candidate_id,
            status=publication.status.value,
            reason_code=publication.reason_code,
            final_facility_id=publication.final_facility_id,
            normalized_facility_name=facility.canonical_name if facility is not None else None,
            country_code=facility.country_code if facility is not None else None,
            aliases_created=int(counts.get("aliases_created", 0) or 0),
            locations_created=int(counts.get("locations_created", 0) or 0),
            contacts_created=int(counts.get("contacts_created", 0) or 0),
            sources_linked=int(counts.get("sources_linked", 0) or 0),
            field_evidence_created=int(counts.get("field_evidence_created", 0) or 0),
            unresolved_fields_created=int(counts.get("unresolved_fields_created", 0) or 0),
            reused_existing_publication=reused,
        )

    async def _assert_execution_access(
        self, db: AsyncSession, auth: AuthContext, execution_id: str
    ) -> None:
        exists = await db.scalar(
            select(ScrapingExecution.id).where(
                ScrapingExecution.id == execution_id,
                ScrapingExecution.organization_id == auth.org_id,
            )
        )
        if exists is None:
            raise NotFoundError("ScrapingExecution", execution_id)


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value or "")
    return " ".join(normalized.split()).strip()


def _normalize_text_value(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _meaningfully_different(raw: str, normalized: str) -> bool:
    return bool(raw and normalized and _normalize_name(raw) != normalized)


def _resolve_publication_country(
    execution: ScrapingExecution,
    evidence: list[ScrapingFacilityCandidateEvidence],
) -> tuple[str, str, str, str | None] | str:
    explicit = next((row for row in evidence if row.field_name in {"country", "countries"}), None)
    execution_country = resolve_country(execution.country_code)
    if explicit is None:
        return execution_country.code, execution_country.name, "execution_scope", None
    raw_value = _normalize_text_value(explicit.raw_value)
    try:
        country = resolve_country(raw_value[:2] if len(raw_value) == 2 else raw_value)
    except ValidationError:
        match = next(
            (
                country
                for country in (resolve_country(code) for code in [execution.country_code])
                if country.name.casefold() == raw_value.casefold()
            ),
            None,
        )
        if match is None:
            return "country_scope_conflict"
        country = match
    if country.code != execution_country.code:
        return "country_scope_conflict"
    return country.code, country.name, "extracted_evidence", raw_value


ContactPlan = tuple[str, str, str | None, ScrapingFacilityCandidateEvidence]
UnresolvedPlan = tuple[str, str, str]


def _build_contacts(
    evidence: list[ScrapingFacilityCandidateEvidence],
) -> tuple[list[ContactPlan], list[UnresolvedPlan]]:
    contacts: list[tuple[str, str, str | None, ScrapingFacilityCandidateEvidence]] = []
    unresolved: list[tuple[str, str, str]] = []
    for row in evidence:
        value = _normalize_text_value(row.raw_value)
        if row.field_name == "emails":
            normalized = _normalize_email(value)
            if normalized is None:
                unresolved.append(("contacts.email", "invalid_extracted_value", "invalid_email"))
                continue
            contacts.append(("email", normalized, normalized, row))
        elif row.field_name == "phones":
            normalized = _normalize_phone(value)
            if normalized is None:
                unresolved.append(("contacts.phone", "invalid_extracted_value", "invalid_phone"))
                continue
            contacts.append(("phone", value, normalized, row))
        elif row.field_name == "websites":
            normalized = _normalize_website(value)
            if normalized is None:
                unresolved.append(("contacts.website", "invalid_extracted_value", "invalid_url"))
                continue
            contacts.append(("website", normalized, normalized, row))
    return contacts, unresolved


def _normalize_email(value: str) -> str | None:
    candidate = value.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", candidate):
        return None
    return candidate[:320]


def _normalize_phone(value: str) -> str | None:
    stripped = value.strip()
    if re.search(r"[A-Za-z]", stripped):
        return None
    digits = re.sub(r"\D", "", stripped)
    if len(digits) < 6:
        return None
    return ("+" if stripped.startswith("+") else "") + digits


def _normalize_website(value: str) -> str | None:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    netloc = parsed.hostname.lower()
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))[:512]


def _bounded_url(value: str) -> str:
    return _normalize_website(value) or value.strip()[:512]


def _stable_key(candidate_id: str, normalized_name: str, country_code: str) -> str:
    digest = hashlib.sha256(
        f"{candidate_id}:{normalized_name.casefold()}:{country_code}".encode("utf-8")
    ).hexdigest()[:20]
    return f"real-{country_code.lower()}-{digest}"


def _field_path(row: ScrapingFacilityCandidateEvidence, counts: dict[str, int]) -> str:
    base = {
        "name": "canonical_name",
        "aliases": "aliases.extracted",
        "facility_type": "facility_type",
        "addresses": "locations.address",
        "phones": "contacts.phone",
        "emails": "contacts.email",
        "websites": "contacts.website",
    }.get(row.field_name, row.field_name)
    index = counts.get(base, 0)
    counts[base] = index + 1
    if index == 0 and base in {"canonical_name", "facility_type"}:
        return base
    return f"{base}.{row.evidence_hash[:8]}"


def _safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return value


def _safe_reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "validation_error"
    if isinstance(exc, IntegrityError):
        return "integrity_error"
    return "publication_failed"


facility_candidate_publication_service = FacilityCandidatePublicationService()
