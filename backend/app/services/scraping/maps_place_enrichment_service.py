"""Phase 2: enrich Maps Census facilities via a web-search LLM (no crawling).

For every relevant facility — with or without a website — we ask a web-search
capable model (Perplexity Sonar Pro by default) to find the addictions it treats
and the languages treatment is delivered in. Facilities are processed in small
batches keyed by ``place_id`` so identities never mix. Treatment price is out of
scope.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    MapsCareSetting,
    MapsCensusRun,
    MapsClientEligibility,
    MapsFacilityType,
    MapsLifecycleStatus,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
)
from app.llm.catalog import get_model
from app.llm.providers import get_provider_registry
from app.services.scraping.maps_eligibility import (
    compute_client_eligibility,
    derive_is_relevant,
    derive_legacy_verification_verdict,
)
from app.services.scraping.maps_pydantic_utils import TruncatingModel
from app.services.scraping.maps_enrichment_response_parser import EnrichmentParseStats
from app.services.scraping.maps_quota_tracker import MapsQuotaTracker, merge_quota_metrics
from app.services.scraping.maps_website_crawl_service import (
    MapsWebsiteCrawlError,
    maps_website_crawl_service,
    path_keywords_from_country_profile,
)

logger = logging.getLogger(__name__)

ENRICHMENT_TIMEOUT_SECONDS = 120.0
MAX_QUOTE_CHARACTERS = 240

SUBSTANCE_ADDICTIONS = (
    "Alcohol",
    "Cocaine",
    "Crack",
    "Methamphetamine",
    "Heroin",
    "Prescription Opioids",
    "Benzodiazepines",
    "Cannabis (dependency)",
    "Synthetic Cannabinoids",
    "Synthetic Stimulants",
    "Inhalants",
    "Stimulant Medications",
    "Ketamine",
    "Kratom",
    "MDMA/Ecstasy",
    "GHB",
    "Anabolic Steroids",
    "Novel Psychoactive Substances",
)

BEHAVIORAL_ADDICTIONS = (
    "Gambling",
    "Sex/Pornography",
    "Gaming/Internet",
    "Food/Binge Eating (clinical)",
    "Love/Relationship",
    "Shopping/Spending",
    "Exercise/Body Dysmorphia",
    "Workaholism",
    "Social Media",
    "Cryptocurrency Trading",
)

ADDICTION_TAXONOMY: tuple[str, ...] = SUBSTANCE_ADDICTIONS + BEHAVIORAL_ADDICTIONS

ADDICTION_ALIASES: dict[str, str] = {
    "opioid": "Prescription Opioids",
    "opioids": "Prescription Opioids",
    "fentanyl": "Prescription Opioids",
    "oxycodone": "Prescription Opioids",
    "vicodin": "Prescription Opioids",
    "benzo": "Benzodiazepines",
    "benzos": "Benzodiazepines",
    "marijuana": "Cannabis (dependency)",
    "cannabis": "Cannabis (dependency)",
    "hashish": "Cannabis (dependency)",
    "meth": "Methamphetamine",
    "ecstasy": "MDMA/Ecstasy",
    "mdma": "MDMA/Ecstasy",
    "porn": "Sex/Pornography",
    "sex addiction": "Sex/Pornography",
    "internet addiction": "Gaming/Internet",
    "gaming addiction": "Gaming/Internet",
    "crypto": "Cryptocurrency Trading",
    "cryptocurrency": "Cryptocurrency Trading",
}


class EvidenceField(TruncatingModel):
    model_config = ConfigDict(extra="ignore")

    value: str = Field(default="", max_length=300)
    evidence_quote: str = Field(default="", max_length=MAX_QUOTE_CHARACTERS)
    source_url: str = Field(default="", max_length=1000)


class ClassificationEvidenceField(TruncatingModel):
    model_config = ConfigDict(extra="ignore")

    value: str | bool | list[str] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_quote: str = Field(default="", max_length=MAX_QUOTE_CHARACTERS)
    source_url: str = Field(default="", max_length=1000)
    source_type: str = Field(default="", max_length=100)


class MapsPlaceEnrichmentResult(TruncatingModel):
    model_config = ConfigDict(extra="ignore")

    place_id: str = Field(default="", max_length=64)
    operator_type: str = Field(default="", max_length=40)
    ownership_status: str = Field(default="", max_length=40)
    funding_type: str = Field(default="", max_length=20)
    facility_type: str = Field(default="", max_length=64)
    care_setting: str = Field(default="", max_length=32)
    organization_scope: str = Field(default="", max_length=32)
    addiction_focus_confirmed: bool | None = None
    medical_detox: bool | None = None
    residential_accommodation: bool | None = None
    operating_status: str = Field(default="", max_length=32)
    classification_evidence: dict[str, ClassificationEvidenceField] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("classification_evidence", "evidence_map", "evidence"),
    )
    classification_confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices("classification_confidence", "confidence"),
    )
    addictions_treated: list[EvidenceField] = Field(default_factory=list)
    languages_spoken: list[EvidenceField] = Field(default_factory=list)
    treatment_price: str | None = Field(default=None, max_length=512)


class MapsPlaceEnrichmentBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[MapsPlaceEnrichmentResult] = Field(default_factory=list)


class EnrichmentError(Exception):
    pass


OPERATING_STATUS_VALUES = {"open", "closed", "unknown"}
FUNDING_TYPE_VALUES = {
    "private",
    "public",
    "mixed",
    "donation_based",
    "insurance_based",
    "unknown",
}
OPERATOR_TYPE_VALUES = {item.value for item in MapsOperatorType}
OWNERSHIP_STATUS_VALUES = {item.value for item in MapsOwnershipStatus}
FACILITY_TYPE_VALUES = {item.value for item in MapsFacilityType}
CARE_SETTING_VALUES = {item.value for item in MapsCareSetting}
ORGANIZATION_SCOPE_VALUES = {item.value for item in MapsOrganizationScope}
EVIDENCE_REQUIRED_WHEN_SET: dict[str, set[str]] = {
    "operator_type": {"unknown"},
    "ownership_status": {MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value},
    "facility_type": {"unknown", MapsFacilityType.UNRELATED.value},
    "organization_scope": {"unknown"},
}


class MapsPlaceEnrichmentService:
    async def enrich_run(self, db: AsyncSession | None, *, run_id: str) -> dict[str, int]:
        settings = get_settings()
        if settings.maps_census_cascade_enrichment_enabled:
            from app.services.scraping.maps_enrichment_cascade_service import (
                maps_enrichment_cascade_service,
            )

            return await maps_enrichment_cascade_service.enrich_run(db, run_id=run_id)

        session_factory = self._session_factory(db)
        settings = get_settings()
        if not settings.maps_census_enrichment_enabled:
            async with session_factory() as db:
                run = await db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.enrichment_refresh_completed_at = datetime.now(UTC)
                    await db.commit()
            return {"enriched": 0}

        async with session_factory() as scan_db:
            run = await scan_db.get(MapsCensusRun, run_id)
            if run is None:
                return {"enriched": 0}
            country_code = run.country_code
            country_name = run.country_name
            state = dict(run.processing_state or {})
            cursor: str | None = state.get("enrichment_cursor") if state.get(
                "enrichment_paused"
            ) else None

        # Resumable batch loop (Phase 2 gap #4): previously this sliced pending
        # places to `maps_census_enrichment_max_places_per_run` (250) and
        # silently dropped the rest. It now keyset-paginates by MapsPlace.id
        # in fixed-size batches until none remain or the call budget is hit,
        # persisting a resume cursor on run.processing_state when paused.
        llm_batch_size = max(1, settings.maps_census_enrichment_batch_size)
        processing_batch_size = max(1, settings.maps_census_enrichment_processing_batch_size)
        max_calls = max(1, settings.maps_census_enrichment_max_calls_per_run)
        tracker = MapsQuotaTracker()
        parse_stats = EnrichmentParseStats()
        enriched = 0
        calls_made = 0
        paused = False

        while True:
            async with session_factory() as scan_db:
                query = select(MapsPlace).where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.is_relevant.is_(True),
                    MapsPlace.enrichment_status.in_(
                        [
                            MapsPlaceEnrichmentStatus.PENDING.value,
                            MapsPlaceEnrichmentStatus.FAILED.value,
                            MapsPlaceEnrichmentStatus.SKIPPED.value,
                        ]
                    ),
                )
                if cursor:
                    query = query.where(MapsPlace.id > cursor)
                batch_places = (
                    await scan_db.execute(query.order_by(MapsPlace.id).limit(processing_batch_size))
                ).scalars().all()
                place_ids = [place.id for place in batch_places]

            if not place_ids:
                break
            if calls_made >= max_calls:
                paused = True
                break

            for offset in range(0, len(place_ids), llm_batch_size):
                chunk = place_ids[offset : offset + llm_batch_size]
                enriched += await self._enrich_batch(
                    session_factory,
                    place_ids=chunk,
                    country_code=country_code,
                    country_name=country_name,
                    parse_stats=parse_stats,
                )
                tracker.add_enrichment_call()

            calls_made += len(place_ids)
            cursor = place_ids[-1]

        async with session_factory() as state_db:
            run = await state_db.get(MapsCensusRun, run_id)
            if run is not None:
                state = dict(run.processing_state or {})
                state["enrichment_paused"] = paused
                state["enrichment_cursor"] = cursor if paused else None
                limits_reached = dict(state.get("limits_reached") or {})
                limits_reached["enrichment"] = paused
                state["limits_reached"] = limits_reached
                _merge_enrichment_parse_stats(state, parse_stats)
                run.processing_state = state
                await state_db.commit()

        await merge_quota_metrics(session_factory, run_id=run_id, tracker=tracker)

        async with session_factory() as final_db:
            run = await final_db.get(MapsCensusRun, run_id)
            if run is not None:
                relevant = (
                    await final_db.execute(
                        select(MapsPlace).where(
                            MapsPlace.run_id == run_id,
                            MapsPlace.is_relevant.is_(True),
                        )
                    )
                ).scalars().all()
                # Verification can demote places, so the relevant/website counters are
                # recomputed here rather than trusted from the classification phase.
                run.places_classified_relevant = len(relevant)
                run.places_with_website = sum(
                    1
                    for place in relevant
                    if (place.official_website or place.raw_website or "").strip()
                )
                run.places_enriched = sum(
                    1
                    for place in relevant
                    if place.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
                    and (
                        _non_empty_list(place.addictions_treated)
                        or _non_empty_list(place.languages_spoken)
                    )
                )
                run.enrichment_refresh_attempts = (run.enrichment_refresh_attempts or 0) + 1
                run.enrichment_refresh_completed_at = datetime.now(UTC)
                await final_db.commit()
        return {"enriched": enriched, "enrichment_parse_stats": parse_stats.as_dict()}

    async def _enrich_batch(
        self,
        session_factory,
        *,
        place_ids: list[str],
        country_code: str,
        country_name: str,
        parse_stats: EnrichmentParseStats,
    ) -> int:
        if not place_ids:
            return 0

        async with session_factory() as db:
            places = (
                await db.execute(select(MapsPlace).where(MapsPlace.id.in_(place_ids)))
            ).scalars().all()
            by_id = {place.id: place for place in places}
            ordered = [by_id[pid] for pid in place_ids if pid in by_id]
            run = await db.get(MapsCensusRun, ordered[0].run_id) if ordered else None
            path_keywords = path_keywords_from_country_profile(
                dict(run.country_profile or {}) if run is not None else None
            )
            crawl_excerpts: dict[str, str | None] = {}
            settings = get_settings()
            for place in ordered:
                website = (place.official_website or place.raw_website or "").strip()
                if not website or not settings.maps_census_website_crawl_enabled:
                    crawl_excerpts[place.id] = None
                    continue
                try:
                    outcome = await maps_website_crawl_service.crawl_website(
                        db,
                        website_url=website,
                        path_keywords=path_keywords,
                    )
                    place.enrichment_pages_crawled = outcome.page_urls or None
                    crawl_excerpts[place.id] = (
                        outcome.combined_excerpt(
                            max_chars=settings.maps_census_website_crawl_max_excerpt_chars
                        )
                        or None
                    )
                except MapsWebsiteCrawlError:
                    crawl_excerpts[place.id] = None
            payloads = [
                self._cap_enrichment_payload(
                    self._facility_payload(
                        place,
                        website_crawl_excerpt=crawl_excerpts.get(place.id),
                    )
                )
                for place in ordered
            ]
            for place in ordered:
                place.enrichment_status = MapsPlaceEnrichmentStatus.RUNNING.value
                place.enrichment_attempts = (place.enrichment_attempts or 0) + 1
                place.enrichment_error_message = None
            run_id = ordered[0].run_id if ordered else None
            await db.commit()

        if run_id is not None:
            async with session_factory() as heartbeat_db:
                run = await heartbeat_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.heartbeat_at = datetime.now(UTC)
                    await heartbeat_db.commit()

        try:
            from app.services.scraping.maps_enrichment_fetch import EnrichmentFetchError

            results = await self._search_fields(
                payloads,
                country_code=country_code,
                country_name=country_name,
                parse_stats=parse_stats,
            )
        except EnrichmentFetchError as exc:
            logger.warning(
                "maps_place_enrichment_batch_parse_exhausted count=%s error=%s raw=%s",
                len(payloads),
                exc,
                exc.raw_excerpt,
            )
            await self._mark_batch_enrichment_failed(
                session_factory,
                place_ids=place_ids,
                error_message=str(exc)[:2000],
            )
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "maps_place_enrichment_batch_failed count=%s error=%s",
                len(payloads),
                exc,
            )
            await self._mark_batch_enrichment_failed(
                session_factory,
                place_ids=place_ids,
                error_message=str(exc)[:2000],
            )
            return 0

        by_place = {result.place_id: result for result in results}
        if not by_place:
            # A parseable but empty response is almost always a bad generation. Treat it
            # as a batch failure so a single bad reply can never demote a whole census.
            logger.warning(
                "maps_place_enrichment_batch_returned_no_results count=%s", len(payloads)
            )
            async with session_factory() as db:
                places = (
                    await db.execute(select(MapsPlace).where(MapsPlace.id.in_(place_ids)))
                ).scalars().all()
                for place in places:
                    place.enrichment_status = MapsPlaceEnrichmentStatus.FAILED.value
                    place.enrichment_error_message = "verification returned no results"
                    place.enrichment_completed_at = datetime.now(UTC)
                await db.commit()
            parse_stats.final_failed += len(place_ids)
            return 0

        enriched = 0
        async with session_factory() as db:
            places = (
                await db.execute(select(MapsPlace).where(MapsPlace.id.in_(place_ids)))
            ).scalars().all()
            for place in places:
                result = by_place.get(place.id)
                if result is None:
                    # The model skipped this facility. That is not a verdict, so keep the
                    # place and let the next enrichment pass retry it.
                    place.enrichment_status = MapsPlaceEnrichmentStatus.FAILED.value
                    place.enrichment_error_message = "no verification returned for this place"
                    place.enrichment_completed_at = datetime.now(UTC)
                    continue

                _enforce_field_evidence(result)
                _apply_structured_fields(place, result)
                place.lifecycle_status = _derive_lifecycle_status(place)
                place.client_eligibility = compute_client_eligibility(place)
                place.is_relevant = derive_is_relevant(place.lifecycle_status)
                place.verification_verdict = derive_legacy_verification_verdict(
                    place.lifecycle_status
                )
                place.verification_reason = _derive_verification_reason(place)
                place.verification_source_url = _derive_verification_source_url(result)
                place.relevance_reason = _derive_relevance_reason(place)

                addictions = _normalize_addictions(result.addictions_treated)
                languages = _normalize_languages(result.languages_spoken)
                place.addictions_treated = addictions
                place.languages_spoken = languages
                if result.treatment_price and result.treatment_price.strip():
                    place.treatment_price = result.treatment_price.strip()[:512]
                if place.is_relevant and (addictions or languages):
                    enriched += 1

                place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
                place.enrichment_completed_at = datetime.now(UTC)
                place.enrichment_error_message = None
            await db.commit()
        return enriched

    def _facility_payload(
        self,
        place: MapsPlace,
        *,
        website_crawl_excerpt: str | None = None,
    ) -> dict[str, str | list[str] | None]:
        payload = {
            "place_id": place.id,
            "name": (place.canonical_name or place.raw_name or "").strip(),
            "city": (place.city_name or "").strip() or None,
            "region": (place.region_name or "").strip() or None,
            "address": (place.formatted_address or "").strip() or None,
            "phone": (place.international_phone_number or "").strip() or None,
            "website": (place.official_website or place.raw_website or "").strip() or None,
            "place_types": list(place.place_types or []),
        }
        if website_crawl_excerpt:
            payload["website_crawl_excerpt"] = website_crawl_excerpt
        return payload

    @staticmethod
    def _cap_enrichment_payload(payload: dict) -> dict:
        from app.services.scraping.maps_enrichment_fetch import cap_payload_excerpt

        settings = get_settings()
        return cap_payload_excerpt(
            payload,
            max_chars=max(1, int(settings.maps_census_enrichment_max_crawl_excerpt_chars)),
        )

    async def _search_fields(
        self,
        payloads: list[dict],
        *,
        country_code: str,
        country_name: str,
        parse_stats: EnrichmentParseStats,
    ) -> list[MapsPlaceEnrichmentResult]:
        from app.services.scraping.maps_enrichment_fetch import fetch_enrichment_batch

        settings = get_settings()
        model = get_model(settings.maps_census_enrichment_model)
        provider = get_provider_registry().get_provider(model.provider)
        return await fetch_enrichment_batch(
            provider,
            model_slug=model.provider_model,
            country_code=country_code,
            country_name=country_name,
            payloads=payloads,
            stats=parse_stats,
        )

    async def _mark_batch_enrichment_failed(
        self,
        session_factory,
        *,
        place_ids: list[str],
        error_message: str,
    ) -> None:
        async with session_factory() as db:
            places = (
                await db.execute(select(MapsPlace).where(MapsPlace.id.in_(place_ids)))
            ).scalars().all()
            for place in places:
                place.enrichment_status = MapsPlaceEnrichmentStatus.FAILED.value
                place.enrichment_error_message = error_message
                place.enrichment_completed_at = datetime.now(UTC)
            await db.commit()

    @staticmethod
    def _session_factory(db: AsyncSession | None):
        if db is not None:
            bind = db.bind
            if bind is None:
                bind = db.get_bind()
            from sqlalchemy.ext.asyncio import async_sessionmaker

            return async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)
        from app.db.session import AsyncSessionLocal

        return AsyncSessionLocal


def _merge_enrichment_parse_stats(state: dict, stats: EnrichmentParseStats) -> None:
    existing_raw = dict(state.get("enrichment_parse_stats") or {})
    existing = EnrichmentParseStats(
        parse_failures=int(existing_raw.get("parse_failures") or 0),
        repair_attempts=int(existing_raw.get("repair_attempts") or 0),
        repair_successes=int(existing_raw.get("repair_successes") or 0),
        final_failed=int(existing_raw.get("final_failed") or 0),
    )
    state["enrichment_parse_stats"] = existing.merge(stats).as_dict()


def _normalize_batch_payload(raw: object) -> dict:
    if isinstance(raw, list):
        return {"results": [_normalize_result_payload(item) for item in raw]}
    if isinstance(raw, dict):
        items = raw.get("results") or raw.get("facilities") or raw.get("decisions")
        if isinstance(items, list):
            return {"results": [_normalize_result_payload(item) for item in items]}
        # Single facility object (common for Sonar classify-one prompts).
        if any(
            key in raw
            for key in (
                "facility_type",
                "operator_type",
                "place_id",
                "classification_bucket",
                "ownership_status",
                "addictions_treated",
            )
        ):
            return {"results": [_normalize_result_payload(raw)]}
        return {"results": []}
    return {"results": []}


def _normalize_result_payload(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {}

    payload = dict(raw)
    if "classification_evidence" not in payload:
        payload["classification_evidence"] = (
            payload.get("evidence_map") or payload.get("evidence") or {}
        )
    if "classification_confidence" not in payload:
        payload["classification_confidence"] = payload.get("confidence")
    return payload


def _non_empty_list(value: list[str] | None) -> bool:
    return bool(value and any(str(item).strip() for item in value))


def _enforce_field_evidence(result: MapsPlaceEnrichmentResult) -> None:
    """Phase 3: drop structured values that lack supporting field evidence."""
    for field_name, unknown_values in EVIDENCE_REQUIRED_WHEN_SET.items():
        raw_value = getattr(result, field_name, "")
        normalized = (raw_value or "").strip().casefold() if isinstance(raw_value, str) else raw_value
        if not normalized or normalized in unknown_values:
            continue
        evidence = result.classification_evidence.get(field_name)
        if evidence is None:
            setattr(result, field_name, "")
            continue
        if not evidence.evidence_quote.strip() or not evidence.source_url.strip():
            setattr(result, field_name, "")


def _apply_structured_fields(place: MapsPlace, result: MapsPlaceEnrichmentResult) -> None:
    place.operator_type = _normalize_choice(result.operator_type, OPERATOR_TYPE_VALUES)
    place.ownership_status = _normalize_choice(result.ownership_status, OWNERSHIP_STATUS_VALUES)
    place.funding_type = _normalize_choice(result.funding_type, FUNDING_TYPE_VALUES)
    place.facility_type = _normalize_choice(result.facility_type, FACILITY_TYPE_VALUES)
    place.care_setting = _normalize_choice(result.care_setting, CARE_SETTING_VALUES)
    place.organization_scope = _normalize_choice(
        result.organization_scope, ORGANIZATION_SCOPE_VALUES
    )
    place.addiction_focus_confirmed = result.addiction_focus_confirmed
    place.medical_detox = result.medical_detox
    place.residential_accommodation = result.residential_accommodation
    place.operating_status = _normalize_choice(result.operating_status, OPERATING_STATUS_VALUES)
    place.classification_evidence = _dump_classification_evidence(result.classification_evidence)
    place.classification_confidence = result.classification_confidence


def _derive_lifecycle_status(place: MapsPlace) -> str:
    if place.operating_status == "closed":
        return MapsLifecycleStatus.PERMANENTLY_CLOSED.value

    if place.ownership_status == MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value or place.operator_type in {
        MapsOperatorType.PUBLIC_HOSPITAL.value,
        MapsOperatorType.GOVERNMENT_AGENCY.value,
    }:
        return MapsLifecycleStatus.CONFIRMED_PUBLIC.value

    if (
        place.organization_scope == MapsOrganizationScope.INDIVIDUAL_PRACTICE.value
        or place.operator_type == MapsOperatorType.INDIVIDUAL_PRACTICE.value
        or place.facility_type
        in {
            MapsFacilityType.INDIVIDUAL_ADDICTOLOGIST.value,
            MapsFacilityType.THERAPIST_OR_COUNSELOR.value,
        }
    ):
        return MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value

    if place.facility_type == MapsFacilityType.CESSATION_SERVICE.value:
        return MapsLifecycleStatus.CONFIRMED_CESSATION_ONLY.value

    if place.facility_type == MapsFacilityType.UNRELATED.value:
        return MapsLifecycleStatus.UNRELATED.value

    if place.facility_type == MapsFacilityType.HARM_REDUCTION_ONLY.value:
        return MapsLifecycleStatus.CONTRADICTED.value

    if (
        place.facility_type == MapsFacilityType.GENERAL_MENTAL_HEALTH_CLINIC.value
        and place.addiction_focus_confirmed is not True
    ):
        return MapsLifecycleStatus.CONTRADICTED.value

    client_eligibility = compute_client_eligibility(place)
    if client_eligibility == MapsClientEligibility.ELIGIBLE.value:
        return MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value

    if client_eligibility == MapsClientEligibility.REVIEW.value:
        if place.ownership_status == MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value:
            return MapsLifecycleStatus.PROBABLE_ELIGIBLE.value
        return MapsLifecycleStatus.NEEDS_REVIEW.value

    # Preserve uncertain or partially evidenced candidates for manual review unless
    # the structured fields positively contradicted the facility above.
    return MapsLifecycleStatus.NEEDS_REVIEW.value


def _derive_verification_reason(place: MapsPlace) -> str:
    reasons = {
        MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value: "structured enrichment confirmed an eligible non-government addiction facility",
        MapsLifecycleStatus.PROBABLE_ELIGIBLE.value: "structured enrichment found probable non-government addiction treatment evidence",
        MapsLifecycleStatus.NEEDS_REVIEW.value: "structured enrichment found insufficient evidence for automatic confirmation",
        MapsLifecycleStatus.CONFIRMED_PUBLIC.value: "structured enrichment identified a public or government-operated provider",
        MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value: "structured enrichment identified an individual practitioner rather than a center",
        MapsLifecycleStatus.CONFIRMED_CESSATION_ONLY.value: "structured enrichment identified a cessation-only service",
        MapsLifecycleStatus.CONTRADICTED.value: "structured enrichment contradicted an in-scope addiction treatment facility",
        MapsLifecycleStatus.UNRELATED.value: "structured enrichment found an unrelated facility",
        MapsLifecycleStatus.PERMANENTLY_CLOSED.value: "structured enrichment found the facility is closed",
    }
    return reasons.get(place.lifecycle_status, "structured enrichment updated facility classification")[
        :400
    ]


def _derive_verification_source_url(result: MapsPlaceEnrichmentResult) -> str | None:
    for field in result.classification_evidence.values():
        if field.source_url.strip():
            return field.source_url.strip()[:1024]
    for field in [*result.addictions_treated, *result.languages_spoken]:
        if field.source_url.strip():
            return field.source_url.strip()[:1024]
    return None


def _derive_relevance_reason(place: MapsPlace) -> str:
    reasons = {
        MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value: "confirmed eligible by structured enrichment",
        MapsLifecycleStatus.PROBABLE_ELIGIBLE.value: "kept for review after structured enrichment",
        MapsLifecycleStatus.NEEDS_REVIEW.value: "kept for review pending stronger structured evidence",
        MapsLifecycleStatus.CONFIRMED_PUBLIC.value: "excluded: public or government-operated provider",
        MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value: "individual practitioner retained outside center export",
        MapsLifecycleStatus.CONFIRMED_CESSATION_ONLY.value: "excluded: cessation-only service",
        MapsLifecycleStatus.CONTRADICTED.value: "excluded: structured evidence contradicted an in-scope addiction facility",
        MapsLifecycleStatus.UNRELATED.value: "excluded: unrelated facility",
        MapsLifecycleStatus.PERMANENTLY_CLOSED.value: "excluded: permanently closed",
    }
    return reasons.get(place.lifecycle_status, "structured enrichment updated facility classification")[
        :300
    ]


def _normalize_choice(value: str | None, allowed: set[str]) -> str | None:
    candidate = (value or "").strip().casefold()
    return candidate if candidate in allowed else None


def _dump_classification_evidence(
    evidence: dict[str, ClassificationEvidenceField],
) -> dict[str, dict] | None:
    if not evidence:
        return None
    dumped = {
        key: field.model_dump(mode="json", exclude_none=True)
        for key, field in evidence.items()
        if key.strip()
    }
    return dumped or None


def _normalize_languages(fields: list[EvidenceField]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for field in fields:
        value = (field.value or "").strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    return values


def _normalize_addictions(fields: list[EvidenceField]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for field in fields:
        raw = (field.value or "").strip()
        if not raw:
            continue
        canonical = _canonical_addiction(raw)
        if canonical is None or canonical in seen:
            continue
        seen.add(canonical)
        values.append(canonical)
    return values


def _canonical_addiction(raw: str) -> str | None:
    cleaned = " ".join(raw.split())
    if cleaned in ADDICTION_TAXONOMY:
        return cleaned
    folded = cleaned.casefold()
    for label in ADDICTION_TAXONOMY:
        if label.casefold() == folded:
            return label
    for alias, label in ADDICTION_ALIASES.items():
        if alias in folded:
            return label
    return None


maps_place_enrichment_service = MapsPlaceEnrichmentService()
