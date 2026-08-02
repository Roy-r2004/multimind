"""Phase 2: enrich addictions and languages for classification candidates only."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import MapsCensusRun, MapsPlace, MapsPlaceEnrichmentStatus
from app.llm.catalog import get_model
from app.llm.providers import get_provider_registry
from app.services.scraping.maps_enrichment_fetch import cap_payload_excerpt
from app.services.scraping.maps_enrichment_processing_state import MapsEnrichmentPipelineState
from app.services.scraping.maps_enrichment_progress import persist_enrichment_progress
from app.services.scraping.maps_enrichment_response_parser import EnrichmentParseStats
from app.services.scraping.maps_enrichment_selection import is_detail_enrichment_candidate
from app.services.scraping.maps_place_enrichment_service import (
    ADDICTION_TAXONOMY,
    maps_place_enrichment_service,
)
from app.services.scraping.maps_quota_tracker import MapsQuotaTracker
from app.services.scraping.maps_website_crawl_service import (
    MapsWebsiteCrawlError,
    maps_website_crawl_service,
    path_keywords_from_country_profile,
)

logger = logging.getLogger(__name__)


def build_detail_enrichment_query(run_id: str):
    return (
        select(MapsPlace)
        .where(
            MapsPlace.run_id == run_id,
            MapsPlace.is_relevant.is_(True),
            MapsPlace.enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value,
            MapsPlace.enrichment_pipeline_state
            == MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value,
        )
        .order_by(MapsPlace.id)
    )


class MapsDetailEnrichmentService:
    async def enrich_run(
        self,
        session_factory,
        *,
        run_id: str,
        country_code: str,
        country_name: str,
        tracker: MapsQuotaTracker,
        parse_stats: EnrichmentParseStats,
    ) -> dict[str, Any]:
        settings = get_settings()
        batch_size = max(1, settings.maps_census_enrichment_batch_size)
        processing_batch_size = max(1, settings.maps_census_enrichment_processing_batch_size)
        max_calls = max(1, settings.maps_census_enrichment_max_calls_per_run)
        calls = 0
        enriched = 0
        paused = False
        # Re-query the live candidate set each batch (no forward-only cursor).
        await persist_enrichment_progress(
            session_factory,
            run_id=run_id,
            phase="detail_enrichment",
            enrichment_status="running",
            detail_enriched_count=0,
        )

        while True:
            async with session_factory() as session:
                places = (
                    await session.execute(
                        build_detail_enrichment_query(run_id).limit(processing_batch_size)
                    )
                ).scalars().all()
                if not places:
                    break

            candidates = [p for p in places if is_detail_enrichment_candidate(p)]
            non_candidates = [p for p in places if p not in candidates]
            if non_candidates:
                async with session_factory() as session:
                    for place in non_candidates:
                        fresh = await session.get(MapsPlace, place.id)
                        if fresh is None:
                            continue
                        fresh.enrichment_pipeline_state = (
                            MapsEnrichmentPipelineState.DETAIL_NOT_REQUIRED.value
                        )
                        fresh.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
                        fresh.enrichment_completed_at = datetime.now(UTC)
                    await session.commit()

            if not candidates:
                # Avoid spinning forever on rows that just became non-candidates.
                if not non_candidates:
                    break
                continue

            if calls >= max_calls:
                paused = True
                break

            for offset in range(0, len(candidates), batch_size):
                chunk = candidates[offset : offset + batch_size]
                try:
                    enriched += await self._enrich_batch(
                        session_factory,
                        places=chunk,
                        country_code=country_code,
                        country_name=country_name,
                        parse_stats=parse_stats,
                        tracker=tracker,
                    )
                except Exception as exc:  # noqa: BLE001 - keep parent job alive
                    logger.exception(
                        "maps_detail_enrichment_batch_failed error=%s",
                        exc,
                    )
                    async with session_factory() as session:
                        for place in chunk:
                            fresh = await session.get(MapsPlace, place.id)
                            if fresh is None:
                                continue
                            # Keep Phase 1 classification; only mark detail failed.
                            fresh.enrichment_status = MapsPlaceEnrichmentStatus.FAILED.value
                            fresh.enrichment_pipeline_state = (
                                MapsEnrichmentPipelineState.DETAIL_ENRICHMENT_FAILED.value
                            )
                            fresh.enrichment_error_message = f"detail_enrichment_error: {exc}"[:500]
                        await session.commit()
                calls += len(chunk)

            await persist_enrichment_progress(
                session_factory,
                run_id=run_id,
                phase="detail_enrichment",
                enrichment_status="running",
                last_processed_place_id=candidates[-1].id if candidates else None,
                detail_enriched_count=enriched,
                processed_count=enriched,
                paused=paused,
            )

        return {"enriched": enriched, "paused": paused, "cursor": None}

    async def _enrich_batch(
        self,
        session_factory,
        *,
        places: list[MapsPlace],
        country_code: str,
        country_name: str,
        parse_stats: EnrichmentParseStats,
        tracker: MapsQuotaTracker,
    ) -> int:
        from app.services.scraping.maps_place_enrichment_service import (
            _normalize_addictions,
            _normalize_languages,
        )

        if not places:
            return 0

        async with session_factory() as session:
            run = await session.get(MapsCensusRun, places[0].run_id)
            path_keywords = path_keywords_from_country_profile(
                dict(run.country_profile or {}) if run is not None else None
            )
            crawl_excerpts: dict[str, str | None] = {}
            settings = get_settings()
            for place in places:
                fresh = await session.get(MapsPlace, place.id)
                if fresh is None:
                    continue
                fresh.enrichment_status = MapsPlaceEnrichmentStatus.RUNNING.value
                fresh.enrichment_attempts = (fresh.enrichment_attempts or 0) + 1
                fresh.enrichment_error_message = None
                fresh.enrichment_pipeline_state = (
                    MapsEnrichmentPipelineState.DETAIL_ENRICHMENT_PENDING.value
                )
                website = (fresh.official_website or fresh.raw_website or "").strip()
                if website and settings.maps_census_website_crawl_enabled:
                    crawl_budget = max(10.0, settings.maps_classification_crawl_timeout_seconds)
                    try:
                        outcome = await asyncio.wait_for(
                            maps_website_crawl_service.crawl_website(
                                session,
                                website_url=website,
                                path_keywords=path_keywords,
                            ),
                            timeout=crawl_budget,
                        )
                        fresh.enrichment_pages_crawled = outcome.page_urls or None
                        crawl_excerpts[fresh.id] = (
                            outcome.combined_excerpt(
                                max_chars=settings.maps_census_website_crawl_max_excerpt_chars
                            )
                            or None
                        )
                    except (MapsWebsiteCrawlError, TimeoutError):
                        crawl_excerpts[fresh.id] = None
                else:
                    crawl_excerpts[fresh.id] = None
            await session.commit()

        payloads = []
        place_ids = []
        async with session_factory() as session:
            for place in places:
                fresh = await session.get(MapsPlace, place.id)
                if fresh is None:
                    continue
                place_ids.append(fresh.id)
                payloads.append(
                    cap_payload_excerpt(
                        maps_place_enrichment_service._facility_payload(
                            fresh,
                            website_crawl_excerpt=crawl_excerpts.get(fresh.id),
                        ),
                        max_chars=get_settings().maps_census_enrichment_max_crawl_excerpt_chars,
                    )
                )

        if not payloads:
            return 0

        results_by_id: dict[str, Any] = {}
        try:
            results = await self._fetch_detail_batch(
                payloads,
                country_code=country_code,
                country_name=country_name,
                parse_stats=parse_stats,
            )
            tracker.add_enrichment_call()
            for result in results:
                if result.place_id:
                    results_by_id[result.place_id] = result
        except Exception as exc:  # noqa: BLE001
            logger.warning("maps_detail_enrichment_batch_failed error=%s", exc)
            async with session_factory() as session:
                for pid in place_ids:
                    place = await session.get(MapsPlace, pid)
                    if place is None:
                        continue
                    place.enrichment_status = MapsPlaceEnrichmentStatus.FAILED.value
                    place.enrichment_pipeline_state = (
                        MapsEnrichmentPipelineState.DETAIL_ENRICHMENT_FAILED.value
                    )
                    place.enrichment_error_message = str(exc)[:500]
                    place.enrichment_completed_at = datetime.now(UTC)
                await session.commit()
            return 0

        completed = 0
        async with session_factory() as session:
            for pid in place_ids:
                place = await session.get(MapsPlace, pid)
                if place is None:
                    continue
                result = results_by_id.get(pid)
                if result is not None:
                    addictions = _normalize_addictions(result.addictions_treated)
                    languages = _normalize_languages(result.languages_spoken)
                    place.addictions_treated = addictions or None
                    place.languages_spoken = languages or None
                    if place.enrichment_extraction_source in {
                        None,
                        "structured_classification",
                        "sonar_classification",
                        "deterministic_rules",
                    }:
                        place.enrichment_extraction_source = "detail_enrichment"
                place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
                place.enrichment_pipeline_state = (
                    MapsEnrichmentPipelineState.DETAIL_ENRICHMENT_COMPLETED.value
                )
                place.enrichment_completed_at = datetime.now(UTC)
                place.enrichment_error_message = None
                completed += 1
            await session.commit()
        return completed

    async def _fetch_detail_batch(
        self,
        payloads: list[dict[str, Any]],
        *,
        country_code: str,
        country_name: str,
        parse_stats: EnrichmentParseStats,
    ):
        from app.llm.prompt_engine import get_prompt_engine
        from app.services.scraping.maps_enrichment_fetch import EnrichmentFetchError
        from app.services.scraping.maps_enrichment_response_parser import (
            parse_and_validate_enrichment_response,
            record_parse_failure,
        )

        settings = get_settings()
        model = get_model(settings.maps_census_enrichment_model)
        provider = get_provider_registry().get_provider(model.provider)
        prompt = get_prompt_engine().render(
            "scraping/maps_detail_enricher.j2",
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            addiction_taxonomy_json=json.dumps(list(ADDICTION_TAXONOMY), ensure_ascii=True),
            facilities_json=json.dumps(payloads, ensure_ascii=False),
        )
        response = await provider.complete(
            system=(
                "You have live web search. Return addictions and languages only for each facility. "
                "Return strict JSON only."
            ),
            user=prompt,
            model=model.provider_model,
            max_tokens=4096,
        )
        try:
            batch = parse_and_validate_enrichment_response(response.text or "")
        except Exception as exc:
            record_parse_failure(parse_stats, error=exc, raw_text=response.text or "", attempt="detail")
            raise EnrichmentFetchError(str(exc)) from exc
        return batch.results


maps_detail_enrichment_service = MapsDetailEnrichmentService()

__all__ = [
    "MapsDetailEnrichmentService",
    "build_detail_enrichment_query",
    "maps_detail_enrichment_service",
]
