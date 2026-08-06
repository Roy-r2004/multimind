"""Phase 1: classify all relevant Maps places (rules → structured AI → Sonar classify)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, or_, select

from app.core.config import get_settings
from app.db.models import (
    MapsCensusRun,
    MapsClientEligibility,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
)
from app.services.scraping.maps_classification_rules import (
    apply_deterministic_classification,
)
from app.services.scraping.maps_batch_claim import claim_batch_place_ids
from app.services.scraping.maps_eligibility import (
    compute_client_eligibility,
    derive_is_relevant,
    derive_legacy_verification_verdict,
    normalize_lifecycle_eligibility_consistency,
)
from app.services.scraping.maps_enrichment_processing_state import MapsEnrichmentPipelineState
from app.services.scraping.maps_enrichment_progress import persist_enrichment_progress
from app.services.scraping.maps_enrichment_response_parser import EnrichmentParseStats
from app.services.scraping.maps_enrichment_selection import is_detail_enrichment_candidate
from app.services.scraping.maps_keep_drop_service import KEEP
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


def _domain_of(url: str | None) -> str | None:
    if not url:
        return None
    from urllib.parse import urlsplit

    host = urlsplit(url.strip()).hostname
    return host.lower() if host else None


async def _persist_crawl_skip_domain(session_factory, *, run_id: str, domain: str) -> None:
    """Remember a domain that hung/failed crawl so later recoveries skip it."""
    host = (domain or "").strip().lower()
    if not host or not run_id:
        return
    async with session_factory() as session:
        run = await session.get(MapsCensusRun, run_id)
        if run is None:
            return
        state = dict(run.processing_state or {})
        skipped = {
            str(item).strip().lower()
            for item in (state.get("crawl_skip_domains") or [])
            if str(item).strip()
        }
        if host in skipped:
            return
        skipped.add(host)
        state["crawl_skip_domains"] = sorted(skipped)
        run.processing_state = state
        await session.commit()


def build_classification_query(run_id: str):
    return (
        select(MapsPlace)
        .where(
            MapsPlace.run_id == run_id,
            MapsPlace.is_relevant.is_(True),
            # Phase 2 must never touch a place the strict keep/drop gate hasn't
            # confirmed yet — without this, a place discovered as needs_review
            # could get reclassified (and potentially demoted) before
            # run_keep_drop_pass ever judges it, then get silently swept into
            # stamp_non_candidate_drops's bulk-exclude as if it had never been
            # a candidate at all. client_eligibility == ELIGIBLE is included
            # alongside keep_drop_decision == KEEP so a manual admin
            # mark_eligible override (which never touches keep_drop_decision)
            # still reaches Phase 2 — compute_client_eligibility only ever
            # returns ELIGIBLE from a positive structured determination, never
            # as a default for an undecided place, so this can't reopen the
            # premature-processing gap.
            or_(
                MapsPlace.keep_drop_decision == KEEP,
                MapsPlace.client_eligibility == MapsClientEligibility.ELIGIBLE.value,
            ),
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
                        # Retryable classification failures must re-enter the
                        # same claim path without a destructive Recover.
                        MapsEnrichmentPipelineState.CLASSIFICATION_FAILED_RETRYABLE.value,
                    ]
                ),
            ),
        )
        .order_by(MapsPlace.id)
    )


class MapsClassificationService:
    async def classify_one_batch(
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
        provider=None,
        batch_size: int | None = None,
        failed_crawl_domains: set[str] | None = None,
    ) -> dict[str, Any]:
        """Atomically claim and classify exactly one small batch, then return.

        Designed to be called from a short-lived job: it never loops internally,
        so a worker crash mid-batch loses at most ``batch_size`` in-flight places
        (already-committed places from earlier batches are untouched).
        """
        settings = get_settings()
        provider = provider or create_structured_classification_provider()
        size = max(1, batch_size or settings.maps_classification_batch_size)

        if failed_crawl_domains is None:
            failed_crawl_domains = set()
            async with session_factory() as session:
                run = await session.get(MapsCensusRun, run_id)
                if run is not None:
                    for item in (run.processing_state or {}).get("crawl_skip_domains") or []:
                        host = str(item).strip().lower()
                        if host:
                            failed_crawl_domains.add(host)

        place_ids = await claim_batch_place_ids(
            session_factory, build_classification_query(run_id), batch_size=size
        )
        if not place_ids:
            async with session_factory() as session:
                pending_left = (
                    await session.execute(
                        select(func.count()).where(
                            MapsPlace.run_id == run_id,
                            MapsPlace.enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value,
                        ).select_from(MapsPlace)
                    )
                ).scalar_one()
            return {"processed": 0, "has_more": False, "pending_count": int(pending_left or 0)}

        semaphore = asyncio.Semaphore(max(1, settings.maps_primary_extraction_concurrency))
        timeout = max(10.0, settings.maps_classification_place_timeout_seconds)

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
                            failed_crawl_domains=failed_crawl_domains,
                        ),
                        timeout=timeout,
                    )
                except TimeoutError:
                    logger.warning("maps_classification_timeout place=%s", place_id)
                    await self._finalize_place_failure(
                        session_factory,
                        place_id=place_id,
                        error=f"classification timeout after {int(timeout)}s",
                    )
                    return 0
                except Exception as exc:  # noqa: BLE001 - never kill the parent job
                    logger.exception(
                        "maps_classification_place_failed place=%s error=%s",
                        place_id,
                        exc,
                    )
                    await self._finalize_place_failure(
                        session_factory,
                        place_id=place_id,
                        error=f"classification_error: {exc}",
                    )
                    return 0

        # return_exceptions so one unexpected failure cannot cancel siblings
        # or abort the entire batch.
        results = await asyncio.gather(
            *(process_one(pid) for pid in place_ids),
            return_exceptions=True,
        )
        batch_ok = 0
        for place_id, result in zip(place_ids, results, strict=False):
            if isinstance(result, Exception):
                logger.exception(
                    "maps_classification_gather_error place=%s error=%s",
                    place_id,
                    result,
                )
                await self._finalize_place_failure(
                    session_factory, place_id=place_id, error=f"gather_error: {result}"
                )
                continue
            batch_ok += int(result or 0)

        async with session_factory() as session:
            pending_left = (
                await session.execute(
                    select(func.count()).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value,
                    ).select_from(MapsPlace)
                )
            ).scalar_one()

        return {
            "processed": batch_ok,
            "claimed": len(place_ids),
            "has_more": True,
            "pending_count": int(pending_left or 0),
            "last_processed_place_id": place_ids[-1],
        }

    async def _finalize_place_failure(self, session_factory, *, place_id: str, error: str) -> None:
        """Mark a place failed after an unexpected error, respecting max attempts."""
        settings = get_settings()
        max_attempts = max(1, settings.maps_place_max_attempts)
        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place is None:
                return
            attempts_exhausted = (place.enrichment_attempts or 0) >= max_attempts
            if attempts_exhausted:
                # Requirement: every relevant place must end in a valid bucket —
                # never loop forever on a place that keeps erroring.
                place.lifecycle_status = MapsLifecycleStatus.NEEDS_REVIEW.value
                place.client_eligibility = MapsClientEligibility.REVIEW.value
                place.enrichment_pipeline_state = MapsEnrichmentPipelineState.NEEDS_REVIEW.value
                place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
                place.enrichment_completed_at = datetime.now(UTC)
            else:
                place.enrichment_status = MapsPlaceEnrichmentStatus.FAILED.value
                place.enrichment_pipeline_state = (
                    MapsEnrichmentPipelineState.CLASSIFICATION_FAILED_RETRYABLE.value
                )
            place.enrichment_error_message = str(error)[:500]
            normalize_lifecycle_eligibility_consistency(place)
            await session.commit()

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
        """Run classification to completion in-process (dev/inline/tests only).

        Production traffic uses ``classify_one_batch`` from short-lived, self
        re-enqueuing ARQ jobs so a worker crash cannot lose hundreds of places.
        """
        settings = get_settings()
        provider = create_structured_classification_provider()
        batch_size = max(1, settings.maps_classification_batch_size)
        max_calls = max(1, settings.maps_primary_extraction_max_calls_per_run)
        calls = 0
        classified = 0
        paused = False
        failed_crawl_domains: set[str] = set()
        async with session_factory() as session:
            run = await session.get(MapsCensusRun, run_id)
            if run is not None:
                for item in (run.processing_state or {}).get("crawl_skip_domains") or []:
                    host = str(item).strip().lower()
                    if host:
                        failed_crawl_domains.add(host)

        await persist_enrichment_progress(
            session_factory,
            run_id=run_id,
            phase="classification",
            enrichment_status="running",
            processed_count=0,
        )

        while True:
            if calls >= max_calls:
                paused = True
                break

            batch = await self.classify_one_batch(
                session_factory,
                run_id=run_id,
                country_code=country_code,
                country_name=country_name,
                tracker=tracker,
                sonar_budget=sonar_budget,
                sonar_stats=sonar_stats,
                parse_stats=parse_stats,
                provider=provider,
                batch_size=batch_size,
                failed_crawl_domains=failed_crawl_domains,
            )
            if not batch["has_more"]:
                break

            classified += batch["processed"]
            calls += batch.get("claimed", 0)

            await persist_enrichment_progress(
                session_factory,
                run_id=run_id,
                phase="classification",
                enrichment_status="running",
                last_processed_place_id=batch.get("last_processed_place_id"),
                processed_count=classified,
                pending_count=batch["pending_count"],
                classified_count=classified,
                paused=paused,
            )

        return {"classified": classified, "paused": paused, "cursor": None}

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
        failed_crawl_domains: set[str] | None = None,
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
            crawl_domain = _domain_of(place.official_website)
            run_id_for_skip = place.run_id
            if (
                place.official_website
                and place.website_relationship in CRAWLABLE_RELATIONSHIPS
                and not (failed_crawl_domains and crawl_domain in failed_crawl_domains)
            ):
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
                    if failed_crawl_domains is not None and crawl_domain:
                        failed_crawl_domains.add(crawl_domain)
                        await _persist_crawl_skip_domain(
                            session_factory, run_id=run_id_for_skip, domain=crawl_domain
                        )
                    logger.info(
                        "maps_classification_crawl_skipped place=%s website=%s",
                        place_id,
                        place.official_website,
                    )
                await session.commit()
            elif failed_crawl_domains and crawl_domain in failed_crawl_domains:
                logger.info(
                    "maps_classification_crawl_circuit_open place=%s website=%s",
                    place_id,
                    place.official_website,
                )

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
            max_attempts = max(1, get_settings().maps_place_max_attempts)
            attempts_exhausted = (place.enrichment_attempts or 0) >= max_attempts
            if sonar_failed and still_unresolved:
                if attempts_exhausted:
                    # Delivery requirement: every relevant place must end in a
                    # valid bucket, never loop forever on a place that keeps
                    # failing classification. A provider failure must never
                    # silently become "excluded" — park it as needs_review.
                    place.lifecycle_status = MapsLifecycleStatus.NEEDS_REVIEW.value
                    place.client_eligibility = MapsClientEligibility.REVIEW.value
                    place.enrichment_pipeline_state = MapsEnrichmentPipelineState.NEEDS_REVIEW.value
                    place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
                    place.enrichment_completed_at = datetime.now(UTC)
                else:
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
            normalize_lifecycle_eligibility_consistency(place)
            await session.commit()
            return 1

maps_classification_service = MapsClassificationService()

__all__ = ["MapsClassificationService", "build_classification_query", "maps_classification_service"]
