"""Standalone Google Places Maps census — independent of the scraping pipeline.

Runs a country-scoped grid of Places text searches, classifies each result
with an LLM, and validates any Google-provided website with the exact same
strict rules the scraping pipeline uses for official websites. Results live in
their own tables (``MapsCensusRun`` / ``MapsCensusCell`` / ``MapsPlace``) so
they can later be compared against a Scraping Council execution for the same
country without coupling the two pipelines together.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import (
    MapsCensusCell,
    MapsCensusCellStatus,
    MapsCensusRegion,
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsContactStatus,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
    MapsRegionSaturationStatus,
)
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider, get_provider_registry
from app.schemas.api import (
    MapsCensusCellItem,
    MapsCensusRunDetail,
    MapsCensusRunSummary,
    MapsPlaceItem,
)
from app.services.scraping.countries import resolve_country
from app.services.scraping.facility_website_enrichment_service import (
    OfficialWebsiteCandidate,
    _homepage_url,
    _is_rejected_result,
    _is_weak_official_candidate,
    _safe_url,
    _tokens,
    build_official_website_query,
    select_official_website,
    website_needs_enrichment,
)
from app.services.scraping.maps_cell_runner import (
    fail_cell_for_retry,
    is_campaign_paused,
    is_run_cancelled,
    recover_stale_running_cells,
)
from app.services.scraping.maps_cell_subdivision import ChildCellSpec, subdivide_cell
from app.services.scraping.maps_country_profile_service import maps_country_profile_service
from app.services.scraping.maps_eligibility import derive_is_relevant, derive_legacy_verification_verdict
from app.services.scraping.maps_external_discovery import maps_external_discovery_coordinator
from app.services.scraping.maps_grid_planner import MapsGridPlanningError, maps_grid_planner
from app.services.scraping.maps_places_client import PlacesProviderError, create_places_client
from app.services.scraping.maps_quota_tracker import MapsQuotaTracker, merge_quota_metrics
from app.services.scraping.maps_saturation import (
    CellWindowResult,
    compute_window_metrics,
    decide_region_saturation,
    should_stop_campaign,
)
from app.services.scraping.pexels_client import create_pexels_client
from app.services.scraping.search_providers import create_search_provider
from app.services.scraping.search_providers.base import (
    SearchProviderError,
    SearchProviderRequest,
    SearchProviderResult,
)

logger = logging.getLogger(__name__)

CLASSIFICATION_BATCH_TIMEOUT_SECONDS = 90.0
WEBSITE_LLM_BATCH_TIMEOUT_SECONDS = 90.0
CLASSIFICATION_FALLBACK_REASONS = frozenset({"classification_failed", "missing_decision"})

# Constant seed budget for the first grid-planning pass — deliberately not a
# country-specific facility count. Adaptive expansion (see ``run_census``)
# grows productive regions beyond this up to the campaign ceiling.
MAPS_CENSUS_SEED_CELLS = 120
# Cap on how many extra cells one expansion round asks the planner for, so a
# single expansion pass can't consume the whole remaining campaign budget.
MAPS_CENSUS_EXPANSION_BATCH_CAP = 40

_TERMINAL_SATURATION_STATUSES = frozenset(
    {MapsRegionSaturationStatus.SATURATED.value, MapsRegionSaturationStatus.CAPPED.value}
)

CONFIDENCE_VERIFIED_MIN = 0.90
CONFIDENCE_EXPORT_MIN = 0.70
EXPORT_NOT_SPECIFIED = "Not Specified"
EXPORT_CONTACT_PRICING = "Contact for pricing"
EXPORT_ADDICTIONS_PLACEHOLDER = "Not Specified"

CSV_EXPORT_HEADERS = (
    "Facility Name",
    "Addictions Treated",
    "Location",
    "Languages Spoken",
    "Website",
    "Phone Number",
    "Treatment Price",
)

# Google returns an Open Location Code ("QW2G+35C") in formatted_address when a
# place has no street address at all — the signature of an unverified
# user-submitted pin rather than a real listing.
PLUS_CODE_RE = re.compile(r"^[23456789CFGHJMPQRVWX]{4,6}\+[23456789CFGHJMPQRVWX]{2,3}$", re.I)

# Bare category words that carry no facility identity. A name made up only of
# these (plus articles/prepositions) identifies nothing and cannot be exported.
GENERIC_NAME_WORDS = frozenset(
    {
        "addiction",
        "addictions",
        "center",
        "centre",
        "clinic",
        "clinique",
        "desintoxication",
        "detox",
        "detoxification",
        "hospital",
        "medical",
        "rehab",
        "rehabilitation",
        "traitement",
        "treatment",
        "الادمان",
        "المدمنين",
        "علاج",
        "مركز",
        "معالجة",
        "مصحة",
        "مكافحة",
    }
)
GENERIC_NAME_STOPWORDS = frozenset(
    {"a", "al", "and", "anti", "de", "des", "du", "et", "for", "la", "le", "les", "of", "the", "ال"}
)

# Facebook paths that belong to the platform rather than to a facility page.
FACEBOOK_NON_PAGE_SEGMENTS = frozenset(
    {
        "events",
        "groups",
        "hashtag",
        "help",
        "login",
        "marketplace",
        "pages",
        "people",
        "photo",
        "photo.php",
        "search",
        "sharer",
        "sharer.php",
        "story.php",
        "watch",
    }
)


class MapsRelevanceDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    place_id: str = Field(min_length=1, max_length=64)
    decision: str = Field(default="", max_length=40)
    is_relevant: bool = False
    reason: str = Field(default="", max_length=300)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class MapsRelevancePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decisions: list[MapsRelevanceDecision] = Field(default_factory=list)


class MapsWebsiteDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    place_id: str = Field(min_length=1, max_length=64)
    url: str | None = Field(default=None, max_length=512)
    reason: str = Field(default="", max_length=2000)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # Display name of the page behind ``url``. Required to prove identity when
    # ``url`` is a social page, whose host alone says nothing about the facility.
    page_name: str = Field(default="", max_length=300)


class MapsWebsitePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decisions: list[MapsWebsiteDecision] = Field(default_factory=list)


class MapsCensusService:
    async def create_run(
        self, db: AsyncSession, auth: AuthContext, country_code: str
    ) -> MapsCensusRunDetail:
        settings = get_settings()
        if not settings.google_places_enabled:
            raise ValidationError("Google Places Maps census is disabled.")
        country = resolve_country(country_code)
        run = MapsCensusRun(
            organization_id=auth.org_id,
            created_by=auth.user.id,
            country_code=country.code,
            country_name=country.name,
            status=MapsCensusStatus.QUEUED,
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()
        await self._enqueue(run_id)
        asyncio.create_task(self._fetch_hero_image(run_id, country.name))
        return await self.get_run(db, auth, run_id)

    async def _fetch_hero_image(self, run_id: str, country_name: str) -> None:
        """Best-effort Pexels lookup for a country hero photo — never raises,
        never blocks or fails the census run if Pexels is slow or unavailable.
        """
        try:
            client = create_pexels_client()
            url = await client.search_landscape(f"{country_name} landscape")
        except Exception:  # noqa: BLE001
            logger.warning("maps_census_hero_image_failed run_id=%s", run_id, exc_info=True)
            return
        if not url:
            return
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            run = await db.get(MapsCensusRun, run_id)
            if run is not None:
                run.hero_image_url = url
                await db.commit()

    async def list_runs(self, db: AsyncSession, auth: AuthContext) -> list[MapsCensusRunSummary]:
        rows = (
            await db.execute(
                select(MapsCensusRun)
                .where(MapsCensusRun.organization_id == auth.org_id)
                .order_by(MapsCensusRun.created_at.desc())
            )
        ).scalars().all()
        return [_run_summary(run) for run in rows]

    async def get_run(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCensusRunDetail:
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        summary = _run_summary(run)
        return MapsCensusRunDetail(**summary.model_dump())

    async def list_places(
        self,
        db: AsyncSession,
        auth: AuthContext,
        run_id: str,
        *,
        relevant_only: bool = False,
        with_website_only: bool = False,
        client_eligibility: str | None = None,
        lifecycle_status: str | None = None,
    ) -> list[MapsPlaceItem]:
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        query = select(MapsPlace).where(MapsPlace.run_id == run_id)
        if relevant_only:
            query = query.where(MapsPlace.is_relevant.is_(True))
        if with_website_only:
            query = query.where(MapsPlace.official_website.is_not(None))
        if client_eligibility is not None:
            query = query.where(MapsPlace.client_eligibility == client_eligibility.strip().lower())
        if lifecycle_status is not None:
            query = query.where(MapsPlace.lifecycle_status == lifecycle_status.strip().lower())
        rows = (await db.execute(query.order_by(MapsPlace.canonical_name))).scalars().all()
        return [_place_item(place) for place in rows]

    async def list_cells(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> list[MapsCensusCellItem]:
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        rows = (
            await db.execute(
                select(MapsCensusCell)
                .where(MapsCensusCell.run_id == run_id)
                .order_by(MapsCensusCell.region_name, MapsCensusCell.city_name, MapsCensusCell.query_text)
            )
        ).scalars().all()
        return [_cell_item(cell) for cell in rows]

    async def export_run_csv(
        self,
        db: AsyncSession,
        auth: AuthContext,
        run_id: str,
        *,
        tier: str = "all",
    ) -> tuple[str, str]:
        """Phase 1 CSV export — name, location, phone, website; addictions/languages/price TBD."""
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        normalized_tier = tier.strip().lower()
        if normalized_tier not in {"all", "verified", "flagged"}:
            raise ValidationError("tier must be all, verified, or flagged")

        rows = (
            await db.execute(
                select(MapsPlace)
                .where(MapsPlace.run_id == run_id)
                .order_by(MapsPlace.canonical_name)
            )
        ).scalars().all()
        eligible = [
            place
            for place in rows
            if _export_eligible(place) and _export_tier_matches(place, normalized_tier)
        ]

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_EXPORT_HEADERS)
        for place in eligible:
            writer.writerow(_export_csv_row(place, country_name=run.country_name))
        filename = f"{run.country_code.lower()}-maps-census-export.csv"
        return filename, buffer.getvalue()

    async def get_place_photo(
        self, db: AsyncSession, auth: AuthContext, run_id: str, place_id: str
    ) -> Path:
        """Return a local cache path for a facility photo, fetching it from Google
        Places once (and caching to disk) on first request. Never re-bills Google
        for a place we've already cached.
        """
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        place = await db.get(MapsPlace, place_id)
        if place is None or place.run_id != run_id:
            raise NotFoundError("Maps place", place_id)
        if not place.photo_reference:
            raise NotFoundError("Maps place photo", place_id)

        settings = get_settings()
        cache_dir = Path(settings.maps_census_photo_cache_dir)
        cache_path = cache_dir / f"{place.id}.jpg"
        if cache_path.exists():
            return cache_path

        if not settings.google_places_api_key:
            raise NotFoundError("Maps place photo", place_id)

        media_url = f"{settings.google_places_base_url}/{place.photo_reference}/media"
        try:
            async with httpx.AsyncClient(timeout=settings.google_places_timeout_seconds) as client:
                response = await client.get(
                    media_url,
                    params={"maxWidthPx": 480, "key": settings.google_places_api_key},
                )
        except httpx.HTTPError as exc:
            raise NotFoundError("Maps place photo", place_id) from exc

        if response.status_code != 200 or not response.content:
            raise NotFoundError("Maps place photo", place_id)

        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(response.content)
        return cache_path

    async def _enqueue(self, run_id: str) -> None:
        await self._enqueue_job(
            "run_maps_census_job", run_id, inline_runner=lambda: run_maps_census_job({}, run_id)
        )

    async def _enqueue_refresh(self, run_id: str) -> None:
        await self._enqueue_job(
            "refresh_maps_census_websites_job",
            run_id,
            inline_runner=lambda: refresh_maps_census_websites_job({}, run_id),
        )

    async def _enqueue_enrichment(self, run_id: str) -> None:
        await self._enqueue_job(
            "run_maps_census_enrichment_job",
            run_id,
            inline_runner=lambda: run_maps_census_enrichment_job({}, run_id),
        )

    async def _maybe_enqueue_enrichment(self, run_id: str) -> None:
        settings = get_settings()
        if settings.maps_census_enrichment_enabled and settings.maps_census_auto_enrichment_enabled:
            await self._enqueue_enrichment(run_id)

    async def request_enrichment(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCensusRunDetail:
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        if not _discovery_finished(run):
            raise ValidationError("Only a completed Maps census run can enrich facility websites.")
        if run.status != MapsCensusStatus.COMPLETED:
            # Website auto-refresh can leave status=running even after discovery finished.
            run.status = MapsCensusStatus.COMPLETED
        run.enrichment_refresh_completed_at = None
        run.heartbeat_at = datetime.now(UTC)
        await db.commit()
        await self._enqueue_enrichment(run_id)
        return await self.get_run(db, auth, run_id)

    async def _enqueue_job(self, job_name: str, run_id: str, *, inline_runner) -> None:
        settings = get_settings()
        inline = (
            settings.scraping_inline_execution
            if settings.scraping_inline_execution is not None
            else settings.environment == "development"
        )
        queued_on_redis = False
        if not inline:
            try:
                from urllib.parse import urlparse

                from arq import create_pool
                from arq.connections import RedisSettings

                parsed = urlparse(settings.redis_url)
                redis = await create_pool(
                    RedisSettings(
                        host=parsed.hostname or "localhost",
                        port=parsed.port or 6379,
                        database=int((parsed.path or "/0").lstrip("/") or "0"),
                        password=parsed.password,
                    )
                )
                await redis.enqueue_job(
                    job_name,
                    run_id,
                    _job_id=f"{job_name}:{run_id}:{uuid4().hex[:12]}",
                )
                await redis.close()
                queued_on_redis = True
            except Exception:
                logger.warning(
                    "maps_census_enqueue_redis_failed job=%s run_id=%s; falling back to inline",
                    job_name,
                    run_id,
                    exc_info=True,
                )
        if inline or not queued_on_redis:
            asyncio.create_task(inline_runner())

    async def delete_run(self, db: AsyncSession, auth: AuthContext, run_id: str) -> None:
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        await db.delete(run)
        await db.commit()

    async def request_website_refresh(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCensusRunDetail:
        """Re-run the missing-website search fallback for a completed run — used to
        backfill facilities that didn't get an official website on the original pass.
        """
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        if run.status != MapsCensusStatus.COMPLETED:
            raise ValidationError("Only a completed Maps census run can refresh missing websites.")
        run.status = MapsCensusStatus.RUNNING
        run.heartbeat_at = datetime.now(UTC)
        await db.commit()
        await self._enqueue_refresh(run_id)
        return await self.get_run(db, auth, run_id)

    async def run_website_refresh(self, db: AsyncSession | None, *, run_id: str) -> dict[str, int]:
        session_factory = self._session_factory(db)
        tracker = MapsQuotaTracker()
        await self._search_missing_websites(session_factory, run_id=run_id, tracker=tracker)
        await self._propagate_shared_websites(session_factory, run_id=run_id)
        await self._apply_missing_contact_filter(session_factory, run_id=run_id)
        await merge_quota_metrics(session_factory, run_id=run_id, tracker=tracker)
        async with session_factory() as final_db:
            run = await final_db.get(MapsCensusRun, run_id)
            summary = {"places_with_website": 0}
            if run is not None:
                relevant_places = (
                    await final_db.execute(
                        select(MapsPlace).where(
                            MapsPlace.run_id == run_id, MapsPlace.is_relevant.is_(True)
                        )
                    )
                ).scalars().all()
                run.places_classified_relevant = len(relevant_places)
                run.places_with_website = sum(1 for p in relevant_places if p.official_website)
                run.status = MapsCensusStatus.COMPLETED
                run.completed_at = datetime.now(UTC)
                run.heartbeat_at = datetime.now(UTC)
                run.website_refresh_attempts += 1
                run.website_refresh_completed_at = datetime.now(UTC)
                summary["places_with_website"] = run.places_with_website
                await final_db.commit()
        await self._maybe_enqueue_enrichment(run_id)
        return summary

    @staticmethod
    def _session_factory(db: AsyncSession | None):
        if db is not None:
            bind = db.bind
            if bind is None:
                bind = db.get_bind()
            return async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)
        from app.db.session import AsyncSessionLocal

        return AsyncSessionLocal

    async def run_census(self, db: AsyncSession | None, *, run_id: str) -> dict[str, int]:
        """Execute a Maps census run end to end with short-lived DB sessions.

        Never holds a connection across a Places/LLM network await — same
        discipline as ``facility_website_enrichment_service`` and
        ``facility_ai_cleanup_service``, to avoid starving the pool during
        long country runs.
        """
        session_factory = self._session_factory(db)
        settings = get_settings()

        async with session_factory() as start_db:
            run = await start_db.get(MapsCensusRun, run_id)
            if run is None:
                return {"error": 1}
            run.status = MapsCensusStatus.RUNNING
            run.started_at = datetime.now(UTC)
            run.heartbeat_at = datetime.now(UTC)
            await start_db.commit()
            country_code = run.country_code
            country_name = run.country_name

        try:
            await maps_country_profile_service.build_profile_for_run(db, run_id=run_id)
        except Exception:  # noqa: BLE001
            # Profiling is a best-effort enrichment stage — a bug here must never
            # take down the whole census. build_profile_for_run already records
            # its own failures on the run; this is just a last-resort guard.
            logger.warning(
                "maps_census_country_profile_stage_failed run_id=%s", run_id, exc_info=True
            )

        async with session_factory() as profile_db:
            run = await profile_db.get(MapsCensusRun, run_id)
            country_profile = run.country_profile if run is not None else None
            country_profile_status = run.country_profile_status if run is not None else None

        max_cells_per_campaign = (
            settings.maps_census_max_cells_per_campaign or settings.maps_census_max_cells_per_run
        )
        seed_max = min(MAPS_CENSUS_SEED_CELLS, max_cells_per_campaign)

        try:
            seed_cells = await maps_grid_planner.plan(
                country_code=country_code,
                country_name=country_name,
                max_cells=seed_max,
                country_profile=country_profile,
            )
        except MapsGridPlanningError as exc:
            async with session_factory() as fail_db:
                run = await fail_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.status = MapsCensusStatus.FAILED
                    run.error_message = str(exc)[:2000]
                    run.completed_at = datetime.now(UTC)
                    await fail_db.commit()
            return {"error": 1}

        if not seed_cells:
            async with session_factory() as fail_db:
                run = await fail_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.status = MapsCensusStatus.FAILED
                    run.error_message = "Grid planning returned no cells."
                    run.completed_at = datetime.now(UTC)
                    await fail_db.commit()
            return {"error": 1}

        region_ids_by_name: dict[str, str] = {}
        seen_query_texts: set[str] = set()
        seed_cells = _cap_to_budget(
            seed_cells, max_cells_per_campaign=max_cells_per_campaign, already_persisted=0
        )
        cell_ids = await self._persist_cells(
            session_factory,
            run_id=run_id,
            cells=seed_cells,
            region_ids_by_name=region_ids_by_name,
            seen_query_texts=seen_query_texts,
        )
        campaign_cells_used = len(cell_ids)
        async with session_factory() as cells_db:
            run = await cells_db.get(MapsCensusRun, run_id)
            if run is not None:
                run.cells_total = campaign_cells_used
                await cells_db.commit()

        client = create_places_client()
        summary = {"cells": campaign_cells_used, "places_found": 0, "cell_failures": 0}
        if not client.is_configured():
            async with session_factory() as fail_db:
                run = await fail_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.status = MapsCensusStatus.FAILED
                    run.error_message = "Google Places API key is not configured."
                    run.completed_at = datetime.now(UTC)
                    await fail_db.commit()
            return summary

        classification_model = get_model(settings.maps_census_model)
        classification_provider = get_provider_registry().get_provider(classification_model.provider)
        tracker = MapsQuotaTracker()
        campaign_budget = {"used": campaign_cells_used, "max": max_cells_per_campaign}

        await recover_stale_running_cells(
            session_factory, run_id=run_id, stale_seconds=settings.maps_census_cell_stale_seconds
        )

        work_queue = list(cell_ids)
        stopped_reason = "completed"
        while True:
            await self._execute_cells(
                session_factory,
                cell_ids=work_queue,
                run_id=run_id,
                country_code=country_code,
                client=client,
                settings=settings,
                summary=summary,
                classification_provider=classification_provider,
                classification_model_slug=classification_model.provider_model,
                country_name=country_name,
                tracker=tracker,
                campaign_budget=campaign_budget,
                seen_query_texts=seen_query_texts,
            )
            campaign_cells_used = campaign_budget["used"]

            all_terminal, expanding_names = await self._refresh_region_saturation(
                session_factory,
                run_id=run_id,
                campaign_cells_used=campaign_cells_used,
                max_cells_per_campaign=max_cells_per_campaign,
                settings=settings,
            )

            if campaign_cells_used >= max_cells_per_campaign:
                stopped_reason = "campaign_capped"
                break
            if should_stop_campaign(
                campaign_cells_used=campaign_cells_used,
                max_cells=max_cells_per_campaign,
                all_regions_terminal=all_terminal,
            ):
                stopped_reason = "all_regions_terminal"
                break
            if not expanding_names:
                stopped_reason = "no_expanding_regions"
                break

            remaining = max_cells_per_campaign - campaign_cells_used
            if remaining <= 0:
                await self._mark_regions_capped(
                    session_factory, run_id=run_id, region_names=expanding_names
                )
                stopped_reason = "campaign_capped"
                break

            expand_batch_size = min(remaining, MAPS_CENSUS_EXPANSION_BATCH_CAP)
            try:
                expansion_cells = await maps_grid_planner.plan(
                    country_code=country_code,
                    country_name=country_name,
                    max_cells=expand_batch_size,
                    country_profile=country_profile,
                    focus_region_names=expanding_names,
                )
            except MapsGridPlanningError:
                logger.warning(
                    "maps_census_expansion_planning_failed run_id=%s regions=%s",
                    run_id,
                    expanding_names,
                    exc_info=True,
                )
                stopped_reason = "no_expanding_regions"
                break

            # Chosen expansion strategy (documented in phase2-task-4-report.md):
            # the planner is hinted with focus_region_names, but we still
            # defensively filter to those region names and dedupe against every
            # query_text already persisted for this run, so a planner that
            # ignores the hint (or repeats a prior batch) can never re-plan the
            # whole country or duplicate work.
            focus_names_casefold = {name.strip().casefold() for name in expanding_names}
            expansion_cells = [
                cell
                for cell in expansion_cells
                if cell.region_name.strip().casefold() in focus_names_casefold
            ]
            expansion_cells = _cap_to_budget(
                expansion_cells,
                max_cells_per_campaign=max_cells_per_campaign,
                already_persisted=campaign_cells_used,
            )
            new_cell_ids = await self._persist_cells(
                session_factory,
                run_id=run_id,
                cells=expansion_cells,
                region_ids_by_name=region_ids_by_name,
                seen_query_texts=seen_query_texts,
            )
            if not new_cell_ids:
                stopped_reason = "no_expanding_regions"
                break

            campaign_cells_used += len(new_cell_ids)
            async with session_factory() as expand_run_db:
                run = await expand_run_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.cells_total = campaign_cells_used
                    await expand_run_db.commit()
            work_queue = new_cell_ids

        classification_summary = await self._classify_pending(
            session_factory,
            run_id=run_id,
            country_code=country_code,
            country_name=country_name,
            tracker=tracker,
        )
        summary.update(classification_summary)

        await self._validate_websites(session_factory, run_id=run_id, tracker=tracker)

        quota_snapshot = await merge_quota_metrics(session_factory, run_id=run_id, tracker=tracker)

        async with session_factory() as final_db:
            run = await final_db.get(MapsCensusRun, run_id)
            if run is not None:
                relevant_places = (
                    await final_db.execute(
                        select(MapsPlace).where(
                            MapsPlace.run_id == run_id, MapsPlace.is_relevant.is_(True)
                        )
                    )
                ).scalars().all()
                run.places_classified_relevant = len(relevant_places)
                run.places_with_website = sum(1 for p in relevant_places if p.official_website)

                regions = (
                    await final_db.execute(
                        select(MapsCensusRegion)
                        .where(MapsCensusRegion.run_id == run_id)
                        .order_by(MapsCensusRegion.region_name)
                    )
                ).scalars().all()
                all_cells = (
                    await final_db.execute(
                        select(MapsCensusCell).where(MapsCensusCell.run_id == run_id)
                    )
                ).scalars().all()
                capped_cells = sum(1 for c in all_cells if c.status == MapsCensusCellStatus.CAPPED)
                subdivision_cells = sum(1 for c in all_cells if c.parent_cell_id is not None)
                pages_fetched_total = sum(c.pages_fetched or 0 for c in all_cells)
                raw_results_total = sum(c.raw_results_found or 0 for c in all_cells)
                unique_results_total = sum(c.unique_results_found or 0 for c in all_cells)
                duplicates_total = sum(c.duplicates_found or 0 for c in all_cells)
                duplicate_rate = (
                    duplicates_total / raw_results_total if raw_results_total else 0.0
                )
                external_candidates = []
                if get_settings().maps_census_external_discovery_enabled:
                    external_candidates = await maps_external_discovery_coordinator.discover_from_profile(
                        country_code=run.country_code,
                        country_name=run.country_name,
                        profile=country_profile if isinstance(country_profile, dict) else None,
                    )

                run.funnel_metrics = {
                    "cells_planned": run.cells_total,
                    "cells_completed": run.cells_completed,
                    "cell_failures": summary.get("cell_failures", 0),
                    "capped_cells": capped_cells,
                    "subdivision_cells": subdivision_cells,
                    "pages_fetched": pages_fetched_total,
                    "raw_results_found": raw_results_total,
                    "unique_results_found": unique_results_total,
                    "duplicates_found": duplicates_total,
                    "duplicate_rate": duplicate_rate,
                    "places_found": run.places_found,
                    "places_classified_relevant": run.places_classified_relevant,
                    "eligible_candidates_found": sum(r.eligible_candidates_found for r in regions),
                    "review_candidates_found": sum(r.review_candidates_found for r in regions),
                    "confirmed_public_found": sum(r.confirmed_public_found for r in regions),
                    "individuals_found": sum(r.individuals_found for r in regions),
                    "unrelated_found": sum(r.unrelated_found for r in regions),
                    "country_profile_status": country_profile_status,
                    "external_discovery_candidates": len(external_candidates),
                    "quota_metrics": quota_snapshot,
                }
                run.saturation_summary = {
                    "campaign_cells_used": campaign_cells_used,
                    "max_cells_per_campaign": max_cells_per_campaign,
                    "stopped_reason": stopped_reason,
                    "regions": [
                        {
                            "region_name": region.region_name,
                            "saturation_status": region.saturation_status,
                            "cells_completed": region.cells_completed,
                            "unique_places_found": region.unique_places_found,
                            "new_unique_places_last_window": region.new_unique_places_last_window,
                            "new_plausible_providers_last_window": (
                                region.new_plausible_providers_last_window
                            ),
                            "eligible_candidates_found": region.eligible_candidates_found,
                            "review_candidates_found": region.review_candidates_found,
                            "confirmed_public_found": region.confirmed_public_found,
                            "individuals_found": region.individuals_found,
                            "unrelated_found": region.unrelated_found,
                        }
                        for region in regions
                    ],
                }
                run.status = MapsCensusStatus.COMPLETED
                run.completed_at = datetime.now(UTC)
                run.heartbeat_at = datetime.now(UTC)
                await final_db.commit()

        await self._maybe_enqueue_enrichment(run_id)
        return summary

    async def _get_or_create_region(
        self,
        session: AsyncSession,
        *,
        run_id: str,
        region_name: str | None,
        region_ids_by_name: dict[str, str],
    ) -> str:
        name = (region_name or "").strip() or "Unknown"
        key = name.casefold()
        cached = region_ids_by_name.get(key)
        if cached is not None:
            return cached
        region = await session.scalar(
            select(MapsCensusRegion).where(
                MapsCensusRegion.run_id == run_id, MapsCensusRegion.region_name == name
            )
        )
        if region is None:
            region = MapsCensusRegion(run_id=run_id, region_name=name)
            session.add(region)
            await session.flush()
        region_ids_by_name[key] = region.id
        return region.id

    async def _persist_cells(
        self,
        session_factory,
        *,
        run_id: str,
        cells: list,
        region_ids_by_name: dict[str, str],
        seen_query_texts: set[str],
    ) -> list[str]:
        """Insert planner cells as ``MapsCensusCell`` rows linked to a region,
        deduping against every query_text already persisted for this run
        (across seed and prior expansion rounds)."""
        cell_ids: list[str] = []
        async with session_factory() as db:
            for cell in cells:
                query_key = (cell.query_text or "").strip().casefold()
                if not query_key or query_key in seen_query_texts:
                    continue
                seen_query_texts.add(query_key)
                region_id = await self._get_or_create_region(
                    db,
                    run_id=run_id,
                    region_name=cell.region_name,
                    region_ids_by_name=region_ids_by_name,
                )
                row = MapsCensusCell(
                    run_id=run_id,
                    region_id=region_id,
                    region_name=cell.region_name,
                    city_name=cell.city_name,
                    query_text=cell.query_text,
                    query_family=cell.query_family,
                    query_language=cell.query_language,
                )
                db.add(row)
                await db.flush()
                cell_ids.append(row.id)
                region = await db.get(MapsCensusRegion, region_id)
                if region is not None:
                    region.cells_planned += 1
            await db.commit()
        return cell_ids

    async def _mark_regions_capped(
        self, session_factory, *, run_id: str, region_names: list[str]
    ) -> None:
        if not region_names:
            return
        names = {name.strip().casefold() for name in region_names}
        async with session_factory() as db:
            regions = (
                await db.execute(select(MapsCensusRegion).where(MapsCensusRegion.run_id == run_id))
            ).scalars().all()
            for region in regions:
                if region.region_name.strip().casefold() in names:
                    region.saturation_status = MapsRegionSaturationStatus.CAPPED.value
            await db.commit()

    async def _refresh_region_saturation(
        self,
        session_factory,
        *,
        run_id: str,
        campaign_cells_used: int,
        max_cells_per_campaign: int,
        settings,
    ) -> tuple[bool, list[str]]:
        """Recompute saturation status for every region in the run.

        Returns ``(all_regions_terminal, expanding_region_names)`` so the
        caller can decide whether to keep expanding or stop the campaign.
        """
        async with session_factory() as db:
            regions = (
                await db.execute(select(MapsCensusRegion).where(MapsCensusRegion.run_id == run_id))
            ).scalars().all()
            all_terminal = True
            expanding_names: list[str] = []
            for region in regions:
                cell_rows = (
                    await db.execute(
                        select(MapsCensusCell)
                        .where(
                            MapsCensusCell.region_id == region.id,
                            MapsCensusCell.status.in_(
                                [MapsCensusCellStatus.COMPLETED, MapsCensusCellStatus.FAILED]
                            ),
                        )
                        .order_by(MapsCensusCell.completed_at)
                    )
                ).scalars().all()
                metrics = compute_window_metrics(
                    [
                        CellWindowResult(
                            new_unique_places=c.new_unique_places,
                            new_plausible_places=c.new_plausible_places,
                        )
                        for c in cell_rows
                    ],
                    saturation_window=settings.maps_census_saturation_window,
                )
                decision = decide_region_saturation(
                    metrics=metrics,
                    cells_completed_total=region.cells_completed,
                    cells_planned_total=region.cells_planned,
                    campaign_cells_used=campaign_cells_used,
                    max_cells_per_campaign=max_cells_per_campaign,
                    min_new_unique_for_expansion=settings.maps_census_min_new_unique_for_expansion,
                    min_new_plausible_for_expansion=(
                        settings.maps_census_min_new_plausible_for_expansion
                    ),
                    saturation_window=settings.maps_census_saturation_window,
                )
                region.saturation_status = decision.status
                region.new_unique_places_last_window = metrics.new_unique_places
                region.new_plausible_providers_last_window = metrics.new_plausible_providers
                terminal = decision.status in _TERMINAL_SATURATION_STATUSES
                if not terminal:
                    all_terminal = False
                if decision.should_expand and not terminal:
                    expanding_names.append(region.region_name)
            await db.commit()
        return all_terminal, expanding_names

    async def _execute_cells(
        self,
        session_factory,
        *,
        cell_ids: list[str],
        run_id: str,
        country_code: str,
        country_name: str,
        client: Any,
        settings: Any,
        summary: dict[str, int],
        classification_provider: Any,
        classification_model_slug: str,
        tracker: MapsQuotaTracker,
        campaign_budget: dict[str, int] | None = None,
        seen_query_texts: set | None = None,
    ) -> None:
        """Run a paginated Places search for each queued cell, classify newly
        discovered places immediately (so saturation reflects real
        plausibility rather than raw discovery), subdivide any cell that hit
        its pagination/result cap into child cells, and record per-cell/region
        discovery counters. Never holds a DB session across a Places/LLM
        network await. Child cells generated mid-run are appended to the same
        work queue so they execute within this call (bounded by
        ``maps_census_max_subdivision_depth`` and the campaign cell budget).
        """
        from collections import deque

        queue: deque[str] = deque(cell_ids)
        local_seen_query_texts = seen_query_texts if seen_query_texts is not None else set()
        max_depth = max(0, int(settings.maps_census_max_subdivision_depth))
        max_cell_attempts = max(1, int(settings.maps_census_cell_max_attempts))

        while queue:
            cell_id = queue.popleft()

            if await is_run_cancelled(session_factory, run_id=run_id):
                break
            if await is_campaign_paused(session_factory, run_id=run_id):
                break

            async with session_factory() as heartbeat_db:
                run = await heartbeat_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.heartbeat_at = datetime.now(UTC)
                    await heartbeat_db.commit()

            async with session_factory() as cell_db:
                cell = await cell_db.get(MapsCensusCell, cell_id)
                if cell is None or cell.status in (
                    MapsCensusCellStatus.COMPLETED,
                    MapsCensusCellStatus.CAPPED,
                ):
                    continue
                cell.status = MapsCensusCellStatus.IN_PROGRESS
                cell.attempt_count = (cell.attempt_count or 0) + 1
                cell.started_at = cell.started_at or datetime.now(UTC)
                cell.heartbeat_at = datetime.now(UTC)
                query_text = cell.query_text
                region_name = cell.region_name
                city_name = cell.city_name
                region_id = cell.region_id
                query_family = cell.query_family
                query_language = cell.query_language
                expansion_depth = cell.expansion_depth or 0
                viewport_bounds = cell.viewport_bounds
                resume_token = cell.pagination_resume_token
                await cell_db.commit()

            try:
                outcome = await client.search_text_paginated(
                    query=query_text,
                    region_code=country_code,
                    page_size=settings.maps_census_places_page_size,
                    max_pages=settings.maps_census_max_pages_per_cell,
                    resume_page_token=resume_token,
                )
            except PlacesProviderError as exc:
                retry_info = await fail_cell_for_retry(
                    session_factory,
                    cell_id=cell_id,
                    error=str(exc),
                    max_attempts=max_cell_attempts,
                )
                if retry_info["terminal"]:
                    summary["cell_failures"] += 1
                    async with session_factory() as cell_db:
                        run = await cell_db.get(MapsCensusRun, run_id)
                        if run is not None:
                            run.cells_completed += 1
                        if region_id:
                            region = await cell_db.get(MapsCensusRegion, region_id)
                            if region is not None:
                                region.cells_completed += 1
                        await cell_db.commit()
                else:
                    queue.append(cell_id)
                continue

            tracker.add_places_request(pages=max(1, outcome.pages_fetched))

            async with session_factory() as write_db:
                new_place_ids: list[str] = []
                for result in outcome.places:
                    existing = await write_db.scalar(
                        select(MapsPlace).where(
                            MapsPlace.run_id == run_id,
                            MapsPlace.google_place_id == result.google_place_id,
                        )
                    )
                    if existing is not None:
                        continue
                    row = MapsPlace(
                        run_id=run_id,
                        google_place_id=result.google_place_id,
                        raw_name=result.raw_name,
                        canonical_name=result.raw_name,
                        place_types=result.place_types,
                        formatted_address=result.formatted_address,
                        city_name=city_name,
                        region_name=region_name,
                        latitude=result.latitude,
                        longitude=result.longitude,
                        international_phone_number=result.international_phone_number,
                        raw_website=result.website,
                        discovered_via_query=query_text,
                        photo_reference=result.photo_reference,
                    )
                    write_db.add(row)
                    await write_db.flush()
                    new_place_ids.append(row.id)
                new_places = len(new_place_ids)

                cell = await write_db.get(MapsCensusCell, cell_id)
                if cell is not None:
                    cell.places_found = outcome.raw_results_found
                    cell.pages_fetched = outcome.pages_fetched
                    cell.raw_results_found = outcome.raw_results_found
                    cell.unique_results_found = outcome.unique_results_found
                    cell.duplicates_found = outcome.duplicates_found
                    cell.next_page_available = outcome.next_page_available
                    cell.result_cap_reached = outcome.result_cap_reached
                    cell.pagination_error = outcome.pagination_error
                    cell.pagination_resume_token = outcome.resume_page_token
                    cell.new_unique_places = new_places

                run = await write_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.places_found += new_places
                await write_db.commit()
            summary["places_found"] += new_places

            lifecycle_counts = await self._classify_new_place_ids(
                session_factory,
                place_ids=new_place_ids,
                provider=classification_provider,
                model_slug=classification_model_slug,
                country_code=country_code,
                country_name=country_name,
                tracker=tracker,
            )
            new_plausible = lifecycle_counts.get("plausible_total", 0)

            needs_subdivision = outcome.result_cap_reached and expansion_depth < max_depth
            child_ids: list[str] = []
            if needs_subdivision:
                async with session_factory() as profile_db:
                    run = await profile_db.get(MapsCensusRun, run_id)
                    country_profile = run.country_profile if run is not None else None
                specs = subdivide_cell(
                    region_name=region_name,
                    city_name=city_name,
                    query_text=query_text,
                    query_family=query_family,
                    query_language=query_language,
                    country_profile=country_profile,
                    viewport_bounds=viewport_bounds,
                    existing_query_texts=local_seen_query_texts,
                )
                if campaign_budget is not None:
                    remaining = max(0, campaign_budget["max"] - campaign_budget["used"])
                    specs = specs[:remaining]
                if specs:
                    child_ids = await self._persist_child_cells(
                        session_factory,
                        run_id=run_id,
                        region_id=region_id,
                        parent_cell_id=cell_id,
                        expansion_depth=expansion_depth + 1,
                        specs=specs,
                        seen_query_texts=local_seen_query_texts,
                    )
                    if campaign_budget is not None:
                        campaign_budget["used"] += len(child_ids)

            capped = bool(needs_subdivision and child_ids)
            async with session_factory() as write_db:
                cell = await write_db.get(MapsCensusCell, cell_id)
                region = await write_db.get(MapsCensusRegion, region_id) if region_id else None
                run = await write_db.get(MapsCensusRun, run_id)
                if cell is not None:
                    cell.new_plausible_places = new_plausible
                    cell.completed_at = datetime.now(UTC)
                    cell.status = (
                        MapsCensusCellStatus.CAPPED if capped else MapsCensusCellStatus.COMPLETED
                    )
                if run is not None:
                    run.cells_completed += 1
                    if child_ids:
                        run.cells_total += len(child_ids)
                if region is not None:
                    region.unique_places_found += new_places
                    region.plausible_providers_found += new_plausible
                    # A capped cell's own coverage of its geography is known
                    # incomplete — its child cells (not it) count toward
                    # region.cells_completed once they finish.
                    if not capped:
                        region.cells_completed += 1
                    region.eligible_candidates_found += lifecycle_counts.get("eligible", 0)
                    region.review_candidates_found += lifecycle_counts.get("review", 0)
                    region.confirmed_public_found += lifecycle_counts.get("public", 0)
                    region.individuals_found += lifecycle_counts.get("individual", 0)
                    region.unrelated_found += lifecycle_counts.get("unrelated", 0)
                await write_db.commit()

            if child_ids:
                queue.extend(child_ids)

    async def _classify_new_place_ids(
        self,
        session_factory,
        *,
        place_ids: list[str],
        provider: Any,
        model_slug: str,
        country_code: str,
        country_name: str,
        tracker: MapsQuotaTracker,
    ) -> dict[str, int]:
        """Classify a cell's newly discovered places immediately, applying the
        same lifecycle/eligibility mapping as ``_classify_pending``. Returns a
        counts dict used for real-time region saturation/candidate metrics —
        this is what fixes ``new_plausible_places`` no longer equalling
        ``new_unique_places`` (Phase 2 gap #5).
        """
        counts = {
            "plausible_total": 0,
            "eligible": 0,
            "review": 0,
            "public": 0,
            "individual": 0,
            "unrelated": 0,
        }
        if not place_ids:
            return counts

        settings = get_settings()
        batch_size = max(int(settings.maps_census_classification_batch_size or 15), 1)

        for offset in range(0, len(place_ids), batch_size):
            batch_ids = place_ids[offset : offset + batch_size]
            async with session_factory() as batch_db:
                batch = (
                    await batch_db.execute(select(MapsPlace).where(MapsPlace.id.in_(batch_ids)))
                ).scalars().all()
                payloads = [
                    {
                        "place_id": place.id,
                        "name": place.raw_name,
                        "place_types": place.place_types,
                        "address": place.formatted_address,
                        "has_street_address": has_street_address(place.formatted_address),
                    }
                    for place in batch
                ]

            decisions = await self._classify_batch(
                provider=provider,
                model_slug=model_slug,
                country_code=country_code,
                country_name=country_name,
                payloads=payloads,
            )
            tracker.add_classifier_call()

            async with session_factory() as write_db:
                writable = (
                    await write_db.execute(select(MapsPlace).where(MapsPlace.id.in_(batch_ids)))
                ).scalars().all()
                by_id = {place.id: place for place in writable}
                for decision in decisions:
                    place = by_id.get(decision.place_id)
                    if place is None:
                        continue
                    lifecycle_status = _lifecycle_from_classification(decision)
                    place.lifecycle_status = lifecycle_status.value
                    place.is_relevant = derive_is_relevant(lifecycle_status.value)
                    place.relevance_reason = decision.reason[:300]
                    place.confidence_score = decision.confidence
                    place.classification_confidence = decision.confidence
                    place.verification_verdict = derive_legacy_verification_verdict(
                        lifecycle_status.value
                    )
                    if place.is_relevant:
                        counts["plausible_total"] += 1
                        if lifecycle_status == MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER:
                            counts["individual"] += 1
                        elif lifecycle_status == MapsLifecycleStatus.NEEDS_REVIEW:
                            counts["review"] += 1
                        else:
                            counts["eligible"] += 1
                    elif lifecycle_status == MapsLifecycleStatus.CONFIRMED_PUBLIC:
                        counts["public"] += 1
                    else:
                        counts["unrelated"] += 1
                await write_db.commit()
        return counts

    async def _persist_child_cells(
        self,
        session_factory,
        *,
        run_id: str,
        region_id: str | None,
        parent_cell_id: str,
        expansion_depth: int,
        specs: list[ChildCellSpec],
        seen_query_texts: set,
    ) -> list[str]:
        """Persist capped-cell subdivision children, deduping regular (non
        viewport-bound) specs against every query_text already used in this
        run. Viewport-quadrant children intentionally reuse their parent's
        query text (the bounding box, not the text, differentiates them), so
        they are keyed separately and never collide with the text-only set.
        """
        cell_ids: list[str] = []
        async with session_factory() as db:
            for spec in specs:
                if spec.viewport_bounds:
                    key: Any = (spec.query_text.strip().casefold(), tuple(sorted(spec.viewport_bounds.items())))
                else:
                    key = spec.query_text.strip().casefold()
                    if key in seen_query_texts:
                        continue
                    seen_query_texts.add(key)

                row = MapsCensusCell(
                    run_id=run_id,
                    region_id=region_id,
                    region_name=spec.region_name,
                    city_name=spec.city_name,
                    query_text=spec.query_text,
                    query_family=spec.query_family,
                    query_language=spec.query_language,
                    parent_cell_id=parent_cell_id,
                    expansion_reason=spec.expansion_reason,
                    expansion_depth=expansion_depth,
                    viewport_bounds=spec.viewport_bounds,
                )
                db.add(row)
                await db.flush()
                cell_ids.append(row.id)
                if region_id:
                    region = await db.get(MapsCensusRegion, region_id)
                    if region is not None:
                        region.cells_planned += 1
            await db.commit()
        return cell_ids

    async def _classify_pending(
        self,
        session_factory,
        *,
        run_id: str,
        country_code: str,
        country_name: str,
        tracker: MapsQuotaTracker | None = None,
    ) -> dict[str, int]:
        settings = get_settings()
        async with session_factory() as scan_db:
            pending = (
                await scan_db.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id, MapsPlace.is_relevant.is_(None)
                    )
                )
            ).scalars().all()
            pending_ids = [p.id for p in pending]
            await scan_db.commit()

        if not pending_ids:
            return {"classified": 0}

        model = get_model(settings.maps_census_model)
        provider = get_provider_registry().get_provider(model.provider)
        batch_size = max(int(settings.maps_census_classification_batch_size or 15), 1)
        classified = 0

        for offset in range(0, len(pending_ids), batch_size):
            batch_ids = pending_ids[offset : offset + batch_size]
            async with session_factory() as batch_db:
                run = await batch_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.heartbeat_at = datetime.now(UTC)
                batch = (
                    await batch_db.execute(select(MapsPlace).where(MapsPlace.id.in_(batch_ids)))
                ).scalars().all()
                payloads = [
                    {
                        "place_id": place.id,
                        "name": place.raw_name,
                        "place_types": place.place_types,
                        "address": place.formatted_address,
                        "has_street_address": has_street_address(place.formatted_address),
                    }
                    for place in batch
                ]
                await batch_db.commit()

            decisions = await self._classify_batch(
                provider=provider,
                model_slug=model.provider_model,
                country_code=country_code,
                country_name=country_name,
                payloads=payloads,
            )
            if tracker is not None:
                tracker.add_classifier_call()

            async with session_factory() as write_db:
                writable = (
                    await write_db.execute(select(MapsPlace).where(MapsPlace.id.in_(batch_ids)))
                ).scalars().all()
                by_id = {place.id: place for place in writable}
                for decision in decisions:
                    place = by_id.get(decision.place_id)
                    if place is None:
                        continue
                    lifecycle_status = _lifecycle_from_classification(decision)
                    place.lifecycle_status = lifecycle_status.value
                    place.is_relevant = derive_is_relevant(lifecycle_status.value)
                    place.relevance_reason = decision.reason[:300]
                    place.confidence_score = decision.confidence
                    place.classification_confidence = decision.confidence
                    place.verification_verdict = derive_legacy_verification_verdict(
                        lifecycle_status.value
                    )
                    classified += 1
                await write_db.commit()

        await self._apply_post_classification_filters(session_factory, run_id=run_id)
        return {"classified": classified}

    async def _classify_batch(
        self,
        *,
        provider: Any,
        model_slug: str,
        country_code: str,
        country_name: str,
        payloads: list[dict[str, Any]],
    ) -> list[MapsRelevanceDecision]:
        prompt = get_prompt_engine().render(
            "scraping/maps_relevance_classifier.j2",
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            places_json=json.dumps(payloads, ensure_ascii=True),
        )
        try:
            response = await asyncio.wait_for(
                provider.complete(
                    system=(
                        "You return strict JSON relevance decisions for candidate addiction-"
                        "treatment provider places found via Google Places search."
                    ),
                    user=prompt,
                    model=model_slug,
                    max_tokens=3000,
                ),
                timeout=CLASSIFICATION_BATCH_TIMEOUT_SECONDS,
            )
            raw = LLMProvider.parse_json_response(response.text)
            plan = MapsRelevancePlan.model_validate(_normalize_relevance_payload(raw))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "maps_census_classification_batch_failed count=%s error=%s",
                len(payloads),
                exc,
            )
            return [
                MapsRelevanceDecision(
                    place_id=item["place_id"],
                    is_relevant=False,
                    reason="classification_failed",
                    confidence=0.0,
                )
                for item in payloads
            ]
        by_id = {d.place_id: d for d in plan.decisions if d.place_id}
        return [
            by_id.get(item["place_id"])
            or MapsRelevanceDecision(
                place_id=item["place_id"],
                is_relevant=False,
                reason="missing_decision",
                confidence=0.0,
            )
            for item in payloads
        ]

    async def _validate_websites(
        self, session_factory, *, run_id: str, tracker: MapsQuotaTracker | None = None
    ) -> None:
        """Trust a Places-provided website when it passes strict validation; otherwise
        fall back to a name+geography search for any relevant place still missing one.
        """
        settings = get_settings()
        async with session_factory() as db:
            places = (
                await db.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.is_relevant.is_(True),
                        MapsPlace.raw_website.is_not(None),
                    )
                )
            ).scalars().all()
            for place in places:
                if not website_needs_enrichment(place.raw_website):
                    place.official_website = place.raw_website
                    place.website_source = "places"
            await db.commit()

        if settings.maps_census_website_search_enabled:
            await self._search_missing_websites(session_factory, run_id=run_id, tracker=tracker)
        await self._apply_places_social_fallbacks(session_factory, run_id=run_id)
        await self._propagate_shared_websites(session_factory, run_id=run_id)
        await self._apply_missing_contact_filter(session_factory, run_id=run_id)

    async def _apply_places_social_fallbacks(self, session_factory, *, run_id: str) -> int:
        """Keep Google-provided Facebook pages when no official domain was found.

        Search still runs first so a dedicated website wins. We only trust social
        URLs attached directly to the Google Place; Sonar/search candidates remain
        subject to the strict official-domain policy to avoid identity mismatches.
        """
        async with session_factory() as db:
            places = (
                await db.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.is_relevant.is_(True),
                        MapsPlace.official_website.is_(None),
                        MapsPlace.raw_website.is_not(None),
                    )
                )
            ).scalars().all()
            applied = 0
            for place in places:
                if _is_facebook_url(place.raw_website):
                    place.official_website = place.raw_website
                    place.website_source = "places_social"
                    applied += 1
            if applied:
                await db.commit()
            return applied

    async def _apply_post_classification_filters(self, session_factory, *, run_id: str) -> int:
        """Apply only the remaining hard guardrails after classifier banding."""
        changed = 0
        async with session_factory() as db:
            places = (
                await db.execute(select(MapsPlace).where(MapsPlace.run_id == run_id))
            ).scalars().all()
            for place in places:
                if not place.is_relevant:
                    continue
                if is_generic_facility_name(place.canonical_name or place.raw_name):
                    _set_place_lifecycle(
                        place,
                        MapsLifecycleStatus.UNRELATED,
                        reason="excluded: generic name with no facility identity",
                    )
                    changed += 1
                    continue
                if not has_street_address(place.formatted_address):
                    if place.lifecycle_status != MapsLifecycleStatus.NEEDS_REVIEW.value:
                        _set_place_lifecycle(
                            place,
                            MapsLifecycleStatus.NEEDS_REVIEW,
                            reason="flagged: no street address (Plus Code only)",
                        )
                        changed += 1
            if changed:
                await db.commit()
        return changed

    async def _apply_missing_contact_filter(self, session_factory, *, run_id: str) -> int:
        """Refresh contact completeness without demoting uncertain facilities."""
        updated = 0
        async with session_factory() as db:
            places = (
                await db.execute(select(MapsPlace).where(MapsPlace.run_id == run_id))
            ).scalars().all()
            for place in places:
                contact_status = _contact_status_for_place(place)
                if place.contact_status == contact_status:
                    continue
                place.contact_status = contact_status
                updated += 1
            if updated:
                await db.commit()
        return updated

    async def _delete_unverified_places(self, session_factory, *, run_id: str) -> int:
        """Permanently remove relevant facilities lacking a verified official site."""
        async with session_factory() as db:
            result = await db.execute(
                delete(MapsPlace).where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.is_relevant.is_(True),
                    MapsPlace.official_website.is_(None),
                )
            )
            await db.commit()
            return int(result.rowcount or 0)

    async def _drop_untrusted_websites(self, session_factory, *, run_id: str) -> int:
        """Re-validate stored websites so blocklist changes apply retroactively.

        A directory that slipped through earlier (e.g. a B2B listing site added to
        the blocklist later) is cleared here, which also re-queues that place for
        the search pass below.
        """
        async with session_factory() as db:
            places = (
                await db.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.is_relevant.is_(True),
                        MapsPlace.official_website.is_not(None),
                    )
                )
            ).scalars().all()
            dropped = 0
            for place in places:
                if website_needs_enrichment(place.official_website) and not _is_facebook_url(
                    place.official_website
                ):
                    place.official_website = None
                    place.website_source = None
                    dropped += 1
            if dropped:
                await db.commit()
            return dropped

    async def _propagate_shared_websites(self, session_factory, *, run_id: str) -> None:
        """Same-name multi-location facilities share one official website when unambiguous.

        If every known website in a canonical-name group is the same URL, copy it to
        sibling locations that are still missing one. If the group already has
        conflicting websites (different municipal facilities that happen to share a
        generic name), leave each location alone.
        """
        async with session_factory() as db:
            places = (
                await db.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.is_relevant.is_(True),
                    )
                )
            ).scalars().all()
            by_name: dict[str, list[MapsPlace]] = {}
            for place in places:
                key = (place.canonical_name or "").strip().casefold()
                if not key:
                    continue
                by_name.setdefault(key, []).append(place)

            changed = False
            for group in by_name.values():
                if len(group) < 2:
                    continue
                known = {
                    (p.official_website or "").strip()
                    for p in group
                    if (p.official_website or "").strip()
                }
                if len(known) != 1:
                    continue
                shared_url = next(iter(known))
                donor = next(p for p in group if (p.official_website or "").strip() == shared_url)
                for place in group:
                    if place.official_website:
                        continue
                    place.official_website = shared_url
                    place.website_source = donor.website_source or "search"
                    changed = True
            if changed:
                await db.commit()

    async def _search_missing_websites(
        self, session_factory, *, run_id: str, tracker: MapsQuotaTracker | None = None
    ) -> None:
        """Resumable batch loop over every relevant place still missing an
        official website — Phase 2 gap #4. Previously this queried at most
        ``maps_census_website_search_max_places_per_run`` (250) places and
        silently dropped the rest; it now keeps pulling fixed-size batches
        (keyset-paginated by ``MapsPlace.id`` for guaranteed forward
        progress) until none remain or the call budget is hit, persisting a
        resume cursor on ``run.processing_state`` when paused.
        """
        settings = get_settings()
        await self._drop_untrusted_websites(session_factory, run_id=run_id)

        async with session_factory() as scan_db:
            run = await scan_db.get(MapsCensusRun, run_id)
            if run is None:
                return
            country_name = run.country_name
            country_code = run.country_code
            state = dict(run.processing_state or {})
            cursor: str | None = state.get("website_search_cursor") if state.get(
                "website_search_paused"
            ) else None

        mode = (settings.maps_census_website_search_mode or "llm").strip().lower()
        batch_size = max(1, settings.maps_census_website_search_batch_size)
        max_calls = max(1, settings.maps_census_website_search_max_calls_per_run)
        calls_made = 0
        paused = False

        while True:
            async with session_factory() as scan_db:
                query = select(MapsPlace).where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.is_relevant.is_(True),
                    MapsPlace.official_website.is_(None),
                )
                if cursor:
                    query = query.where(MapsPlace.id > cursor)
                pending = (
                    await scan_db.execute(query.order_by(MapsPlace.id).limit(batch_size))
                ).scalars().all()
                pending_items = [
                    {
                        "id": place.id,
                        "name": place.canonical_name,
                        "city": place.city_name or place.region_name,
                        "address": place.formatted_address,
                        "phone": place.international_phone_number,
                    }
                    for place in pending
                ]

            if not pending_items:
                break
            if calls_made >= max_calls:
                paused = True
                break

            if mode == "llm":
                await self._find_missing_websites_llm(
                    session_factory,
                    run_id=run_id,
                    pending_items=pending_items,
                    country_code=country_code,
                    country_name=country_name,
                    tracker=tracker,
                )
            else:
                await self._find_missing_websites_serper(
                    session_factory,
                    run_id=run_id,
                    pending_items=pending_items,
                    country_code=country_code,
                    country_name=country_name,
                    tracker=tracker,
                )
            calls_made += len(pending_items)
            cursor = pending_items[-1]["id"]

        async with session_factory() as state_db:
            run = await state_db.get(MapsCensusRun, run_id)
            if run is not None:
                state = dict(run.processing_state or {})
                state["website_search_paused"] = paused
                state["website_search_cursor"] = cursor if paused else None
                limits_reached = dict(state.get("limits_reached") or {})
                limits_reached["website_search"] = paused
                state["limits_reached"] = limits_reached
                run.processing_state = state
                await state_db.commit()

    async def _find_missing_websites_llm(
        self,
        session_factory,
        *,
        run_id: str,
        pending_items: list[dict[str, Any]],
        country_code: str,
        country_name: str,
        tracker: MapsQuotaTracker | None = None,
    ) -> None:
        settings = get_settings()
        if not settings.maps_census_website_llm_enabled:
            return

        model = get_model(settings.maps_census_website_llm_model)
        llm_provider = get_provider_registry().get_provider(model.provider)
        batch_size = max(1, settings.maps_census_website_llm_batch_size)
        for offset in range(0, len(pending_items), batch_size):
            batch = pending_items[offset : offset + batch_size]
            async with session_factory() as heartbeat_db:
                run = await heartbeat_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.heartbeat_at = datetime.now(UTC)
                    await heartbeat_db.commit()

            decisions = await self._find_websites_llm_batch(
                provider=llm_provider,
                model_slug=model.provider_model,
                country_code=country_code,
                country_name=country_name,
                batch=batch,
            )
            if tracker is not None:
                tracker.add_website_lookup_call()
            by_id = {d.place_id: d for d in decisions}
            for item in batch:
                decision = by_id.get(item["id"])
                url = await _accepted_direct_llm_website_url(
                    decision.url if decision is not None else None,
                    confidence=float(decision.confidence if decision is not None else 0),
                    page_name=decision.page_name if decision is not None else "",
                    facility_name=item.get("name"),
                )
                if not url:
                    continue
                async with session_factory() as write_db:
                    place = await write_db.get(MapsPlace, item["id"])
                    if place is not None and place.official_website is None:
                        place.official_website = url
                        place.website_source = "llm_social" if _is_facebook_url(url) else "llm"
                    await write_db.commit()

    async def _find_missing_websites_serper(
        self,
        session_factory,
        *,
        run_id: str,
        pending_items: list[dict[str, Any]],
        country_code: str,
        country_name: str,
        tracker: MapsQuotaTracker | None = None,
    ) -> None:
        settings = get_settings()
        provider = create_search_provider()
        llm_queue: list[dict[str, Any]] = []
        for item in pending_items:
            async with session_factory() as heartbeat_db:
                run = await heartbeat_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.heartbeat_at = datetime.now(UTC)
                    await heartbeat_db.commit()

            results = await self._search_website_candidates(
                provider,
                item=item,
                country_code=country_code,
                country_name=country_name,
            )
            if tracker is not None:
                tracker.add_website_lookup_call()
            if not results:
                continue

            selected = select_official_website(
                facility_name=item["name"],
                city=item["city"],
                country_name=country_name,
                results=results,
            )
            if selected is None:
                selected = _match_official_website_by_address(
                    address=item["address"],
                    city=item["city"],
                    country_name=country_name,
                    results=results,
                )
            if selected is not None:
                async with session_factory() as write_db:
                    place = await write_db.get(MapsPlace, item["id"])
                    if place is not None and place.official_website is None:
                        place.official_website = selected.url
                        place.website_source = "search"
                    await write_db.commit()
                continue

            if settings.maps_census_website_llm_enabled:
                llm_queue.append({**item, "candidates": results})

        if not llm_queue:
            return

        model = get_model(settings.maps_census_website_llm_model)
        llm_provider = get_provider_registry().get_provider(model.provider)
        batch_size = max(1, settings.maps_census_website_llm_batch_size)
        for offset in range(0, len(llm_queue), batch_size):
            batch = llm_queue[offset : offset + batch_size]
            async with session_factory() as heartbeat_db:
                run = await heartbeat_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.heartbeat_at = datetime.now(UTC)
                    await heartbeat_db.commit()

            decisions = await self._select_websites_llm_batch(
                provider=llm_provider,
                model_slug=model.provider_model,
                country_code=country_code,
                country_name=country_name,
                batch=batch,
            )
            by_id = {d.place_id: d for d in decisions}
            for item in batch:
                decision = by_id.get(item["id"])
                url = _accepted_llm_website_url(
                    decision.url if decision is not None else None,
                    candidates=item["candidates"],
                )
                if not url:
                    continue
                async with session_factory() as write_db:
                    place = await write_db.get(MapsPlace, item["id"])
                    if place is not None and place.official_website is None:
                        place.official_website = url
                        place.website_source = "search"
                    await write_db.commit()

    async def _find_websites_llm_batch(
        self,
        *,
        provider: Any,
        model_slug: str,
        country_code: str,
        country_name: str,
        batch: list[dict[str, Any]],
    ) -> list[MapsWebsiteDecision]:
        payloads = [
            {
                "place_id": item["id"],
                "name": item["name"],
                "city": item.get("city"),
                "address": item.get("address"),
                "phone": item.get("phone"),
            }
            for item in batch
        ]
        settings = get_settings()
        prompt = get_prompt_engine().render(
            "scraping/maps_website_finder.j2",
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            facilities_json=json.dumps(payloads, ensure_ascii=False),
        )
        timeout_seconds = max(
            WEBSITE_LLM_BATCH_TIMEOUT_SECONDS,
            settings.maps_census_website_llm_timeout_seconds,
        )
        try:
            response = await asyncio.wait_for(
                provider.complete(
                    system=(
                        "You have live web search. Return strict JSON with official "
                        "rehabilitation facility homepage URLs when found, otherwise null."
                    ),
                    user=prompt,
                    model=model_slug,
                    max_tokens=2500,
                ),
                timeout=timeout_seconds,
            )
            raw = LLMProvider.parse_json_response(response.text)
            plan = MapsWebsitePlan.model_validate(_normalize_website_payload(raw))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "maps_census_website_finder_batch_failed count=%s error=%s",
                len(payloads),
                exc,
            )
            return [
                MapsWebsiteDecision(place_id=p["place_id"], url=None, reason="llm_failed")
                for p in payloads
            ]

        by_id = {d.place_id: d for d in plan.decisions}
        return [
            by_id.get(p["place_id"])
            or MapsWebsiteDecision(place_id=p["place_id"], url=None, reason="missing_decision")
            for p in payloads
        ]

    async def _search_website_candidates(
        self,
        provider: Any,
        *,
        item: dict[str, Any],
        country_code: str,
        country_name: str,
    ) -> list[SearchProviderResult]:
        settings = get_settings()
        merged: list[SearchProviderResult] = []
        seen_urls: set[str] = set()
        for query in _maps_website_search_queries(
            name=item["name"],
            city=item["city"],
            country_name=country_name,
            address=item["address"],
        ):
            try:
                results = await asyncio.wait_for(
                    provider.search(
                        SearchProviderRequest(
                            query=query,
                            country_code=country_code,
                            search_language="en",
                            result_limit=settings.facility_website_enrichment_results_per_facility,
                            metadata={
                                "purpose": "maps_census_official_website",
                                "place_id": item["id"],
                            },
                        )
                    ),
                    timeout=settings.facility_website_enrichment_timeout_seconds,
                )
            except (TimeoutError, SearchProviderError):
                continue
            for result in results:
                key = (result.url or "").strip().casefold()
                if not key or key in seen_urls:
                    continue
                seen_urls.add(key)
                merged.append(
                    SearchProviderResult(
                        rank=len(merged) + 1,
                        url=result.url,
                        title=result.title,
                        snippet=result.snippet,
                    )
                )
            # First non-empty query is enough — further variants are only needed
            # when the previous query returned nothing (e.g. quoted transliteration).
            if merged:
                break
        return merged

    async def _select_websites_llm_batch(
        self,
        *,
        provider: Any,
        model_slug: str,
        country_code: str,
        country_name: str,
        batch: list[dict[str, Any]],
    ) -> list[MapsWebsiteDecision]:
        payloads = []
        for item in batch:
            candidates = []
            for result in item.get("candidates") or []:
                if _is_rejected_result(result):
                    continue
                candidates.append(
                    {
                        "url": result.url,
                        "title": (result.title or "")[:200],
                        "snippet": (result.snippet or "")[:240],
                    }
                )
            payloads.append(
                {
                    "place_id": item["id"],
                    "name": item["name"],
                    "city": item.get("city"),
                    "address": item.get("address"),
                    "phone": item.get("phone"),
                    "candidates": candidates[:8],
                }
            )
        if not any(p["candidates"] for p in payloads):
            return [
                MapsWebsiteDecision(place_id=p["place_id"], url=None, reason="no_candidates")
                for p in payloads
            ]

        prompt = get_prompt_engine().render(
            "scraping/maps_website_selector.j2",
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            facilities_json=json.dumps(payloads, ensure_ascii=False),
        )
        try:
            response = await asyncio.wait_for(
                provider.complete(
                    system=(
                        "You return strict JSON picking official facility homepage URLs "
                        "from provided search candidates only."
                    ),
                    user=prompt,
                    model=model_slug,
                    max_tokens=2500,
                ),
                timeout=WEBSITE_LLM_BATCH_TIMEOUT_SECONDS,
            )
            raw = LLMProvider.parse_json_response(response.text)
            plan = MapsWebsitePlan.model_validate(_normalize_website_payload(raw))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "maps_census_website_llm_batch_failed count=%s error=%s",
                len(payloads),
                exc,
            )
            return [
                MapsWebsiteDecision(place_id=p["place_id"], url=None, reason="llm_failed")
                for p in payloads
            ]

        by_id = {d.place_id: d for d in plan.decisions}
        return [
            by_id.get(p["place_id"])
            or MapsWebsiteDecision(place_id=p["place_id"], url=None, reason="missing_decision")
            for p in payloads
        ]


def _cap_to_budget(
    cells: list, *, max_cells_per_campaign: int, already_persisted: int
) -> list:
    """Slice a planner batch down to the campaign's remaining cell budget.

    Guards against a planner that ignores the ``max_cells`` hint it was given
    (e.g. a test fake, or an LLM that overshoots) so the campaign ceiling is
    always the source of truth for how many cells actually get persisted.
    """
    remaining = max(0, max_cells_per_campaign - already_persisted)
    return list(cells[:remaining])


_LETTER_DIGIT_BOUNDARY = re.compile(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", re.UNICODE)


def _split_glued_house_numbers(value: str) -> str:
    return _LETTER_DIGIT_BOUNDARY.sub(" ", value)


def _maps_website_search_queries(
    *,
    name: str,
    city: str | None,
    country_name: str,
    address: str | None,
) -> list[str]:
    """Ordered Serper queries for Maps missing-website backfill.

    The shared quoted ``"name" … official website`` query is great for Latin
    facility names, but Google Places often returns Latin *transliterations*
    of Cyrillic names — quoting those yields zero hits. Fall back to an
    unquoted name query, then a street-address query that keeps the native
    script Google left in ``formatted_address``.
    """
    queries: list[str] = []
    quoted = build_official_website_query(name=name, city=city, country_name=country_name)
    if quoted:
        queries.append(quoted)

    geography = " ".join(part for part in (city, country_name) if part)
    unquoted = f"{name.strip()} {geography} official website".strip()[:240]
    if unquoted and unquoted not in queries:
        queries.append(unquoted)

    street = _street_query_fragment(address)
    if street:
        city_part = (city or "").strip()
        address_query = f"{street} {city_part} official website".strip()[:240]
        if address_query and address_query not in queries:
            queries.append(address_query)
    return queries


def _street_query_fragment(address: str | None) -> str | None:
    if not address:
        return None
    # Prefer the street/house portion before the first comma — that's where
    # Google usually keeps native-script street names for Belarus etc.
    head = address.split(",", 1)[0].strip()
    spaced = _split_glued_house_numbers(head).strip()
    tokens = _address_tokens(spaced)
    if len(tokens) < 2:
        return None
    return spaced[:160] or None


def _address_tokens(address: str | None) -> set[str]:
    if not address:
        return set()
    # Google's formatted_address often glues the house number onto the street
    # name with no separator (e.g. "Гастелло16"), which would otherwise
    # tokenize as one blob that never matches a real site's "ул. Гастелло, 16".
    # Insert a boundary at letter/digit transitions so the number tokenizes
    # separately — it's often the second signal alongside a street name that
    # confirms a real match. _tokens() already drops 1-char tokens and stopwords.
    return _tokens(_split_glued_house_numbers(address))


def _match_official_website_by_address(
    *,
    address: str | None,
    city: str | None,
    country_name: str,
    results: list[SearchProviderResult],
) -> OfficialWebsiteCandidate | None:
    """Fallback matcher for when Places' transliterated name can't be matched
    against native-script site content — matches on address tokens instead
    (street name, house number), which Google often leaves partially untransliterated.
    """
    address_tokens = _address_tokens(address)
    if len(address_tokens) < 2:
        return None
    scored: list[OfficialWebsiteCandidate] = []
    for item in results:
        if _is_rejected_result(item):
            continue
        parsed = _safe_url(item.url)
        if parsed is None:
            continue
        blob_tokens = _tokens(f"{item.title} {item.snippet}")
        overlap = address_tokens & blob_tokens
        # Require at least two distinct address tokens (e.g. street name + house
        # number) so a bare city-name mention alone can't count as a match.
        if len(overlap) < 2:
            continue
        host_tokens = _tokens(parsed.hostname or "")
        host_coverage = len(address_tokens & host_tokens) / len(address_tokens)
        if _is_weak_official_candidate(parsed, host_coverage=host_coverage):
            continue
        blob = f"{item.title} {item.snippet}".casefold()
        score = len(overlap) * 12
        if country_name.casefold() in blob:
            score += 8
        if city and city.casefold() in blob:
            score += 5
        if parsed.path in {"", "/"}:
            score += 10
        score += max(0, 6 - max(item.rank, 1))
        if score < 35:
            continue
        scored.append(
            OfficialWebsiteCandidate(
                url=_homepage_url(parsed),
                score=score,
                source_url=item.url,
                title=item.title[:500],
            )
        )
    if not scored:
        return None
    scored.sort(key=lambda candidate: (-candidate.score, candidate.url))
    if len(scored) > 1 and scored[0].score - scored[1].score < 8:
        return None
    return scored[0]


def _verification_tier(place: MapsPlace) -> str:
    if not place.is_relevant:
        return "excluded"
    confidence = float(place.confidence_score or 0)
    if confidence >= CONFIDENCE_VERIFIED_MIN:
        return "verified"
    if confidence >= CONFIDENCE_EXPORT_MIN:
        return "flagged"
    return "excluded"


def _has_export_location(place: MapsPlace) -> bool:
    return bool((place.formatted_address or "").strip())


def has_street_address(address: str | None) -> bool:
    """True when the address carries a real street reference.

    Google substitutes a Plus Code grid reference for the street portion when a
    place has none, so ``"QW2G+35C, Chéraga"`` is a city name and a coordinate —
    not an address we can verify or export. A Plus Code alongside a real street
    (``"7VQ8+8J9, Rue DES FRÈRES BEN FISSA"``) still counts.
    """
    remaining = [
        part.strip()
        for part in (address or "").split(",")
        if part.strip() and not PLUS_CODE_RE.match(part.strip().split(" ", 1)[0])
    ]
    if not remaining:
        return False
    # A lone trailing city/commune name is what's left of a Plus-Code-only
    # address; a usable address needs more than a single bare token.
    return len(remaining) > 1 or len(remaining[0].split()) > 1


def is_generic_facility_name(name: str | None) -> bool:
    """True when the name is only category words and carries no identity.

    Rejects user-submitted pins labelled ``"désintoxication"`` or
    ``"Centre De Désintoxication"`` while keeping any name with a distinguishing
    token such as ``"Abidat centre anti drogues"`` or ``"CERTA-TO"``.
    """
    # Fold Latin accents so "désintoxication" matches the ASCII category word.
    folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", (name or "").lower())
        if not unicodedata.combining(char)
    )
    cleaned = re.sub(r"[^\w\s\u0600-\u06ff]+", " ", folded)
    tokens = [
        token
        for token in cleaned.split()
        if token not in GENERIC_NAME_STOPWORDS and not token.isdigit()
    ]
    if not tokens:
        return True
    return all(token in GENERIC_NAME_WORDS for token in tokens)


def has_contact_channel(place: MapsPlace | Any) -> bool:
    """True when the place has a phone number and/or a website URL.

    Either channel alone is enough. Facebook and other raw Places websites count
    even when they were not promoted to ``official_website``.
    """
    phone = (getattr(place, "international_phone_number", None) or "").strip()
    website = (
        getattr(place, "official_website", None)
        or getattr(place, "raw_website", None)
        or ""
    ).strip()
    return bool(phone or website)


def _export_location(place: MapsPlace, *, country_name: str) -> str:
    address = (place.formatted_address or "").strip()
    if address:
        return address
    parts = [p for p in (place.city_name, place.region_name, country_name) if p]
    return ", ".join(parts)


def _has_enriched_addictions(place: MapsPlace) -> bool:
    addictions = place.addictions_treated or []
    return bool(addictions and any(str(item).strip() for item in addictions))


def _export_eligible(place: MapsPlace) -> bool:
    if not place.is_relevant:
        return False
    if not (place.canonical_name or place.raw_name or "").strip():
        return False
    if not _has_export_location(place):
        return False
    confidence = float(place.confidence_score or 0)
    if confidence < CONFIDENCE_EXPORT_MIN:
        return False
    if get_settings().maps_census_enrichment_enabled:
        return _has_enriched_addictions(place)
    return True


def _format_addictions(place: MapsPlace) -> str:
    addictions = [str(item).strip() for item in (place.addictions_treated or []) if str(item).strip()]
    return ", ".join(addictions) if addictions else EXPORT_ADDICTIONS_PLACEHOLDER


def _format_languages(place: MapsPlace) -> str:
    languages = [str(item).strip() for item in (place.languages_spoken or []) if str(item).strip()]
    return ", ".join(languages) if languages else EXPORT_NOT_SPECIFIED


def _export_tier_matches(place: MapsPlace, tier: str) -> bool:
    if tier == "all":
        return True
    return _verification_tier(place) == tier


def normalized_export_website(place: MapsPlace) -> str | None:
    """Public: the website shown in exports, prefixed to a clickable URL."""
    website = (place.official_website or place.raw_website or "").strip()
    if not website:
        return None
    if not website.lower().startswith(("http://", "https://")):
        website = f"https://{website}"
    return website


def _export_csv_row(place: MapsPlace, *, country_name: str) -> list[str]:
    name = (place.canonical_name or place.raw_name or "").strip()
    website = normalized_export_website(place) or ""
    phone = (place.international_phone_number or "").strip()
    price = (place.treatment_price or "").strip() or EXPORT_CONTACT_PRICING
    return [
        name,
        _format_addictions(place),
        _export_location(place, country_name=country_name),
        _format_languages(place),
        website if website else EXPORT_NOT_SPECIFIED,
        phone if phone else EXPORT_NOT_SPECIFIED,
        price,
    ]


def _cell_item(cell: MapsCensusCell) -> MapsCensusCellItem:
    return MapsCensusCellItem(
        id=cell.id,
        region_name=cell.region_name,
        city_name=cell.city_name,
        query_text=cell.query_text,
        query_family=cell.query_family,
        query_language=cell.query_language,
        status=cell.status.value if hasattr(cell.status, "value") else str(cell.status),
        places_found=cell.places_found,
        error_message=cell.error_message,
        completed_at=cell.completed_at,
        started_at=cell.started_at,
        pages_fetched=cell.pages_fetched,
        raw_results_found=cell.raw_results_found,
        unique_results_found=cell.unique_results_found,
        duplicates_found=cell.duplicates_found,
        next_page_available=cell.next_page_available,
        result_cap_reached=cell.result_cap_reached,
        pagination_error=cell.pagination_error,
        parent_cell_id=cell.parent_cell_id,
        expansion_reason=cell.expansion_reason,
        expansion_depth=cell.expansion_depth,
        attempt_count=cell.attempt_count,
        last_error=cell.last_error,
        next_retry_at=cell.next_retry_at,
        new_unique_places=cell.new_unique_places,
        new_plausible_places=cell.new_plausible_places,
    )


def _run_summary(run: MapsCensusRun) -> MapsCensusRunSummary:
    return MapsCensusRunSummary(
        id=run.id,
        country_code=run.country_code,
        country_name=run.country_name,
        status=run.status.value if hasattr(run.status, "value") else str(run.status),
        error_message=run.error_message,
        cells_total=run.cells_total,
        cells_completed=run.cells_completed,
        places_found=run.places_found,
        places_classified_relevant=run.places_classified_relevant,
        places_with_website=run.places_with_website,
        places_enriched=run.places_enriched,
        enrichment_refresh_completed_at=run.enrichment_refresh_completed_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        hero_image_url=run.hero_image_url,
    )


def _place_item(place: MapsPlace) -> MapsPlaceItem:
    verification_verdict = place.verification_verdict or derive_legacy_verification_verdict(
        place.lifecycle_status
    )
    return MapsPlaceItem(
        id=place.id,
        google_place_id=place.google_place_id,
        canonical_name=place.canonical_name,
        place_types=place.place_types or [],
        formatted_address=place.formatted_address,
        city_name=place.city_name,
        region_name=place.region_name,
        latitude=place.latitude,
        longitude=place.longitude,
        international_phone_number=place.international_phone_number,
        raw_website=place.raw_website,
        official_website=place.official_website,
        website_source=place.website_source,
        lifecycle_status=place.lifecycle_status or MapsLifecycleStatus.DISCOVERED.value,
        client_eligibility=place.client_eligibility or MapsClientEligibility.EXCLUDED.value,
        operator_type=place.operator_type,
        ownership_status=place.ownership_status,
        funding_type=place.funding_type,
        facility_type=place.facility_type,
        care_setting=place.care_setting,
        organization_scope=place.organization_scope,
        operator_name=place.operator_name,
        contact_status=place.contact_status,
        addiction_focus_confirmed=place.addiction_focus_confirmed,
        medical_detox=place.medical_detox,
        residential_accommodation=place.residential_accommodation,
        classification_confidence=(
            float(place.classification_confidence) if place.classification_confidence is not None else None
        ),
        classification_evidence=place.classification_evidence,
        discovery_sources=list(place.discovery_sources or []),
        is_relevant=place.is_relevant,
        relevance_reason=place.relevance_reason,
        confidence_score=float(place.confidence_score) if place.confidence_score is not None else None,
        discovered_via_query=place.discovered_via_query,
        has_photo=bool(place.photo_reference),
        verification_tier=_verification_tier(place),
        export_eligible=_export_eligible(place),
        enrichment_status=place.enrichment_status or "pending",
        addictions_treated=list(place.addictions_treated or []),
        languages_spoken=list(place.languages_spoken or []),
        treatment_price=place.treatment_price,
        verification_verdict=verification_verdict,
        verification_reason=place.verification_reason,
        verification_source_url=place.verification_source_url,
    )


def _normalize_website_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("decisions") or raw.get("results") or raw.get("websites") or []
    else:
        items = []
    if not isinstance(items, list):
        items = []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("website") or item.get("official_website")
        if isinstance(url, str):
            url = url.strip() or None
        else:
            url = None
        normalized.append(
            {
                "place_id": item.get("place_id") or item.get("id"),
                "url": url,
                "reason": str(item.get("reason") or item.get("explanation") or "")[:2000],
                "confidence": item.get("confidence", 0.5),
                "page_name": str(
                    item.get("page_name") or item.get("pageName") or item.get("title") or ""
                )[:300],
            }
        )
    return {"decisions": normalized}


def _accepted_llm_website_url(
    url: str | None,
    *,
    candidates: list[SearchProviderResult],
) -> str | None:
    """Accept an LLM pick only if it maps to a non-blocked candidate host homepage."""
    if not url:
        return None
    parsed = _safe_url(url)
    if parsed is None:
        return None
    host = (parsed.hostname or "").casefold()
    if not host:
        return None
    allowed_hosts = set()
    for candidate in candidates:
        if _is_rejected_result(candidate):
            continue
        candidate_parsed = _safe_url(candidate.url)
        if candidate_parsed is None or not candidate_parsed.hostname:
            continue
        allowed_hosts.add(candidate_parsed.hostname.casefold())
    if host not in allowed_hosts:
        return None
    homepage = _homepage_url(parsed)
    probe = SearchProviderResult(rank=1, url=homepage, title="", snippet="")
    if _is_rejected_result(probe):
        return None
    return homepage


async def _accepted_direct_llm_website_url(
    url: str | None,
    *,
    confidence: float,
    page_name: str = "",
    facility_name: str | None = None,
) -> str | None:
    """Accept a direct LLM website find after blocklist and optional HTTP checks.

    A Facebook page is accepted as a fallback only when the page's own name
    shares a distinguishing token with the facility name, so a real page for a
    *different* clinic can never be attached to this one.
    """
    settings = get_settings()
    if not url or confidence < settings.maps_census_website_llm_min_confidence:
        return None
    parsed = _safe_url(url)
    if parsed is None:
        return None
    if _is_facebook_url(url):
        return _accepted_social_page_url(url, page_name=page_name, facility_name=facility_name)
    homepage = _homepage_url(parsed)
    probe = SearchProviderResult(rank=1, url=homepage, title="", snippet="")
    if _is_rejected_result(probe):
        return None
    if settings.maps_census_website_llm_verify_reachable:
        if not await _verify_website_reachable(homepage):
            return None
    return homepage


def _is_facebook_url(url: str | None) -> bool:
    """True for genuine Facebook hosts, excluding platform (non-page) paths."""
    parsed = _safe_url(url or "")
    if parsed is None:
        return False
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host != "facebook.com" and not host.endswith(".facebook.com"):
        return False
    segments = [segment for segment in (parsed.path or "").split("/") if segment]
    if segments and segments[0].casefold() in FACEBOOK_NON_PAGE_SEGMENTS:
        return False
    return bool(segments) or bool(parsed.query)


def _accepted_social_page_url(
    url: str,
    *,
    page_name: str,
    facility_name: str | None,
) -> str | None:
    """Keep a social page only when its name identifies this facility.

    The URL path is included in the comparison so a vanity handle such as
    ``/EhsFernaneOuedAissi`` can prove identity when no page name came back.
    """
    identity = _distinctive_tokens(facility_name)
    if not identity:
        return None
    parsed = _safe_url(url)
    handle = (parsed.path or "").replace("/", " ") if parsed is not None else ""
    claimed = _distinctive_tokens(f"{page_name} {handle}")
    if not claimed & identity:
        return None
    return url


def _distinctive_tokens(text: str | None) -> set[str]:
    """Tokens that identify a specific facility, minus generic category words.

    Social vanity handles glue words together ("CliniqueLilasAlger"), so split
    on case transitions before folding or the whole handle becomes one token.
    """
    split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text or "")
    folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", split.lower())
        if not unicodedata.combining(char)
    )
    cleaned = re.sub(r"[^\w\s\u0600-\u06ff]+", " ", folded)
    return {
        token
        for token in cleaned.split()
        if len(token) > 2
        and not token.isdigit()
        and token not in GENERIC_NAME_WORDS
        and token not in GENERIC_NAME_STOPWORDS
    }


async def _verify_website_reachable(url: str) -> bool:
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(8.0, connect=4.0),
            headers={"User-Agent": "MultiAI-MapsCensus/1.0"},
        ) as client:
            response = await client.head(url)
            if response.status_code >= 400 or response.status_code < 200:
                response = await client.get(url)
            return 200 <= response.status_code < 400
    except Exception:  # noqa: BLE001
        return False


def _normalize_relevance_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("decisions") or raw.get("places") or raw.get("results") or []
    else:
        items = []
    if not isinstance(items, list):
        items = []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "place_id": item.get("place_id") or item.get("id"),
                "decision": item.get("decision") or item.get("status") or "",
                "is_relevant": bool(item.get("is_relevant") or item.get("relevant")),
                "reason": item.get("reason") or item.get("explanation") or "",
                "confidence": item.get("confidence", 0.5),
            }
        )
    return {"decisions": normalized}


def _is_explicitly_unrelated(decision: MapsRelevanceDecision) -> bool:
    normalized_decision = (decision.decision or "").strip().casefold()
    if normalized_decision in {"unrelated", "irrelevant", "not_relevant", "excluded"}:
        return True
    if decision.is_relevant:
        return False
    reason = (decision.reason or "").casefold()
    return any(
        marker in reason
        for marker in (
            "unrelated",
            "wrong country",
            "outside",
            "hotel",
            "spa",
            "pharmacy",
            "school",
            "university",
            "college",
            "campus",
            "gym",
            "fitness",
            "physical rehab",
            "physical therapy",
            "physiotherapy",
            "recycling",
            "waste",
            "wastewater",
            "waste water",
            "waste management",
            "industrial",
            "factory",
            "educational rehab",
            "educational rehabilitation",
            "vocational rehab",
            "closed",
            "not a facility",
            "directory listing",
        )
    )


def _lifecycle_from_classification(decision: MapsRelevanceDecision) -> MapsLifecycleStatus:
    if (decision.reason or "").strip().casefold() in CLASSIFICATION_FALLBACK_REASONS:
        return MapsLifecycleStatus.DISCOVERED
    confidence = float(decision.confidence or 0)
    # High-confidence noise-only unrelated reasons must not inflate plausible
    # saturation (Phase 2 gap #5). Mid-confidence ``decision=unrelated`` still
    # maps to ``needs_review`` via the confidence band below.
    if not decision.is_relevant and confidence >= 0.75:
        reason = (decision.reason or "").casefold()
        if any(
            marker in reason
            for marker in (
                "unrelated",
                "wrong country",
                "outside",
                "hotel",
                "spa",
                "pharmacy",
                "school",
                "not a facility",
                "not a rehab",
            )
        ):
            return MapsLifecycleStatus.UNRELATED
    if confidence >= 0.75:
        return MapsLifecycleStatus.PLAUSIBLE
    if confidence >= 0.45:
        return MapsLifecycleStatus.NEEDS_REVIEW
    if _is_explicitly_unrelated(decision):
        return MapsLifecycleStatus.UNRELATED
    return MapsLifecycleStatus.NEEDS_REVIEW


def _discovery_finished(run: MapsCensusRun | Any) -> bool:
    """True once Google discovery has finished, even if a later refresh left status=running."""
    if getattr(run, "completed_at", None) is not None:
        return True
    status = getattr(run, "status", None)
    status_value = status.value if hasattr(status, "value") else status
    return status_value == MapsCensusStatus.COMPLETED.value and (getattr(run, "places_found", 0) or 0) > 0


def _set_place_lifecycle(
    place: MapsPlace | Any,
    lifecycle_status: MapsLifecycleStatus,
    *,
    reason: str | None = None,
) -> None:
    place.lifecycle_status = lifecycle_status.value
    place.is_relevant = derive_is_relevant(lifecycle_status.value)
    place.verification_verdict = derive_legacy_verification_verdict(lifecycle_status.value)
    if reason:
        place.relevance_reason = reason[:300]


def _contact_status_for_place(place: MapsPlace | Any) -> str:
    phone = (getattr(place, "international_phone_number", None) or "").strip()
    website = (
        getattr(place, "official_website", None) or getattr(place, "raw_website", None) or ""
    ).strip()
    if phone and website:
        return MapsContactStatus.COMPLETE.value
    if phone:
        return MapsContactStatus.PHONE_ONLY.value
    if website:
        return MapsContactStatus.WEBSITE_ONLY.value
    return MapsContactStatus.MISSING.value


maps_census_service = MapsCensusService()


async def run_maps_census_job(ctx: dict, run_id: str) -> None:
    """ARQ entrypoint: run a queued Maps census to completion."""
    del ctx
    logger.info("maps_census_job_entered", extra={"run_id": run_id})
    try:
        await maps_census_service.run_census(None, run_id=run_id)
    except Exception:  # noqa: BLE001
        logger.exception("maps_census_job_failed", extra={"run_id": run_id})
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            run = await db.get(MapsCensusRun, run_id)
            if run is not None and run.status != MapsCensusStatus.COMPLETED:
                run.status = MapsCensusStatus.FAILED
                run.error_message = "Unexpected error during Maps census execution."
                run.completed_at = datetime.now(UTC)
                await db.commit()


async def refresh_maps_census_websites_job(ctx: dict, run_id: str) -> None:
    """ARQ entrypoint: backfill missing official websites for a completed run."""
    del ctx
    logger.info("maps_census_refresh_websites_job_entered", extra={"run_id": run_id})
    try:
        await maps_census_service.run_website_refresh(None, run_id=run_id)
    except Exception:  # noqa: BLE001
        logger.exception("maps_census_refresh_websites_job_failed", extra={"run_id": run_id})
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            run = await db.get(MapsCensusRun, run_id)
            if run is not None and run.status != MapsCensusStatus.COMPLETED:
                run.status = MapsCensusStatus.FAILED
                run.error_message = "Unexpected error while refreshing missing websites."
                run.completed_at = datetime.now(UTC)
                await db.commit()


async def run_maps_census_enrichment_job(ctx: dict, run_id: str) -> None:
    """ARQ entrypoint: crawl facility websites and extract export columns."""
    del ctx
    from app.db.session import AsyncSessionLocal
    from app.services.scraping.maps_enrichment_progress import (
        ENRICHMENT_STATUS_FAILED_RETRYABLE,
        ENRICHMENT_STATUS_RUNNING,
        persist_enrichment_progress,
    )
    from app.services.scraping.maps_place_enrichment_service import maps_place_enrichment_service

    logger.info("maps_census_enrichment_job_entered", extra={"run_id": run_id})
    await persist_enrichment_progress(
        AsyncSessionLocal,
        run_id=run_id,
        phase="job_start",
        enrichment_status=ENRICHMENT_STATUS_RUNNING,
    )
    try:
        result = await maps_place_enrichment_service.enrich_run(None, run_id=run_id)
        if isinstance(result, dict) and result.get("failed_retryable"):
            logger.error(
                "maps_census_enrichment_job_failed_retryable",
                extra={"run_id": run_id, "error": result.get("error")},
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("maps_census_enrichment_job_failed", extra={"run_id": run_id})
        await persist_enrichment_progress(
            AsyncSessionLocal,
            run_id=run_id,
            phase="job_fatal",
            enrichment_status=ENRICHMENT_STATUS_FAILED_RETRYABLE,
            error=str(exc),
            extra={"enrichment_fatal": True},
        )


async def recover_maps_census_runs(ctx: dict) -> None:
    """Requeue Maps census runs whose worker died mid-flight (stale heartbeat).

    Also auto-resumes enrichment on discovery-completed campaigns when:
    - enrichment_refresh_completed_at is null
    - pending places remain
    - running == 0
    - enrichment heartbeat is stale
    without requiring a destructive Recover reset.
    """
    del ctx
    from datetime import timedelta

    from app.db.session import AsyncSessionLocal
    from app.services.scraping.maps_enrichment_progress import (
        ENRICHMENT_STATUS_STALE_FAILED,
        MAX_ENRICHMENT_AUTO_RESUMES,
        count_places_by_enrichment_status,
        parse_enrichment_heartbeat,
        persist_enrichment_progress,
    )

    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    stale_discovery_ids: list[str] = []
    enrichment_snapshots: list[dict] = []
    async with AsyncSessionLocal() as db:
        stale = (
            await db.execute(
                select(MapsCensusRun).where(
                    MapsCensusRun.status == MapsCensusStatus.RUNNING,
                    (MapsCensusRun.heartbeat_at.is_(None)) | (MapsCensusRun.heartbeat_at < cutoff),
                )
            )
        ).scalars().all()
        stale_discovery_ids = [run.id for run in stale]

        enrichment_candidates = (
            await db.execute(
                select(MapsCensusRun).where(
                    MapsCensusRun.status == MapsCensusStatus.COMPLETED,
                    MapsCensusRun.enrichment_refresh_completed_at.is_(None),
                    MapsCensusRun.enrichment_refresh_attempts > 0,
                )
            )
        ).scalars().all()
        for run in enrichment_candidates:
            enrichment_snapshots.append(
                {
                    "id": run.id,
                    "heartbeat": parse_enrichment_heartbeat(run),
                    "updated_at": run.updated_at,
                    "auto_resumes": int(
                        (run.processing_state or {}).get("enrichment_auto_resume_count") or 0
                    ),
                }
            )
        await db.commit()

    for run_id in stale_discovery_ids:
        logger.warning("maps_census_recovering_stale_run", extra={"run_id": run_id})
        asyncio.create_task(run_maps_census_job({}, run_id))

    for snap in enrichment_snapshots:
        heartbeat = snap["heartbeat"]
        if heartbeat is not None and heartbeat >= cutoff:
            continue
        updated = snap["updated_at"]
        if heartbeat is None and updated is not None:
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            if updated >= cutoff:
                continue
        auto_resumes = int(snap["auto_resumes"] or 0)
        if auto_resumes >= MAX_ENRICHMENT_AUTO_RESUMES:
            continue
        run_id = snap["id"]
        counts = await count_places_by_enrichment_status(AsyncSessionLocal, run_id=run_id)
        pending = int(counts.get(MapsPlaceEnrichmentStatus.PENDING.value, 0))
        running = int(counts.get(MapsPlaceEnrichmentStatus.RUNNING.value, 0))
        if pending <= 0 or running != 0:
            continue
        logger.warning(
            "maps_census_auto_resuming_stale_enrichment",
            extra={"run_id": run_id, "pending": pending, "auto_resumes": auto_resumes},
        )
        await persist_enrichment_progress(
            AsyncSessionLocal,
            run_id=run_id,
            phase="stale_watchdog",
            enrichment_status=ENRICHMENT_STATUS_STALE_FAILED,
            pending_count=pending,
            running_count=running,
            error="enrichment heartbeat stale with pending work; auto-requeue once",
            extra={
                "enrichment_auto_resume_count": auto_resumes + 1,
                "enrichment_stale_watchdog_at": datetime.now(UTC).isoformat(),
            },
        )
        asyncio.create_task(run_maps_census_enrichment_job({}, run_id))


async def auto_refresh_maps_census_websites(ctx: dict) -> None:
    """Periodically retry the missing-website search for completed runs that still
    have relevant facilities without an official website — fully automatic, no
    manual "find missing websites" click required.
    """
    del ctx
    settings = get_settings()
    if not settings.maps_census_auto_website_refresh_enabled:
        return
    from datetime import timedelta

    from app.db.session import AsyncSessionLocal

    cooldown = timedelta(hours=max(1, settings.maps_census_auto_website_refresh_cooldown_hours))
    max_attempts = max(1, settings.maps_census_auto_website_refresh_max_attempts)
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as db:
        candidates = (
            await db.execute(
                select(MapsCensusRun).where(
                    MapsCensusRun.status == MapsCensusStatus.COMPLETED,
                    MapsCensusRun.places_classified_relevant > MapsCensusRun.places_with_website,
                    MapsCensusRun.website_refresh_attempts < max_attempts,
                )
            )
        ).scalars().all()
        due_run_ids: list[str] = []
        for run in candidates:
            last_attempt = run.website_refresh_completed_at or run.completed_at
            if last_attempt is not None and last_attempt.tzinfo is None:
                last_attempt = last_attempt.replace(tzinfo=UTC)
            if last_attempt is not None and now - last_attempt < cooldown:
                continue
            run.status = MapsCensusStatus.RUNNING
            run.heartbeat_at = now
            due_run_ids.append(run.id)
        await db.commit()

    for run_id in due_run_ids:
        logger.info("maps_census_auto_refresh_websites", extra={"run_id": run_id})
        asyncio.create_task(refresh_maps_census_websites_job({}, run_id))
