"""Phase 1: classify all relevant Maps places (rules → structured AI → Sonar classify)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import or_, select

from app.core.config import get_settings
from app.db.models import (
    MapsCensusRun,
    MapsClientEligibility,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
)
from app.services.scraping.maps_classification_rules import (
    apply_deterministic_classification,
)
from app.services.scraping.maps_eligibility import (
    compute_client_eligibility,
    derive_is_relevant,
    derive_legacy_verification_verdict,
)
from app.services.scraping.maps_enrichment_processing_state import MapsEnrichmentPipelineState
from app.services.scraping.maps_enrichment_response_parser import EnrichmentParseStats
from app.services.scraping.maps_enrichment_selection import is_detail_enrichment_candidate
from app.services.scraping.maps_place_enrichment_service import (
    MapsPlaceEnrichmentResult,
    maps_place_enrichment_service,
)
from app.services.scraping.maps_place_website_resolution import (
    CRAWLABLE_RELATIONSHIPS,
    apply_website_resolution,
    resolve_official_website,
)
from app.services.scraping.maps_quota_tracker import MapsQuotaTracker
from app.services.scraping.maps_sonar_classify import (
    apply_sonar_bucket_lifecycle,
    fetch_sonar_classify_one,
    needs_sonar_classification,
)
from app.services.scraping.maps_sonar_fallback import SonarBudget, SonarFallbackStats
from app.services.scraping.maps_structured_classification import (
    apply_deterministic_classification_fields,
    create_structured_classification_provider,
    map_classification_to_enrichment_fields,
)
from app.services.scraping.maps_website_crawl_service import (
    MapsWebsiteCrawlError,
    maps_website_crawl_service,
    path_keywords_from_country_profile,
)

logger = logging.getLogger(__name__)


def build_classification_query(run_id: str):
    return (
        select(MapsPlace)
        .where(
            MapsPlace.run_id == run_id,
            MapsPlace.is_relevant.is_(True),
            MapsPlace.enrichment_status.in_(
                [
                    MapsPlaceEnrichmentStatus.PENDING.value,
                    MapsPlaceEnrichmentStatus.FAILED.value,
                ]
            ),
            or_(
                MapsPlace.enrichment_pipeline_state.is_(None),
                MapsPlace.enrichment_pipeline_state.in_(
                    [
                        MapsEnrichmentPipelineState.PREFILTER_PENDING.value,
                        MapsEnrichmentPipelineState.PREFILTER_COMPLETED.value,
                        MapsEnrichmentPipelineState.WEBSITE_RESOLUTION_PENDING.value,
                        MapsEnrichmentPipelineState.WEBSITE_RESOLVED.value,
                        MapsEnrichmentPipelineState.WEBSITE_NOT_FOUND.value,
                        MapsEnrichmentPipelineState.CRAWL_PENDING.value,
                        MapsEnrichmentPipelineState.CRAWL_COMPLETED.value,
                        MapsEnrichmentPipelineState.CRAWL_FAILED.value,
                        MapsEnrichmentPipelineState.PRIMARY_EXTRACTION_PENDING.value,
                        MapsEnrichmentPipelineState.PRIMARY_EXTRACTION_FAILED.value,
                    ]
                ),
            ),
        )
        .order_by(MapsPlace.id)
    )


class MapsClassificationService:
    async def classify_run(
        self,
        session_factory,
        *,
        run_id: str,
        country_code: str,
        country_name: str,
        tracker: MapsQuotaTracker,
        sonar_budget: SonarBudget,
        sonar_stats: SonarFallbackStats,
        parse_stats: EnrichmentParseStats,
    ) -> dict[str, Any]:
        settings = get_settings()
        provider = create_structured_classification_provider()
        batch_size = max(1, settings.maps_census_enrichment_processing_batch_size)
        max_calls = max(1, settings.maps_primary_extraction_max_calls_per_run)
        calls = 0
        classified = 0
        paused = False
        cursor: str | None = None

        while True:
            async with session_factory() as session:
                query = build_classification_query(run_id)
                if cursor:
                    query = query.where(MapsPlace.id > cursor)
                places = (await session.execute(query.limit(batch_size))).scalars().all()
                if not places:
                    break

            if calls >= max_calls:
                paused = True
                break

            semaphore = asyncio.Semaphore(max(1, settings.maps_primary_extraction_concurrency))
            timeout = max(30.0, settings.maps_classification_place_timeout_seconds)

            async def process_one(place_id: str) -> int:
                async with semaphore:
                    try:
                        return await asyncio.wait_for(
                            self._classify_place(
                                session_factory,
                                place_id=place_id,
                                country_code=country_code,
                                country_name=country_name,
                                provider=provider,
                                sonar_budget=sonar_budget,
                                sonar_stats=sonar_stats,
                                parse_stats=parse_stats,
                                tracker=tracker,
                            ),
                            timeout=timeout,
                        )
                    except TimeoutError:
                        logger.warning("maps_classification_timeout place=%s", place_id)
                        async with session_factory() as session:
                            place = await session.get(MapsPlace, place_id)
                            if place is not None:
                                place.enrichment_status = MapsPlaceEnrichmentStatus.FAILED.value
                                # Terminal for this pass so the same hanging place is
                                # not re-selected and stalls every later batch.
                                place.enrichment_pipeline_state = (
                                    MapsEnrichmentPipelineState.CLASSIFICATION_FAILED_RETRYABLE.value
                                )
                                place.enrichment_error_message = (
                                    f"classification timeout after {int(timeout)}s"
                                )
                                await session.commit()
                        return 0

            results = await asyncio.gather(*(process_one(p.id) for p in places))
            classified += sum(results)
            calls += len(places)
            cursor = places[-1].id

            async with session_factory() as session:
                run = await session.get(MapsCensusRun, run_id)
                if run is not None:
                    run.heartbeat_at = datetime.now(UTC)
                    await session.commit()

        return {"classified": classified, "paused": paused, "cursor": cursor}

    async def _classify_place(
        self,
        session_factory,
        *,
        place_id: str,
        country_code: str,
        country_name: str,
        provider,
        sonar_budget: SonarBudget,
        sonar_stats: SonarFallbackStats,
        parse_stats: EnrichmentParseStats,
        tracker: MapsQuotaTracker,
    ) -> int:
        from app.services.scraping.maps_place_enrichment_service import (
            _apply_structured_fields,
            _derive_lifecycle_status,
            _derive_relevance_reason,
            _derive_verification_reason,
            _derive_verification_source_url,
            _enforce_field_evidence,
        )

        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place is None:
                return 0
            run = await session.get(MapsCensusRun, place.run_id)
            path_keywords = path_keywords_from_country_profile(
                dict(run.country_profile or {}) if run is not None else None
            )
            place.enrichment_status = MapsPlaceEnrichmentStatus.RUNNING.value
            place.enrichment_attempts = (place.enrichment_attempts or 0) + 1
            place.enrichment_error_message = None
            await session.commit()

        rule = None
        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place is None:
                return 0
            rule = apply_deterministic_classification(place)
            if rule is not None:
                apply_deterministic_classification_fields(place, rule)
                place.is_relevant = derive_is_relevant(place.lifecycle_status)
                place.verification_verdict = derive_legacy_verification_verdict(place.lifecycle_status)
                place.verification_reason = _derive_verification_reason(place)
                place.relevance_reason = _derive_relevance_reason(place)
                place.enrichment_pipeline_state = (
                    MapsEnrichmentPipelineState.DETAIL_NOT_REQUIRED.value
                    if not is_detail_enrichment_candidate(place)
                    else MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value
                )
                if is_detail_enrichment_candidate(place):
                    place.enrichment_status = MapsPlaceEnrichmentStatus.PENDING.value
                else:
                    place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
                    place.enrichment_completed_at = datetime.now(UTC)
                await session.commit()
                return 1

        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place is None:
                return 0
            resolution = await resolve_official_website(
                place,
                country_code=country_code,
                country_name=country_name,
                enable_search=get_settings().maps_census_website_search_enabled,
            )
            apply_website_resolution(place, resolution)
            await session.commit()

        crawl_excerpt: str | None = None
        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place is None:
                return 0
            if place.official_website and place.website_relationship in CRAWLABLE_RELATIONSHIPS:
                # Hard-bound the crawl: a slow domain must not consume the whole
                # per-place budget and stall its batch (see clinique-tabet.com).
                crawl_budget = max(10.0, get_settings().maps_classification_crawl_timeout_seconds)
                try:
                    outcome = await asyncio.wait_for(
                        maps_website_crawl_service.crawl_website(
                            session,
                            website_url=place.official_website,
                            path_keywords=path_keywords,
                        ),
                        timeout=crawl_budget,
                    )
                    tracker.add_crawl_request()
                    place.enrichment_pages_crawled = outcome.page_urls or None
                    crawl_excerpt = outcome.combined_excerpt(
                        max_chars=get_settings().maps_crawl_max_total_context_chars
                    ) or None
                except (MapsWebsiteCrawlError, TimeoutError):
                    logger.info(
                        "maps_classification_crawl_skipped place=%s website=%s",
                        place_id,
                        place.official_website,
                    )
                await session.commit()

        payload = None
        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place is None:
                return 0
            payload = maps_place_enrichment_service._facility_payload(
                place, website_crawl_excerpt=crawl_excerpt
            )

        try:
            ai_result = await provider.classify_one(
                country_code=country_code,
                country_name=country_name,
                facility_payload=payload,
                crawl_excerpt=crawl_excerpt,
            )
            tracker.add_primary_extraction_call()
            mapped = map_classification_to_enrichment_fields(ai_result.output)
            result = MapsPlaceEnrichmentResult.model_validate({"place_id": place_id, **mapped})
            _enforce_field_evidence(result)
            async with session_factory() as session:
                place = await session.get(MapsPlace, place_id)
                if place is None:
                    return 0
                _apply_structured_fields(place, result)
                place.enrichment_extraction_source = "structured_classification"
                await session.commit()
        except (ValidationError, Exception) as exc:  # noqa: BLE001
            logger.warning("maps_structured_classification_failed place=%s error=%s", place_id, exc)
            async with session_factory() as session:
                place = await session.get(MapsPlace, place_id)
                if place is not None:
                    place.enrichment_error_message = str(exc)[:500]
                    await session.commit()

        settings = get_settings()
        use_sonar = False
        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place is None:
                return 0
            place.lifecycle_status = _derive_lifecycle_status(place)
            place.client_eligibility = compute_client_eligibility(place)
            place.is_relevant = derive_is_relevant(place.lifecycle_status)
            place.verification_verdict = derive_legacy_verification_verdict(place.lifecycle_status)
            place.verification_reason = _derive_verification_reason(place)
            place.relevance_reason = _derive_relevance_reason(place)
            use_sonar = (
                needs_sonar_classification(place)
                and sonar_budget.can_call()
                and settings.maps_sonar_fallback_enabled
            )
            await session.commit()

        if use_sonar:
            sonar_budget.calls_used += 1
            sonar_stats.sonar_calls += 1
            sonar_failed = False
            try:
                sonar_result, classified = await fetch_sonar_classify_one(
                    country_code=country_code,
                    country_name=country_name,
                    payload=payload or {},
                    parse_stats=parse_stats,
                )
                tracker.add_sonar_fallback_call()
                async with session_factory() as session:
                    place = await session.get(MapsPlace, place_id)
                    if place is None:
                        return 0
                    _apply_structured_fields(place, sonar_result)
                    apply_sonar_bucket_lifecycle(place, classified.classification_bucket)
                    place.enrichment_extraction_source = "sonar_classification"
                    place.is_relevant = derive_is_relevant(place.lifecycle_status)
                    place.verification_verdict = derive_legacy_verification_verdict(
                        place.lifecycle_status
                    )
                    place.verification_reason = (
                        classified.reason or _derive_verification_reason(place)
                    )[:400]
                    place.verification_source_url = _derive_verification_source_url(sonar_result)
                    place.relevance_reason = _derive_relevance_reason(place)
                    place.enrichment_error_message = None
                    await session.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("maps_sonar_classify_failed place=%s error=%s", place_id, exc)
                sonar_stats.sonar_final_failures += 1
                sonar_failed = True
                async with session_factory() as session:
                    place = await session.get(MapsPlace, place_id)
                    if place is not None:
                        place.enrichment_error_message = f"sonar_classify_failed: {exc}"[:500]
                        place.enrichment_pipeline_state = (
                            MapsEnrichmentPipelineState.CLASSIFICATION_FAILED_RETRYABLE.value
                        )
                        await session.commit()
        else:
            sonar_failed = False
            async with session_factory() as session:
                place = await session.get(MapsPlace, place_id)
                if place is not None and needs_sonar_classification(place):
                    if not sonar_budget.can_call():
                        sonar_stats.budget_exhausted = True

        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place is None:
                return 0

            still_unresolved = needs_sonar_classification(place)
            if sonar_failed and still_unresolved:
                place.enrichment_pipeline_state = (
                    MapsEnrichmentPipelineState.CLASSIFICATION_FAILED_RETRYABLE.value
                )
                place.enrichment_status = MapsPlaceEnrichmentStatus.FAILED.value
            elif is_detail_enrichment_candidate(place):
                place.enrichment_pipeline_state = (
                    MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value
                )
                place.enrichment_status = MapsPlaceEnrichmentStatus.PENDING.value
                place.enrichment_completed_at = None
                if not place.enrichment_error_message:
                    place.enrichment_error_message = None
            else:
                # Classification finished; detail enrichment not required.
                place.enrichment_pipeline_state = (
                    MapsEnrichmentPipelineState.DETAIL_NOT_REQUIRED.value
                )
                place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
                place.enrichment_completed_at = datetime.now(UTC)
            await session.commit()
            return 1

maps_classification_service = MapsClassificationService()

__all__ = ["MapsClassificationService", "build_classification_query", "maps_classification_service"]
