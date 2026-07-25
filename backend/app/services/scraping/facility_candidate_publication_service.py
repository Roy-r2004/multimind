"""Publish one verified staged facility candidate into the final rehabilitation dataset."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import (
    FacilityCandidateEvidenceVerificationStatus,
    FacilityCandidatePublicationStatus,
    FacilityCandidateStagingStatus,
    FacilityExtractionAttemptStatus,
    RehabilitationFacility,
    RehabilitationFacilityAlias,
    RehabilitationFacilityAttribute,
    RehabilitationFacilityContact,
    RehabilitationFacilityLocation,
    RehabilitationFacilitySourceLink,
    RehabilitationFieldEvidence,
    RehabilitationPossibleDuplicate,
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
from app.services.scraping.country_containment_service import (
    CountryContainmentResult,
    assess_country_containment,
)
from app.services.scraping.branch_identity_service import (
    BranchIdentityInput,
    branch_identity_service,
)
from app.services.scraping.hard_gate_verification_service import (
    HardGateEvidence,
    HardGateLocation,
    HardGateVerificationResult,
    HardGateVerificationInput,
    hard_gate_verification_service,
)

NORMALIZATION_VERSION = "facility-publication-v2"
VERIFICATION_STATUS = "verified_from_staging"
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
    extracted_country_code: str | None
    facility_type: str
    primary_address: str | None
    primary_region: str | None
    primary_city: str | None
    primary_website: str | None
    aliases: list[ScrapingFacilityCandidateEvidence]
    locations: list[ScrapingFacilityCandidateEvidence]
    contacts: list[tuple[str, str, str | None, ScrapingFacilityCandidateEvidence]]
    services: list[ScrapingFacilityCandidateEvidence]
    programs: list[ScrapingFacilityCandidateEvidence]
    populations_served: list[ScrapingFacilityCandidateEvidence]
    admissions_eligibility: list[ScrapingFacilityCandidateEvidence]
    unresolved: list[tuple[str, str, str]]
    evidence: list[ScrapingFacilityCandidateEvidence]
    containment: CountryContainmentResult
    hard_gate_results: HardGateVerificationResult


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

            confidence = _score_confidence(loaded.candidate, plan)
            min_confidence = Decimal(str(get_settings().facility_publication_min_confidence))
            if confidence < min_confidence:
                return await self._mark_skipped(db, existing, "below_min_confidence")

            source = await self._get_or_create_source(db, loaded)
            merge_target = await self._find_merge_target(
                db,
                execution_id=loaded.execution.id,
                plan=plan,
                country_code=plan.country_code,
            )
            merged_into_existing = merge_target is not None
            if merge_target is not None:
                facility = merge_target
                if confidence > Decimal(str(facility.confidence_score)):
                    facility.confidence_score = confidence
                if not facility.primary_website and plan.primary_website:
                    facility.primary_website = plan.primary_website
                facility.duplicate_status = "merged"
                facility.human_review_status = "required"
                facility.country_containment_status = plan.containment.status
                facility.country_containment_reason = plan.containment.reason
                facility.country_containment_signals_json = plan.containment.signals
                facility.publication_class = plan.hard_gate_results.publication_class
                facility.hard_gate_results_json = _safe_metadata(plan.hard_gate_results.as_json())
                facility.last_verified_at = datetime.now(UTC)
            else:
                facility = await self._create_facility(db, loaded, plan, confidence=confidence)
            existing.final_facility_id = facility.id
            await self._after_facility_created(db, facility)
            aliases = await self._add_aliases(db, facility, plan)
            locations = await self._add_locations(db, facility, plan, confidence=confidence)
            await db.refresh(facility, attribute_names=["locations"])
            contacts = await self._add_contacts(db, facility, plan, confidence=confidence)
            if not facility.primary_website and plan.primary_website:
                facility.primary_website = plan.primary_website
            services = await self._add_text_attributes(
                db,
                facility,
                plan.services,
                attribute_group="treatment_service",
                confidence=confidence,
            )
            programs = await self._add_text_attributes(
                db,
                facility,
                plan.programs,
                attribute_group="program",
                confidence=confidence,
            )
            populations = await self._add_text_attributes(
                db,
                facility,
                plan.populations_served,
                attribute_group="population_served",
                confidence=confidence,
            )
            admissions = await self._add_text_attributes(
                db,
                facility,
                plan.admissions_eligibility,
                attribute_group="admission_eligibility",
                confidence=confidence,
            )
            source_links = await self._link_source(db, facility, source)
            evidence, evidence_metadata = await self._add_field_evidence(
                db, facility, source, loaded, plan, confidence=confidence
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
                    "publication_confidence": float(confidence),
                    "hard_gate_results": plan.hard_gate_results.as_json(),
                    "merged_into_existing": merged_into_existing,
                    "evidence_mappings": evidence_metadata[:MAX_METADATA_EVIDENCE_ITEMS],
                    "counts": {
                        "aliases_created": aliases,
                        "locations_created": locations,
                        "contacts_created": contacts,
                        "treatment_services_created": services,
                        "programs_created": programs,
                        "populations_served_created": populations,
                        "admissions_eligibility_created": admissions,
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

    async def publish_execution_candidates(
        self,
        db: AsyncSession,
        *,
        organization_id: str,
        execution_id: str,
        max_candidates: int | None = None,
    ) -> dict[str, int]:
        """Publish staged EXTRACTED candidates for an execution (worker auto-publish)."""
        settings = get_settings()
        limit = max(
            1,
            max_candidates
            if max_candidates is not None
            else settings.facility_publication_max_candidates_per_execution,
        )
        published_ids = (
            await db.execute(
                select(ScrapingFacilityCandidatePublication.facility_candidate_id).where(
                    ScrapingFacilityCandidatePublication.organization_id == organization_id,
                    ScrapingFacilityCandidatePublication.execution_id == execution_id,
                    ScrapingFacilityCandidatePublication.status.in_(
                        [
                            FacilityCandidatePublicationStatus.PUBLISHED,
                            FacilityCandidatePublicationStatus.SKIPPED,
                        ]
                    ),
                )
            )
        ).scalars().all()
        published_set = set(published_ids)
        # Materialize IDs before publishing: publish_one_candidate commits and
        # expires ORM instances; lazy-loading candidate.id later raises MissingGreenlet.
        candidate_ids = list(
            (
                await db.execute(
                    select(ScrapingFacilityCandidate.id)
                    .where(
                        ScrapingFacilityCandidate.organization_id == organization_id,
                        ScrapingFacilityCandidate.execution_id == execution_id,
                        ScrapingFacilityCandidate.staging_status
                        == FacilityCandidateStagingStatus.EXTRACTED,
                    )
                    .order_by(ScrapingFacilityCandidate.created_at.asc())
                )
            ).scalars().all()
        )
        summary = {
            "candidates_considered": 0,
            "published": 0,
            "skipped": 0,
            "failed": 0,
            "reused": 0,
        }
        for candidate_id in candidate_ids:
            if candidate_id in published_set:
                continue
            if summary["candidates_considered"] >= limit:
                break
            summary["candidates_considered"] += 1
            result = await self.publish_one_candidate(
                db,
                FacilityCandidatePublicationContext(
                    organization_id=organization_id,
                    execution_id=execution_id,
                    facility_candidate_id=candidate_id,
                ),
            )
            if result.reused_existing_publication:
                summary["reused"] += 1
            elif result.status == FacilityCandidatePublicationStatus.PUBLISHED.value:
                summary["published"] += 1
            elif result.status == FacilityCandidatePublicationStatus.SKIPPED.value:
                summary["skipped"] += 1
            else:
                summary["failed"] += 1
        return summary

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
            select(ScrapingExecution)
            .where(
                ScrapingExecution.id == context.execution_id,
                ScrapingExecution.organization_id == context.organization_id,
            )
            .options(selectinload(ScrapingExecution.blueprint))
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

        country_code, country_name, country_source, explicit_country_value, extracted_country_code = (
            _resolve_publication_country(loaded.execution, evidence)
        )

        aliases = [row for row in evidence if row.field_name == "aliases"]
        if _meaningfully_different(candidate.raw_name, normalized_name):
            aliases = [*aliases, name_evidence[0]]

        addresses = [row for row in evidence if row.field_name == "addresses"]
        contacts, unresolved = _build_contacts(evidence)
        addresses, contacts = _merge_deterministic_contact_extracts(
            loaded.source_document,
            addresses=addresses,
            contacts=contacts,
            anchor_evidence=name_evidence[0],
        )
        services = [row for row in evidence if row.field_name == "services"]
        programs = [row for row in evidence if row.field_name == "programs"]
        populations_served = [
            row for row in evidence if row.field_name == "populations_served"
        ]
        admissions_eligibility = [
            row for row in evidence if row.field_name == "admissions_eligibility"
        ]
        primary_address = _normalize_text_value(addresses[0].raw_value) if addresses else None
        primary_website = next(
            (value for contact_type, value, _, _ in contacts if contact_type == "website"),
            None,
        )
        phone_values = [value for contact_type, value, _, _ in contacts if contact_type == "phone"]
        website_host = None
        if primary_website:
            try:
                website_host = urlsplit(primary_website).hostname
            except Exception:
                website_host = None
        containment = assess_country_containment(
            target_country_code=loaded.execution.country_code,
            extracted_country_code=extracted_country_code,
            extracted_country_raw=explicit_country_value,
            address_text=primary_address,
            phone_values=phone_values,
            website_host=website_host,
            country_source=country_source,
        )
        facility_type = _normalize_text_value(
            next((row.raw_value for row in evidence if row.field_name == "facility_type"), None)
        )
        unique_location_addresses: list[str] = []
        seen_addresses: set[str] = set()
        for row in addresses:
            address = _normalize_text_value(row.raw_value)
            if not address or address.casefold() in seen_addresses:
                continue
            seen_addresses.add(address.casefold())
            unique_location_addresses.append(address)
        multi_branch = len(unique_location_addresses) > 1
        hard_gate_locations: list[HardGateLocation] = []
        for index, address in enumerate(unique_location_addresses):
            has_phone_for_location = bool(phone_values) and (not multi_branch or index == 0)
            completeness, gap = _location_completeness(
                address=address, has_phone=has_phone_for_location
            )
            hard_gate_locations.append(
                HardGateLocation(
                    full_address=address,
                    country_containment_status=assess_country_containment(
                        target_country_code=loaded.execution.country_code,
                        extracted_country_code=extracted_country_code,
                        extracted_country_raw=explicit_country_value,
                        address_text=address,
                        phone_values=phone_values if has_phone_for_location else [],
                        website_host=website_host,
                        country_source=country_source,
                    ).status,
                    location_completeness_status=completeness,
                    location_gap_reason=gap,
                )
            )
        hard_gate_results = hard_gate_verification_service.evaluate(
            HardGateVerificationInput(
                target_country_code=loaded.execution.country_code,
                mission_profile=_mission_profile(loaded.execution),
                facility_country_containment_status=containment.status,
                facility_type=facility_type or None,
                locations=hard_gate_locations,
                phone_values=phone_values,
                verified_evidence_count=len(evidence),
                evidence=[
                    HardGateEvidence(
                        field_name=row.field_name,
                        raw_value=_normalize_text_value(row.raw_value) or None,
                        evidence_quote=row.evidence_quote,
                    )
                    for row in evidence
                ],
            )
        )
        return _PublicationPlan(
            normalized_name=normalized_name,
            raw_name=candidate.raw_name,
            country_code=country_code,
            country_name=country_name,
            country_source=country_source,
            explicit_country_value=explicit_country_value,
            extracted_country_code=extracted_country_code,
            facility_type=(facility_type or "rehabilitation_or_addiction_treatment")[:80],
            primary_address=primary_address,
            primary_region=None,
            primary_city=None,
            primary_website=primary_website,
            aliases=aliases,
            locations=addresses,
            contacts=contacts,
            services=services,
            programs=programs,
            populations_served=populations_served,
            admissions_eligibility=admissions_eligibility,
            unresolved=unresolved,
            evidence=evidence,
            containment=containment,
            hard_gate_results=hard_gate_results,
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
        self,
        db: AsyncSession,
        loaded: _LoadedCandidate,
        plan: _PublicationPlan,
        *,
        confidence: Decimal,
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
            confidence_score=confidence,
            duplicate_status="unique",
            human_review_status=_human_review_for_plan(plan, confidence),
            country_containment_status=plan.containment.status,
            country_containment_reason=plan.containment.reason,
            country_containment_signals_json=plan.containment.signals,
            publication_class=plan.hard_gate_results.publication_class,
            hard_gate_results_json=_safe_metadata(plan.hard_gate_results.as_json()),
            is_mock=False,
            last_verified_at=datetime.now(UTC),
        )
        db.add(facility)
        await db.flush()
        return facility

    async def _find_merge_target(
        self,
        db: AsyncSession,
        *,
        execution_id: str,
        plan: _PublicationPlan,
        country_code: str,
    ) -> RehabilitationFacility | None:
        result = await db.execute(
            select(RehabilitationFacility)
            .where(
                RehabilitationFacility.execution_id == execution_id,
                RehabilitationFacility.country_code == country_code,
                RehabilitationFacility.is_mock.is_(False),
            )
            .options(
                selectinload(RehabilitationFacility.contacts),
                selectinload(RehabilitationFacility.locations),
            )
        )
        target = plan.normalized_name.casefold()
        for facility in result.scalars().all():
            if (facility.canonical_name or "").casefold() == target:
                verdict = branch_identity_service.compare(
                    _branch_identity_input_for_facility(facility),
                    _branch_identity_input_for_plan(plan),
                )
                if verdict.should_merge:
                    return facility
        return None

    async def _after_facility_created(
        self, db: AsyncSession, facility: RehabilitationFacility
    ) -> None:
        await self._link_possible_duplicates(db, facility)

    async def _link_possible_duplicates(
        self, db: AsyncSession, facility: RehabilitationFacility
    ) -> None:
        threshold = float(get_settings().facility_publication_duplicate_match_threshold)
        peers = (
            await db.execute(
                select(RehabilitationFacility).where(
                    RehabilitationFacility.execution_id == facility.execution_id,
                    RehabilitationFacility.id != facility.id,
                    RehabilitationFacility.country_code == facility.country_code,
                    RehabilitationFacility.is_mock.is_(False),
                )
            )
        ).scalars().all()
        for peer in peers:
            score = SequenceMatcher(
                None,
                (facility.canonical_name or "").casefold(),
                (peer.canonical_name or "").casefold(),
            ).ratio()
            if score < threshold or score >= 1.0:
                continue
            left_id, right_id = sorted([facility.id, peer.id])
            existing = await db.scalar(
                select(RehabilitationPossibleDuplicate.id).where(
                    RehabilitationPossibleDuplicate.execution_id == facility.execution_id,
                    RehabilitationPossibleDuplicate.left_facility_id == left_id,
                    RehabilitationPossibleDuplicate.right_facility_id == right_id,
                )
            )
            if existing is not None:
                continue
            db.add(
                RehabilitationPossibleDuplicate(
                    execution_id=facility.execution_id,
                    left_facility_id=left_id,
                    right_facility_id=right_id,
                    match_score=Decimal(str(round(score, 4))),
                    matching_reasons=(
                        "Similar canonical names within the same country execution; "
                        "human review required."
                    ),
                    resolution_status="possible",
                    is_mock=False,
                )
            )
            if facility.duplicate_status == "unique":
                facility.duplicate_status = "possible_duplicate"
                facility.human_review_status = "required"
            if peer.duplicate_status == "unique":
                peer.duplicate_status = "possible_duplicate"
                peer.human_review_status = "required"
        await db.flush()

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
        self,
        db: AsyncSession,
        facility: RehabilitationFacility,
        plan: _PublicationPlan,
        *,
        confidence: Decimal,
    ) -> int:
        count = 0
        seen: set[str] = set()
        unique_addresses: list[str] = []
        for row in plan.locations:
            address = _normalize_text_value(row.raw_value)
            if not address or address.casefold() in seen:
                continue
            seen.add(address.casefold())
            unique_addresses.append(address)
        multi_branch = len(unique_addresses) > 1
        facility_phones = [
            value for contact_type, value, _, _ in plan.contacts if contact_type == "phone"
        ]
        has_facility_phone = bool(facility_phones)
        seen.clear()
        for address in unique_addresses:
            # HQ/facility phone may verify the primary site only; branches need their own phone.
            has_phone_for_location = has_facility_phone and (not multi_branch or count == 0)
            loc_containment = assess_country_containment(
                target_country_code=facility.country_code,
                extracted_country_code=plan.extracted_country_code,
                extracted_country_raw=plan.explicit_country_value,
                address_text=address,
                phone_values=facility_phones if has_phone_for_location else [],
                website_host=None,
                country_source=plan.country_source,
            )
            completeness, gap = _location_completeness(
                address=address,
                has_phone=has_phone_for_location,
            )
            location_gate = hard_gate_verification_service.location_snapshot(
                location=HardGateLocation(
                    full_address=address,
                    country_containment_status=loc_containment.status,
                    location_completeness_status=completeness,
                    location_gap_reason=gap,
                ),
                has_phone=has_phone_for_location,
            )
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
                    confidence_score=confidence,
                    country_containment_status=loc_containment.status,
                    country_containment_reason=loc_containment.reason,
                    country_containment_signals_json=loc_containment.signals,
                    location_completeness_status=completeness,
                    location_gap_reason=gap,
                    hard_gate_results_json=_safe_metadata(location_gate),
                    is_mock=False,
                )
            )
            count += 1
        await db.flush()
        return count

    async def _add_contacts(
        self,
        db: AsyncSession,
        facility: RehabilitationFacility,
        plan: _PublicationPlan,
        *,
        confidence: Decimal,
    ) -> int:
        count = 0
        seen: set[tuple[str, str]] = set()
        primary_contact_types: set[str] = set()
        primary_location_id = next(
            (loc.id for loc in facility.locations if loc.is_primary),
            facility.locations[0].id if facility.locations else None,
        )
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
                    location_id=primary_location_id if contact_type == "phone" else None,
                    contact_type=contact_type,
                    label=None,
                    value=value[:512],
                    normalized_value=(normalized or value)[:512],
                    is_primary=is_primary,
                    available_24_7=False,
                    verification_status=VERIFICATION_STATUS,
                    confidence_score=confidence,
                    contact_discovery_status=(
                        "verified"
                        if plan.hard_gate_results.publication_class == "verified"
                        and contact_type == "phone"
                        else "found_unverified"
                    ),
                    is_mock=False,
                )
            )
            count += 1
        await db.flush()
        return count

    async def _add_text_attributes(
        self,
        db: AsyncSession,
        facility: RehabilitationFacility,
        rows: list[ScrapingFacilityCandidateEvidence],
        *,
        attribute_group: str,
        confidence: Decimal,
    ) -> int:
        count = 0
        seen_keys: set[str] = set()
        for row in rows:
            display = _normalize_text_value(row.raw_value)
            if not display:
                continue
            base_key = _attribute_key(display)
            attribute_key = base_key
            suffix = 2
            while attribute_key in seen_keys:
                attribute_key = f"{base_key}_{suffix}"[:120]
                suffix += 1
            seen_keys.add(attribute_key)
            existing = await db.scalar(
                select(RehabilitationFacilityAttribute.id).where(
                    RehabilitationFacilityAttribute.facility_id == facility.id,
                    RehabilitationFacilityAttribute.attribute_group == attribute_group,
                    RehabilitationFacilityAttribute.attribute_key == attribute_key,
                )
            )
            if existing is not None:
                continue
            db.add(
                RehabilitationFacilityAttribute(
                    facility_id=facility.id,
                    attribute_group=attribute_group,
                    attribute_key=attribute_key,
                    display_name=display[:255],
                    value_type="text",
                    value_text=display,
                    verification_status=VERIFICATION_STATUS,
                    confidence_score=confidence,
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
        *,
        confidence: Decimal,
    ) -> tuple[int, list[dict[str, Any]]]:
        count = 0
        metadata: list[dict[str, Any]] = []
        field_counts: dict[str, int] = {}
        published_contact_evidence_ids = {
            row.id for _type, _value, _normalized, row in plan.contacts
        }
        for row in plan.evidence:
            if row.field_name in {"license_or_registration", "operator"}:
                continue
            if (
                row.field_name in {"phones", "emails", "websites"}
                and row.id not in published_contact_evidence_ids
            ):
                continue
            attribute_plan_rows = {
                "services": plan.services,
                "programs": plan.programs,
                "populations_served": plan.populations_served,
                "admissions_eligibility": plan.admissions_eligibility,
            }
            if row.field_name in attribute_plan_rows and row.id not in {
                attr_row.id for attr_row in attribute_plan_rows[row.field_name]
            }:
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
                    confidence_score=confidence,
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
) -> tuple[str, str, str, str | None, str | None]:
    """Return mission-scoped country plus optional extracted country code.

    Mismatches no longer hard-skip; containment service classifies them as
    confirmed_outside / uncertain for soft publish.
    """
    from app.services.scraping.countries import COUNTRIES

    explicit = next((row for row in evidence if row.field_name in {"country", "countries"}), None)
    execution_country = resolve_country(execution.country_code)
    if explicit is None:
        return execution_country.code, execution_country.name, "execution_scope", None, None
    raw_value = _normalize_text_value(explicit.raw_value)
    extracted_code: str | None = None
    if len(raw_value) == 2:
        try:
            extracted_code = resolve_country(raw_value).code
        except ValidationError:
            extracted_code = None
    if extracted_code is None:
        match = next(
            (
                country
                for country in COUNTRIES.values()
                if country.name.casefold() == raw_value.casefold()
            ),
            None,
        )
        extracted_code = match.code if match else None

    return (
        execution_country.code,
        execution_country.name,
        "extracted_evidence" if extracted_code else "execution_scope",
        raw_value or None,
        extracted_code,
    )


def _human_review_for_plan(plan: _PublicationPlan, confidence: Decimal) -> str:
    if plan.hard_gate_results.publication_class != "verified":
        return "required"
    if confidence < Decimal("0.85"):
        return "required"
    return "not_required"


def _location_completeness(*, address: str, has_phone: bool) -> tuple[str, str | None]:
    if address and has_phone:
        return "complete", None
    if not address and not has_phone:
        return "incomplete", "location_missing"
    if not address:
        return "incomplete", "location_missing"
    return "incomplete", "phone_missing"


ContactPlan = tuple[str, str, str | None, ScrapingFacilityCandidateEvidence]
UnresolvedPlan = tuple[str, str, str]


@dataclass
class _SyntheticAddressEvidence:
    raw_value: str
    field_name: str = "addresses"
    evidence_quote: str | None = None


def _merge_deterministic_contact_extracts(
    document: ScrapingSourceDocument,
    *,
    addresses: list[Any],
    contacts: list[ContactPlan],
    anchor_evidence: ScrapingFacilityCandidateEvidence,
) -> tuple[list[Any], list[ContactPlan]]:
    """Phase B2: fold tel/mailto/JSON-LD extracts into publication candidates."""
    payload = (document.metadata_json or {}).get("deterministic_contacts")
    if not isinstance(payload, dict):
        return addresses, contacts
    seen_addresses = {
        _normalize_text_value(getattr(row, "raw_value", None)).casefold()
        for row in addresses
        if _normalize_text_value(getattr(row, "raw_value", None))
    }
    for item in payload.get("addresses") or []:
        if not isinstance(item, dict):
            continue
        value = _normalize_text_value(item.get("value"))
        if not value or value.casefold() in seen_addresses:
            continue
        seen_addresses.add(value.casefold())
        addresses = [
            *addresses,
            _SyntheticAddressEvidence(
                raw_value=value,
                evidence_quote=(item.get("evidence_quote") or value)[:1000],
            ),
        ]
    seen_contacts = {(ctype, value.casefold()) for ctype, value, _, _ in contacts}
    for contact_type, key in (("phone", "phones"), ("email", "emails")):
        for item in payload.get(key) or []:
            if not isinstance(item, dict):
                continue
            value = _normalize_text_value(item.get("value"))
            if not value:
                continue
            if (contact_type, value.casefold()) in seen_contacts:
                continue
            if contact_type == "email":
                normalized = _normalize_email(value)
                if normalized is None:
                    continue
                contacts = [*contacts, ("email", normalized, normalized, anchor_evidence)]
                seen_contacts.add(("email", normalized.casefold()))
            else:
                normalized = _normalize_phone(value)
                contacts = [*contacts, ("phone", value, normalized, anchor_evidence)]
                seen_contacts.add(("phone", value.casefold()))
    return addresses, contacts


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


def _score_confidence(
    candidate: ScrapingFacilityCandidate, plan: _PublicationPlan
) -> Decimal:
    model = (
        float(candidate.model_confidence)
        if candidate.model_confidence is not None
        else 0.70
    )
    verified_count = len(plan.evidence)
    evidence_factor = 0.55
    if verified_count >= 4:
        evidence_factor = 0.92
    elif verified_count >= 2:
        evidence_factor = 0.85
    elif verified_count == 1:
        evidence_factor = 0.75
    contact_bonus = 0.04 if plan.contacts else 0.0
    location_bonus = 0.03 if plan.locations else 0.0
    score = min(0.99, max(0.10, model * 0.55 + evidence_factor * 0.45 + contact_bonus + location_bonus))
    return Decimal(str(round(score, 4)))


def _attribute_key(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    return (slug or "service")[:120]


def _field_path(row: ScrapingFacilityCandidateEvidence, counts: dict[str, int]) -> str:
    base = {
        "name": "canonical_name",
        "aliases": "aliases.extracted",
        "facility_type": "facility_type",
        "addresses": "locations.address",
        "phones": "contacts.phone",
        "emails": "contacts.email",
        "websites": "contacts.website",
        "services": "attributes.treatment_service",
        "programs": "attributes.program",
        "populations_served": "attributes.population_served",
        "admissions_eligibility": "attributes.admission_eligibility",
    }.get(row.field_name, row.field_name)
    index = counts.get(base, 0)
    counts[base] = index + 1
    if index == 0 and base in {"canonical_name", "facility_type"}:
        return base
    return f"{base}.{row.evidence_hash[:8]}"


def _mission_profile(execution: ScrapingExecution) -> str:
    country_profile = execution.country_profile_json or {}
    blueprint_json = execution.blueprint.blueprint_json if execution.blueprint else {}
    for key in (
        "mission_profile",
        "publication_profile",
        "dataset_profile",
        "collection_profile",
    ):
        value = country_profile.get(key)
        if isinstance(value, str) and value.strip():
            return value
        value = blueprint_json.get(key) if isinstance(blueprint_json, dict) else None
        if isinstance(value, str) and value.strip():
            return value
    return "full_national_census"


def _branch_identity_input_for_plan(plan: _PublicationPlan) -> BranchIdentityInput:
    phone_values = [
        normalized or value
        for contact_type, value, normalized, _row in plan.contacts
        if contact_type == "phone"
    ]
    website = next(
        (
            normalized or value
            for contact_type, value, normalized, _row in plan.contacts
            if contact_type == "website"
        ),
        plan.primary_website,
    )
    return BranchIdentityInput(
        canonical_name=plan.normalized_name,
        address=plan.primary_address,
        phone_values=phone_values,
        website_host=urlsplit(website).hostname if website else None,
    )


def _branch_identity_input_for_facility(facility: RehabilitationFacility) -> BranchIdentityInput:
    phone_values = [
        contact.normalized_value or contact.value
        for contact in facility.contacts
        if contact.contact_type == "phone"
    ]
    primary_location = next((location for location in facility.locations if location.is_primary), None)
    return BranchIdentityInput(
        canonical_name=facility.canonical_name,
        address=facility.primary_address or (primary_location.full_address if primary_location else None),
        postal_code=primary_location.postal_code if primary_location else None,
        phone_values=phone_values,
        latitude=float(facility.latitude) if facility.latitude is not None else None,
        longitude=float(facility.longitude) if facility.longitude is not None else None,
        website_host=urlsplit(facility.primary_website).hostname if facility.primary_website else None,
    )


def _safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return value


def _safe_reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "validation_error"
    if isinstance(exc, IntegrityError):
        return "integrity_error"
    return "publication_failed"


facility_candidate_publication_service = FacilityCandidatePublicationService()
