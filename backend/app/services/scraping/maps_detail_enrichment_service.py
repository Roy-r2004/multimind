"""Phase 2: enrich addictions, languages, and treatment price for candidates."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select

from app.core.config import get_settings
from app.db.models import MapsCensusRun, MapsPlace, MapsPlaceEnrichmentStatus
from app.llm.catalog import get_model
from app.llm.providers import get_provider_registry
from app.services.scraping.maps_batch_claim import claim_batch_place_ids
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


# Free/public webmail providers a small NGO's contact email may legitimately
# use without matching its own website's domain.
_PUBLIC_WEBMAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "yahoo.com",
        "icloud.com",
        "protonmail.com",
        "gmx.at",
        "gmx.net",
        "gmx.de",
        "web.de",
        "t-online.de",
        "aon.at",
        "chello.at",
    }
)


def _domain_of(value: str) -> str:
    host = urlsplit(value if "//" in value else f"//{value}").hostname or ""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def _email_belongs_to_facility(email: str, place: MapsPlace) -> bool:
    """Guard against a live-search result attributing another facility's email.

    The live-search model can occasionally return an email from a different
    (unrelated) organization's page it found while researching this facility.
    Only trust an email whose domain matches the facility's own official
    website (allowing a subdomain relationship either way) or a well-known
    public webmail provider — otherwise discard it rather than risk
    misattributing contact info.
    """
    at_index = email.rfind("@")
    if at_index <= 0:
        return False
    email_domain = email[at_index + 1 :].strip().lower()
    if not email_domain:
        return False
    if email_domain in _PUBLIC_WEBMAIL_DOMAINS:
        return True
    website = (place.official_website or place.raw_website or "").strip()
    if not website:
        return False
    site_domain = _domain_of(website)
    if not site_domain:
        return False
    return (
        email_domain == site_domain
        or email_domain.endswith(f".{site_domain}")
        or site_domain.endswith(f".{email_domain}")
    )


def build_detail_enrichment_query(run_id: str, *, max_attempts: int | None = None):
    settings = get_settings()
    attempts_cap = max_attempts if max_attempts is not None else settings.maps_place_max_attempts
    return (
        select(MapsPlace)
        .where(
            MapsPlace.run_id == run_id,
            MapsPlace.is_relevant.is_(True),
            MapsPlace.enrichment_status.in_(
                [MapsPlaceEnrichmentStatus.PENDING.value, MapsPlaceEnrichmentStatus.FAILED.value]
            ),
            MapsPlace.enrichment_pipeline_state.in_(
                [
                    MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value,
                    MapsEnrichmentPipelineState.DETAIL_ENRICHMENT_FAILED.value,
                ]
            ),
            MapsPlace.enrichment_attempts < max(1, attempts_cap),
        )
        .order_by(MapsPlace.id)
    )


class MapsDetailEnrichmentService:
    async def enrich_one_batch(
        self,
        session_factory,
        *,
        run_id: str,
        country_code: str,
        country_name: str,
        tracker: MapsQuotaTracker,
        parse_stats: EnrichmentParseStats,
        batch_size: int | None = None,
    ) -> dict[str, Any]:
        """Atomically claim and enrich exactly one small batch, then return.

        A Phase 2 failure never changes the Phase 1 classification already
        committed on the place — only detail-enrichment fields/status change.
        """
        settings = get_settings()
        size = max(1, batch_size or settings.maps_detail_enrichment_batch_size)

        claimed_ids = await claim_batch_place_ids(
            session_factory, build_detail_enrichment_query(run_id), batch_size=size
        )
        if not claimed_ids:
            async with session_factory() as session:
                pending_left = (
                    await session.execute(
                        select(func.count()).where(
                            MapsPlace.run_id == run_id,
                            MapsPlace.enrichment_pipeline_state
                            == MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value,
                            MapsPlace.enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value,
                        ).select_from(MapsPlace)
                    )
                ).scalar_one()
            return {"processed": 0, "has_more": False, "pending_count": int(pending_left or 0)}

        async with session_factory() as session:
            places = (
                await session.execute(select(MapsPlace).where(MapsPlace.id.in_(claimed_ids)))
            ).scalars().all()

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

        enriched = 0
        # Group into small LLM-call chunks (default 1 facility/call) so one
        # facility's provider failure cannot take the rest of the claimed
        # batch down with it, and Sonar failures stay bounded per-call.
        llm_chunk_size = max(1, settings.maps_census_enrichment_batch_size)
        for offset in range(0, len(candidates), llm_chunk_size):
            chunk = candidates[offset : offset + llm_chunk_size]
            timeout = max(10.0, settings.maps_detail_place_timeout_seconds) * len(chunk)
            try:
                enriched += await asyncio.wait_for(
                    self._enrich_batch(
                        session_factory,
                        places=chunk,
                        country_code=country_code,
                        country_name=country_name,
                        parse_stats=parse_stats,
                        tracker=tracker,
                    ),
                    timeout=timeout,
                )
            except Exception as exc:  # noqa: BLE001 - keep parent job alive
                logger.exception("maps_detail_enrichment_batch_failed error=%s", exc)
                await self._finalize_detail_failure(session_factory, places=chunk, error=str(exc))

        async with session_factory() as session:
            pending_left = (
                await session.execute(
                    select(func.count()).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.enrichment_pipeline_state
                        == MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value,
                        MapsPlace.enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value,
                    ).select_from(MapsPlace)
                )
            ).scalar_one()

        return {
            "processed": enriched,
            "claimed": len(claimed_ids),
            "has_more": True,
            "pending_count": int(pending_left or 0),
            "last_processed_place_id": claimed_ids[-1],
        }

    async def _finalize_detail_failure(
        self, session_factory, *, places: list[MapsPlace] | list[str], error: str
    ) -> None:
        """Mark detail enrichment failed without touching Phase 1 classification.

        Preserves lifecycle_status/client_eligibility/addictions already set by
        classification; the place remains exportable on Phase 1 alone.
        """
        settings = get_settings()
        max_attempts = max(1, settings.maps_place_max_attempts)
        place_ids = [p.id if isinstance(p, MapsPlace) else p for p in places]
        async with session_factory() as session:
            for pid in place_ids:
                fresh = await session.get(MapsPlace, pid)
                if fresh is None:
                    continue
                attempts_exhausted = (fresh.enrichment_attempts or 0) >= max_attempts
                fresh.enrichment_pipeline_state = MapsEnrichmentPipelineState.DETAIL_ENRICHMENT_FAILED.value
                fresh.enrichment_error_message = f"detail_enrichment_error: {error}"[:500]
                if attempts_exhausted:
                    # Optional fields must never block delivery — finalize the
                    # place as exportable-without-details, not stuck retrying.
                    fresh.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
                    fresh.enrichment_completed_at = datetime.now(UTC)
                else:
                    fresh.enrichment_status = MapsPlaceEnrichmentStatus.FAILED.value
            await session.commit()

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
        """Run detail enrichment to completion in-process (dev/inline/tests only).

        Production traffic uses ``enrich_one_batch`` from short-lived, self
        re-enqueuing ARQ jobs so a worker crash cannot lose hundreds of places.
        """
        settings = get_settings()
        batch_size = max(1, settings.maps_detail_enrichment_batch_size)
        max_calls = max(1, settings.maps_census_enrichment_max_calls_per_run)
        calls = 0
        enriched = 0
        paused = False
        await persist_enrichment_progress(
            session_factory,
            run_id=run_id,
            phase="detail_enrichment",
            enrichment_status="running",
            detail_enriched_count=0,
        )

        while True:
            if calls >= max_calls:
                paused = True
                break

            batch = await self.enrich_one_batch(
                session_factory,
                run_id=run_id,
                country_code=country_code,
                country_name=country_name,
                tracker=tracker,
                parse_stats=parse_stats,
                batch_size=batch_size,
            )
            if not batch["has_more"]:
                break

            enriched += batch["processed"]
            calls += batch.get("claimed", 0)

            await persist_enrichment_progress(
                session_factory,
                run_id=run_id,
                phase="detail_enrichment",
                enrichment_status="running",
                last_processed_place_id=batch.get("last_processed_place_id"),
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
            await self._finalize_detail_failure(session_factory, places=place_ids, error=str(exc))
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
                    if result.treatment_price and str(result.treatment_price).strip():
                        place.treatment_price = str(result.treatment_price).strip()[:512]
                    candidate_email = str(result.contact_email or "").strip()
                    if candidate_email and _email_belongs_to_facility(candidate_email, place):
                        place.contact_email = candidate_email[:320]
                    candidate_phone = str(result.contact_phone or "").strip()
                    if candidate_phone:
                        place.international_phone_number = candidate_phone[:64]
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
                "You have live web search. Return addictions, languages, treatment_price, a "
                "contact email, and a contact phone number for each facility. Return strict "
                "JSON only."
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
