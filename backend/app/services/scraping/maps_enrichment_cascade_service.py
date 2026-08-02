"""Cascaded Maps enrichment: selection → website → crawl → primary → eligibility → Sonar fallback."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    MapsCensusRun,
    MapsClientEligibility,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
    MapsCensusStatus,
)
from app.services.scraping.maps_eligibility import (
    compute_client_eligibility,
    derive_is_relevant,
    derive_legacy_verification_verdict,
)
from app.services.scraping.maps_enrichment_processing_state import (
    MapsEnrichmentPipelineState,
    default_pipeline_state,
)
from app.services.scraping.maps_enrichment_response_parser import EnrichmentParseStats
from app.services.scraping.maps_enrichment_selection import (
    build_expensive_pipeline_query,
    build_selection_report,
    should_select_for_expensive_pipeline,
    skip_reason_for_place,
)
from app.services.scraping.maps_place_enrichment_service import (
    MapsPlaceEnrichmentResult,
    maps_place_enrichment_service,
)
from app.services.scraping.maps_place_website_resolution import (
    CRAWLABLE_RELATIONSHIPS,
    apply_website_resolution,
    resolve_official_website,
)
from app.services.scraping.maps_primary_extraction import (
    create_primary_extraction_provider,
    map_primary_to_enrichment_fields,
)
from app.services.scraping.maps_quota_tracker import MapsQuotaTracker, merge_quota_metrics
from app.services.scraping.maps_sonar_fallback import (
    SonarBudget,
    SonarFallbackStats,
    fetch_sonar_fallback_one,
    sonar_fallback_reason,
)
from app.services.scraping.maps_website_crawl_service import (
    MapsWebsiteCrawlError,
    maps_website_crawl_service,
    path_keywords_from_country_profile,
)

logger = logging.getLogger(__name__)


class MapsEnrichmentCascadeService:
    async def enrich_run(self, db: AsyncSession | None, *, run_id: str) -> dict[str, Any]:
        session_factory = maps_place_enrichment_service._session_factory(db)
        settings = get_settings()

        if not settings.maps_census_enrichment_enabled:
            async with session_factory() as session:
                run = await session.get(MapsCensusRun, run_id)
                if run is not None:
                    run.enrichment_refresh_completed_at = datetime.now(UTC)
                    await session.commit()
            return {"enriched": 0}

        async with session_factory() as session:
            run = await session.get(MapsCensusRun, run_id)
            if run is None:
                return {"enriched": 0}
            state = dict(run.processing_state or {})
            if state.get("enrichment_pipeline_paused") or state.get("campaign_paused"):
                return {"enriched": 0, "paused": True}
            country_code = run.country_code
            country_name = run.country_name
            selection_report = await build_selection_report(session, run_id=run_id)
            cursor = state.get("enrichment_cursor") if state.get("enrichment_paused") else None

        tracker = MapsQuotaTracker()
        parse_stats = EnrichmentParseStats()
        sonar_stats = SonarFallbackStats()
        primary_provider = create_primary_extraction_provider()
        processing_batch_size = max(1, settings.maps_census_enrichment_processing_batch_size)
        max_primary_calls = max(1, settings.maps_primary_extraction_max_calls_per_run)
        primary_calls = 0
        enriched = 0
        paused = False
        sonar_budget = SonarBudget(
            enabled=settings.maps_sonar_fallback_enabled,
            max_percent=settings.maps_sonar_fallback_max_percent,
            max_per_campaign=settings.maps_sonar_fallback_max_per_campaign,
            selected_candidates=selection_report.selected_count,
            calls_used=int((state.get("sonar_fallback_stats") or {}).get("sonar_calls") or 0),
        )

        while True:
            async with session_factory() as session:
                query = build_expensive_pipeline_query(run_id)
                if cursor:
                    query = query.where(MapsPlace.id > cursor)
                places = (
                    await session.execute(query.limit(processing_batch_size))
                ).scalars().all()
                if not places:
                    break

            if primary_calls >= max_primary_calls:
                paused = True
                break

            semaphore = asyncio.Semaphore(max(1, settings.maps_primary_extraction_concurrency))

            async def process_one(place_id: str) -> int:
                async with semaphore:
                    return await self._process_place(
                        session_factory,
                        place_id=place_id,
                        country_code=country_code,
                        country_name=country_name,
                        primary_provider=primary_provider,
                        sonar_budget=sonar_budget,
                        sonar_stats=sonar_stats,
                        parse_stats=parse_stats,
                        tracker=tracker,
                    )

            results = await asyncio.gather(*(process_one(place.id) for place in places))
            enriched += sum(results)
            primary_calls += len(places)
            cursor = places[-1].id

        async with session_factory() as session:
            skipped_places = (
                await session.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.is_relevant.is_(True),
                        MapsPlace.enrichment_status.in_(
                            [
                                MapsPlaceEnrichmentStatus.PENDING.value,
                                MapsPlaceEnrichmentStatus.FAILED.value,
                            ]
                        ),
                    )
                )
            ).scalars().all()
            for place in skipped_places:
                if should_select_for_expensive_pipeline(place):
                    continue
                await self._finalize_skipped_place(session, place, skip_reason_for_place(place))

            run = await session.get(MapsCensusRun, run_id)
            if run is not None:
                state = dict(run.processing_state or {})
                state["enrichment_paused"] = paused
                state["enrichment_cursor"] = cursor if paused else None
                state["enrichment_pipeline"] = "cascade_v1"
                state["enrichment_selection"] = {
                    "sql": selection_report.selection_sql,
                    "selected_count": selection_report.selected_count,
                    "skipped_count": selection_report.skipped_count,
                    "skip_reasons": selection_report.skip_reasons,
                }
                state["sonar_fallback_stats"] = sonar_stats.as_dict()
                state["sonar_fallback_budget"] = {
                    "enabled": sonar_budget.enabled,
                    "max_calls": sonar_budget.max_calls,
                    "calls_used": sonar_budget.calls_used,
                    "remaining": sonar_budget.remaining,
                    "budget_exhausted": sonar_stats.budget_exhausted,
                }
                limits_reached = dict(state.get("limits_reached") or {})
                limits_reached["enrichment"] = paused
                limits_reached["sonar_fallback"] = sonar_stats.budget_exhausted
                state["limits_reached"] = limits_reached
                run.processing_state = state
                await session.commit()

        await merge_quota_metrics(session_factory, run_id=run_id, tracker=tracker)
        await self._refresh_run_counters(session_factory, run_id=run_id)
        return {
            "enriched": enriched,
            "selection": selection_report.__dict__,
            "sonar_fallback_stats": sonar_stats.as_dict(),
            "paused": paused,
        }

    async def _process_place(
        self,
        session_factory,
        *,
        place_id: str,
        country_code: str,
        country_name: str,
        primary_provider,
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
            _normalize_addictions,
            _normalize_languages,
        )

        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place is None:
                return 0
            run = await session.get(MapsCensusRun, place.run_id)
            path_keywords = path_keywords_from_country_profile(
                dict(run.country_profile or {}) if run is not None else None
            )
            place.enrichment_pipeline_state = MapsEnrichmentPipelineState.WEBSITE_RESOLUTION_PENDING.value
            place.enrichment_status = MapsPlaceEnrichmentStatus.RUNNING.value
            place.enrichment_attempts = (place.enrichment_attempts or 0) + 1
            place.enrichment_error_message = None
            await session.commit()

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
            if place.official_website and place.website_relationship in CRAWLABLE_RELATIONSHIPS:
                place.enrichment_pipeline_state = MapsEnrichmentPipelineState.CRAWL_PENDING.value
            else:
                place.enrichment_pipeline_state = MapsEnrichmentPipelineState.WEBSITE_NOT_FOUND.value
            await session.commit()

        crawl_excerpt: str | None = None
        if place.official_website and place.website_relationship in CRAWLABLE_RELATIONSHIPS:
            async with session_factory() as session:
                place = await session.get(MapsPlace, place_id)
                if place is None:
                    return 0
                try:
                    outcome = await maps_website_crawl_service.crawl_website(
                        session,
                        website_url=place.official_website,
                        path_keywords=path_keywords,
                    )
                    tracker.add_crawl_request()
                    place.enrichment_pages_crawled = outcome.page_urls or None
                    crawl_excerpt = outcome.combined_excerpt(
                        max_chars=get_settings().maps_crawl_max_total_context_chars
                    ) or None
                    place.enrichment_pipeline_state = MapsEnrichmentPipelineState.CRAWL_COMPLETED.value
                except MapsWebsiteCrawlError:
                    place.enrichment_pipeline_state = MapsEnrichmentPipelineState.CRAWL_FAILED.value
                await session.commit()

        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place is None:
                return 0
            payload = maps_place_enrichment_service._facility_payload(
                place, website_crawl_excerpt=crawl_excerpt
            )
            place.enrichment_pipeline_state = (
                MapsEnrichmentPipelineState.PRIMARY_EXTRACTION_PENDING.value
            )
            await session.commit()

        primary_confidence: float | None = None
        try:
            primary_result = await primary_provider.extract_one(
                country_code=country_code,
                country_name=country_name,
                facility_payload=payload,
                crawl_excerpt=crawl_excerpt,
            )
            tracker.add_primary_extraction_call()
            mapped = map_primary_to_enrichment_fields(primary_result.output)
            primary_confidence = mapped.get("classification_confidence")
            result = MapsPlaceEnrichmentResult.model_validate(
                {"place_id": place_id, **mapped}
            )
            _enforce_field_evidence(result)
            async with session_factory() as session:
                place = await session.get(MapsPlace, place_id)
                if place is None:
                    return 0
                _apply_structured_fields(place, result)
                place.enrichment_extraction_source = "primary"
                place.enrichment_pipeline_state = (
                    MapsEnrichmentPipelineState.PRIMARY_EXTRACTION_COMPLETED.value
                )
                await session.commit()
        except (ValidationError, Exception) as exc:  # noqa: BLE001
            logger.warning("maps_primary_extraction_failed place=%s error=%s", place_id, exc)
            async with session_factory() as session:
                place = await session.get(MapsPlace, place_id)
                if place is not None:
                    place.enrichment_pipeline_state = (
                        MapsEnrichmentPipelineState.PRIMARY_EXTRACTION_FAILED.value
                    )
                    place.enrichment_error_message = str(exc)[:500]
                    await session.commit()

        reason: str | None = None
        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place is None:
                return 0
            place.lifecycle_status = _derive_lifecycle_status(place)
            place.client_eligibility = compute_client_eligibility(place)
            place.is_relevant = derive_is_relevant(place.lifecycle_status)
            place.verification_verdict = derive_legacy_verification_verdict(place.lifecycle_status)
            place.verification_reason = _derive_verification_reason(place)
            reason = sonar_fallback_reason(
                has_website=bool(place.official_website),
                primary_confidence=primary_confidence,
                lifecycle_status=place.lifecycle_status,
                client_eligibility=place.client_eligibility,
                facility_type=place.facility_type,
                ownership_status=place.ownership_status,
                addiction_focus_confirmed=place.addiction_focus_confirmed,
            )
            current_eligibility = place.client_eligibility
            await session.commit()

        use_sonar = (
            reason is not None
            and sonar_budget.can_call()
            and current_eligibility == MapsClientEligibility.REVIEW.value
        )
        if use_sonar:
            sonar_budget.calls_used += 1
            try:
                sonar_result = await fetch_sonar_fallback_one(
                    country_code=country_code,
                    country_name=country_name,
                    payload=payload,
                    parse_stats=parse_stats,
                    sonar_stats=sonar_stats,
                )
                tracker.add_enrichment_call()
                tracker.add_sonar_fallback_call()
                _enforce_field_evidence(sonar_result)
                async with session_factory() as session:
                    place = await session.get(MapsPlace, place_id)
                    if place is None:
                        return 0
                    _apply_structured_fields(place, sonar_result)
                    addictions = _normalize_addictions(sonar_result.addictions_treated)
                    languages = _normalize_languages(sonar_result.languages_spoken)
                    place.addictions_treated = addictions
                    place.languages_spoken = languages
                    place.enrichment_extraction_source = "sonar"
                    place.enrichment_pipeline_state = (
                        MapsEnrichmentPipelineState.SONAR_FALLBACK_COMPLETED.value
                    )
                    place.lifecycle_status = _derive_lifecycle_status(place)
                    place.client_eligibility = compute_client_eligibility(place)
                    place.is_relevant = derive_is_relevant(place.lifecycle_status)
                    place.verification_verdict = derive_legacy_verification_verdict(
                        place.lifecycle_status
                    )
                    place.verification_reason = _derive_verification_reason(place)
                    place.verification_source_url = _derive_verification_source_url(sonar_result)
                    place.relevance_reason = _derive_relevance_reason(place)
                    await session.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning("maps_sonar_fallback_failed place=%s error=%s", place_id, exc)
                async with session_factory() as session:
                    place = await session.get(MapsPlace, place_id)
                    if place is not None:
                        place.enrichment_pipeline_state = (
                            MapsEnrichmentPipelineState.SONAR_FALLBACK_FAILED.value
                        )
                        place.enrichment_status = MapsPlaceEnrichmentStatus.FAILED.value
                        place.enrichment_error_message = str(exc)[:500]
                        place.enrichment_completed_at = datetime.now(UTC)
                        await session.commit()
                return 0
        elif reason is not None and not sonar_budget.can_call():
            sonar_stats.budget_exhausted = True

        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place is None:
                return 0
            if place.enrichment_status != MapsPlaceEnrichmentStatus.FAILED.value:
                if place.client_eligibility == MapsClientEligibility.REVIEW.value:
                    place.enrichment_pipeline_state = MapsEnrichmentPipelineState.NEEDS_REVIEW.value
                else:
                    place.enrichment_pipeline_state = MapsEnrichmentPipelineState.FINALIZED.value
                place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
                place.enrichment_completed_at = datetime.now(UTC)
                place.enrichment_error_message = None
            await session.commit()

        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place is None:
                return 0
            return 1 if place.client_eligibility == MapsClientEligibility.ELIGIBLE.value else 0

    async def _finalize_skipped_place(
        self,
        session: AsyncSession,
        place: MapsPlace,
        skip_reason: str | None,
    ) -> None:
        place.enrichment_pipeline_state = MapsEnrichmentPipelineState.FINALIZED.value
        place.enrichment_extraction_source = "deterministic_skip"
        place.enrichment_status = MapsPlaceEnrichmentStatus.SKIPPED.value
        place.enrichment_completed_at = datetime.now(UTC)
        place.enrichment_error_message = None
        if skip_reason:
            evidence = dict(place.classification_evidence or {})
            evidence["enrichment_skip_reason"] = skip_reason
            place.classification_evidence = evidence
        place.client_eligibility = compute_client_eligibility(place)
        await session.flush()

    async def _refresh_run_counters(self, session_factory, *, run_id: str) -> None:
        async with session_factory() as session:
            run = await session.get(MapsCensusRun, run_id)
            if run is None:
                return
            relevant = (
                await session.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.is_relevant.is_(True),
                    )
                )
            ).scalars().all()
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
            )
            run.enrichment_refresh_attempts = (run.enrichment_refresh_attempts or 0) + 1
            run.enrichment_refresh_completed_at = datetime.now(UTC)
            if run.completed_at is not None:
                run.status = MapsCensusStatus.COMPLETED
            await session.commit()

    async def reset_for_recovery(self, session_factory, *, run_id: str) -> dict[str, int]:
        """Reset failed/pending enrichment states without touching discovery data."""
        async with session_factory() as session:
            places = (
                await session.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.enrichment_status.in_(
                            [
                                MapsPlaceEnrichmentStatus.PENDING.value,
                                MapsPlaceEnrichmentStatus.FAILED.value,
                            ]
                        ),
                    )
                )
            ).scalars().all()
            reset = 0
            for place in places:
                if not should_select_for_expensive_pipeline(place):
                    continue
                place.enrichment_status = MapsPlaceEnrichmentStatus.PENDING.value
                place.enrichment_error_message = None
                place.enrichment_completed_at = None
                place.enrichment_pipeline_state = default_pipeline_state()
                reset += 1
            run = await session.get(MapsCensusRun, run_id)
            if run is not None:
                state = dict(run.processing_state or {})
                state.pop("enrichment_cursor", None)
                state["enrichment_paused"] = False
                state["enrichment_pipeline_paused"] = False
                run.processing_state = state
                run.enrichment_refresh_completed_at = None
                if run.completed_at is not None:
                    run.status = MapsCensusStatus.COMPLETED
            await session.commit()
            return {"reset_places": reset}


maps_enrichment_cascade_service = MapsEnrichmentCascadeService()

__all__ = ["MapsEnrichmentCascadeService", "maps_enrichment_cascade_service"]
