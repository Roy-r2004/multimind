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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, delete, func, not_, or_, select
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
    MapsCountryProfileStatus,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
    MapsRegionSaturationStatus,
)
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider, get_provider_registry
from app.schemas.api import (
    MapsCampaignActionResponse,
    MapsCensusCellItem,
    MapsCensusRunDetail,
    MapsCensusRunSummary,
    MapsPlaceItem,
    MapsPlaceListResponse,
    MapsPlaceUpdate,
    PaginatedMeta,
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
from app.services.scraping.maps_manual_overrides import (
    EDITABLE_EXPORT_FIELDS,
    add_override_fields,
    set_unless_overridden,
)
from app.services.scraping.maps_cell_runner import (
    DEFAULT_RETRY_BASE_SECONDS,
    RunExecutionSuperseded,
    claim_cell_by_id,
    ensure_run_execution_current,
    fail_cell_for_retry,
    heartbeat_cell,
    heartbeat_run,
    increment_run_cells_total,
    is_campaign_paused,
    is_run_cancelled,
    reconcile_run_cell_counters,
    recover_stale_running_cells,
)
from app.services.scraping.maps_cell_subdivision import ChildCellSpec, subdivide_cell
from app.services.scraping.maps_circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitState,
    LLMBudgetExhaustedError,
    LLMCallBudget,
)
from app.services.scraping.maps_country_profile_service import maps_country_profile_service
from app.services.scraping.maps_eligibility import (
    compute_client_eligibility,
    derive_is_relevant,
    derive_legacy_verification_verdict,
)
from app.services.scraping.maps_enrichment_progress import (
    ENRICHMENT_STATUS_STALE_FAILED,
    enrichment_status_from_run,
)
from app.services.scraping.maps_external_discovery import maps_external_discovery_coordinator
from app.services.scraping import maps_geo_boundaries, maps_geo_planner_service
from app.services.scraping.maps_geo_regions import resolve_geo_anchor, resolve_geo_anchor_detailed
from app.services.scraping.maps_observability import (
    log_budget_exceeded,
    log_cell_processing,
    log_circuit_breaker_event,
    log_llm_call,
    log_stage_end,
    log_stage_start,
    timed_operation,
)
from app.services.scraping.maps_grid_planner import (
    MapsGridCell,
    MapsGridPlanningError,
    maps_grid_planner,
)
from app.services.scraping import gosom_health_monitor
from app.services.scraping.maps_places_client import (
    DISCOVERY_PROVIDERS,
    PlacesProviderError,
    PlacesTimeoutError,
    create_places_client,
    resolve_discovery_provider,
)
from app.services.scraping.maps_quota_tracker import (
    MapsQuotaTracker,
    merge_quota_metrics,
    set_active_tracker,
)
from app.services.scraping.maps_saturation import (
    CellWindowResult,
    compute_window_metrics,
    decide_region_saturation,
    is_coverage_evidence,
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
# First gosom full-coverage wave. Saturation is checked after this batch, so a
# small geography can stop here instead of planning toward the 1500 ceiling.
MAPS_CENSUS_ADAPTIVE_SEED_CELLS = 30
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
        self,
        db: AsyncSession,
        auth: AuthContext,
        country_code: str,
        state_code: str | None = None,
        state_name: str | None = None,
        discovery_provider: str | None = None,
    ) -> MapsCensusRunDetail:
        settings = get_settings()
        provider = (discovery_provider or "").strip().lower() or settings.maps_discovery_provider
        if provider not in DISCOVERY_PROVIDERS:
            raise ValidationError("discovery_provider must be 'google_places' or 'gosom'.")
        if provider == "gosom":
            if not (settings.gosom_base_url or "").strip():
                raise ValidationError("Gosom Maps census is not configured (GOSOM_BASE_URL).")
        elif not settings.google_places_enabled:
            raise ValidationError("Google Places Maps census is disabled.")
        country = resolve_country(country_code)
        clean_state_code = state_code.strip().upper() if state_code and state_code.strip() else None
        clean_state_name = state_name.strip() if state_name and state_name.strip() else None
        run = MapsCensusRun(
            organization_id=auth.org_id,
            created_by=auth.user.id,
            country_code=country.code,
            country_name=country.name,
            state_code=clean_state_code,
            state_name=clean_state_name,
            discovery_provider=provider,
            status=MapsCensusStatus.QUEUED,
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        await db.commit()
        await self._enqueue(run_id)
        asyncio.create_task(
            self._fetch_hero_image(run_id, country.name, state_name=clean_state_name)
        )
        return await self.get_run(db, auth, run_id)

    async def _fetch_hero_image(
        self, run_id: str, country_name: str, state_name: str | None = None
    ) -> None:
        """Best-effort Pexels lookup for a country/state hero photo — never raises,
        never blocks or fails the census run if Pexels is slow or unavailable.
        """
        try:
            client = create_pexels_client()
            search_query = (
                f"{state_name} {country_name} landscape"
                if state_name
                else f"{country_name} landscape"
            )
            url = await client.search_landscape(search_query)
            if not url and state_name:
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
        # One grouped query for every run's accepted-facility count rather
        # than one query per run (N+1) — same authoritative predicate the
        # Phase 2 tab and Excel export use.
        counts_by_run: dict[str, int] = {}
        awaiting_enrichment_by_run: dict[str, int] = {}
        if rows:
            run_ids = [run.id for run in rows]
            count_rows = await db.execute(
                select(MapsPlace.run_id, func.count())
                .where(MapsPlace.run_id.in_(run_ids), _accepted_facility_filter())
                .group_by(MapsPlace.run_id)
            )
            counts_by_run = {run_id: int(count) for run_id, count in count_rows.all()}
            # Same N+1-avoidance pattern for _phase2_status's
            # "awaiting_enrichment" check — one grouped query for every run
            # rather than count_keeps_awaiting_enrichment called once per row.
            awaiting_rows = await db.execute(
                select(MapsPlace.run_id, func.count())
                .where(
                    MapsPlace.run_id.in_(run_ids),
                    MapsPlace.keep_drop_decision.in_(["keep", "review"]),
                    MapsPlace.enrichment_status.in_(
                        [
                            MapsPlaceEnrichmentStatus.PENDING.value,
                            MapsPlaceEnrichmentStatus.RUNNING.value,
                        ]
                    ),
                )
                .group_by(MapsPlace.run_id)
            )
            awaiting_enrichment_by_run = {
                run_id: int(count) for run_id, count in awaiting_rows.all()
            }
        return [
            _run_summary(
                run,
                accepted_facilities_count=counts_by_run.get(run.id, 0),
                keeps_awaiting_enrichment=awaiting_enrichment_by_run.get(run.id, 0),
            )
            for run in rows
        ]

    async def get_run(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCensusRunDetail:
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        accepted_count = int(
            await db.scalar(
                select(func.count())
                .select_from(MapsPlace)
                .where(MapsPlace.run_id == run_id, _accepted_facility_filter())
            )
            or 0
        )
        from app.services.scraping.maps_keep_drop_service import count_keeps_awaiting_enrichment

        keeps_awaiting_enrichment = await count_keeps_awaiting_enrichment(db, run_id=run_id)
        summary = _run_summary(
            run,
            accepted_facilities_count=accepted_count,
            keeps_awaiting_enrichment=keeps_awaiting_enrichment,
        )
        breakdown = await compute_phase2_breakdown(db, run_id=run_id)
        return MapsCensusRunDetail(**summary.model_dump(), phase2_breakdown=breakdown)

    async def get_run_live_stats(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> dict[str, any]:
        """Compute real-time stats from current database state (not cached)."""
        from datetime import UTC, datetime

        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)

        # Compute live counts from database
        places_found = (
            await db.execute(
                select(func.count()).where(
                    MapsPlace.run_id == run_id,
                ).select_from(MapsPlace)
            )
        ).scalar_one() or 0

        places_relevant = (
            await db.execute(
                select(func.count()).where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.is_relevant.is_(True),
                ).select_from(MapsPlace)
            )
        ).scalar_one() or 0

        places_classified = (
            await db.execute(
                select(func.count()).where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.lifecycle_status.isnot(None),
                ).select_from(MapsPlace)
            )
        ).scalar_one() or 0

        places_enriched = (
            await db.execute(
                select(func.count()).where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value,
                ).select_from(MapsPlace)
            )
        ).scalar_one() or 0

        places_dropped = places_found - places_relevant
        cells_completed = run.cells_completed or 0
        cells_total = run.cells_total or 1

        # Calculate progress percentages
        discovery_pct = (cells_completed / cells_total * 100) if cells_total > 0 else 0
        classification_pct = (places_classified / places_found * 100) if places_found > 0 else 0
        enrichment_pct = (places_enriched / places_relevant * 100) if places_relevant > 0 else 0

        # Calculate elapsed time
        elapsed_seconds = None
        if run.started_at:
            elapsed = datetime.now(UTC) - run.started_at
            elapsed_seconds = int(elapsed.total_seconds())

        return {
            "run_id": run_id,
            "status": run.status.value if hasattr(run.status, "value") else str(run.status),
            "places_found_live": int(places_found),
            "places_classified_live": int(places_classified),
            "places_relevant_live": int(places_relevant),
            "places_dropped_live": int(places_dropped),
            "places_enriched_live": int(places_enriched),
            "cells_completed_live": cells_completed,
            "cells_total": cells_total,
            "discovery_progress_pct": float(round(discovery_pct, 1)),
            "classification_progress_pct": float(round(classification_pct, 1)),
            "enrichment_progress_pct": float(round(enrichment_pct, 1)),
            "elapsed_seconds": elapsed_seconds,
            "started_at": run.started_at,
        }

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
        keep_drop_decision: str | None = None,
        accepted_only: bool = False,
        include_removed: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> MapsPlaceListResponse:
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)
        limit = max(1, min(limit, 1000))
        offset = max(0, offset)

        query = select(MapsPlace).where(MapsPlace.run_id == run_id)
        count_query = select(func.count()).select_from(MapsPlace).where(MapsPlace.run_id == run_id)
        if relevant_only:
            query = query.where(MapsPlace.is_relevant.is_(True))
            count_query = count_query.where(MapsPlace.is_relevant.is_(True))
        if with_website_only:
            query = query.where(MapsPlace.official_website.is_not(None))
            count_query = count_query.where(MapsPlace.official_website.is_not(None))
        if accepted_only:
            # Applied in SQL before LIMIT/OFFSET so the total stays
            # accurate under pagination — see _accepted_facility_filter.
            accepted_filter = _accepted_facility_filter()
            query = query.where(accepted_filter)
            count_query = count_query.where(accepted_filter)
        if client_eligibility is not None:
            eligibility_filter = MapsPlace.client_eligibility == client_eligibility.strip().lower()
            query = query.where(eligibility_filter)
            count_query = count_query.where(eligibility_filter)
        if lifecycle_status is not None:
            lifecycle_filter = MapsPlace.lifecycle_status == lifecycle_status.strip().lower()
            query = query.where(lifecycle_filter)
            count_query = count_query.where(lifecycle_filter)
        if keep_drop_decision is not None:
            keep_drop_filter = MapsPlace.keep_drop_decision == keep_drop_decision.strip().lower()
            query = query.where(keep_drop_filter)
            count_query = count_query.where(keep_drop_filter)
        if not include_removed:
            query = query.where(MapsPlace.manually_excluded_at.is_(None))
            count_query = count_query.where(MapsPlace.manually_excluded_at.is_(None))

        total = await db.scalar(count_query)
        rows = (
            await db.execute(query.order_by(MapsPlace.canonical_name).limit(limit).offset(offset))
        ).scalars().all()
        return MapsPlaceListResponse(
            items=[_place_item(place) for place in rows],
            meta=PaginatedMeta(total=int(total or 0), limit=limit, offset=offset),
        )

    async def exclude_place(
        self,
        db: AsyncSession,
        auth: AuthContext,
        run_id: str,
        place_id: str,
        *,
        reason: str | None = None,
    ) -> MapsPlaceItem:
        """Customer-facing Phase 1 'remove row' action.

        Delegates to the same review-action logic the admin dashboard uses
        (``mark_excluded``) — no duplicated business logic — but is reachable
        by any user who can view this run, not just org admins.
        """
        from app.schemas.api import MapsPlaceReviewRequest
        from app.services.scraping.maps_admin_service import maps_admin_service

        detail = await maps_admin_service.apply_review_action(
            db,
            auth,
            run_id,
            place_id,
            MapsPlaceReviewRequest(
                action="mark_excluded",
                reason=(reason or "Removed during Phase 1 review").strip() or "Removed during Phase 1 review",
            ),
        )
        return detail

    async def update_place(
        self,
        db: AsyncSession,
        auth: AuthContext,
        run_id: str,
        place_id: str,
        data: MapsPlaceUpdate,
    ) -> MapsPlaceItem:
        """Persist manual edits to client-facing export columns on a Maps place."""
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)

        place = await db.get(MapsPlace, place_id)
        if place is None or place.run_id != run_id:
            raise NotFoundError("Maps place", place_id)
        if place.manually_excluded_at is not None:
            raise ValidationError("Cannot edit a removed facility")

        updates = data.model_dump(exclude_unset=True)
        unknown = set(updates) - EDITABLE_EXPORT_FIELDS
        if unknown:
            raise ValidationError(f"Field(s) cannot be edited: {', '.join(sorted(unknown))}")

        string_limits = {
            "canonical_name": 512,
            "formatted_address": 512,
            "official_website": 512,
            "contact_email": 320,
            "international_phone_number": 64,
            "treatment_price": 512,
        }
        list_fields = {"addictions_treated", "languages_spoken"}

        for field_name, value in updates.items():
            if field_name == "canonical_name":
                text = (value or "").strip() if isinstance(value, str) else ""
                if not text:
                    raise ValidationError("canonical_name cannot be empty")
                place.canonical_name = text[:512]
                continue
            if field_name == "bed_count":
                place.bed_count = value
                continue
            if field_name in list_fields:
                if value is None:
                    setattr(place, field_name, None)
                    continue
                cleaned = [str(item).strip() for item in value if str(item).strip()]
                setattr(place, field_name, cleaned or None)
                continue
            if field_name in string_limits:
                if value is None or (isinstance(value, str) and not value.strip()):
                    setattr(place, field_name, None)
                else:
                    setattr(place, field_name, str(value).strip()[: string_limits[field_name]])

        if updates:
            add_override_fields(place, updates.keys())

        await db.commit()
        await db.refresh(place)
        return _place_item(place)

    async def advance_to_phase_2(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        """Customer-facing 'Proceed to Phase 2' action — triggers the strict
        keep/drop gate over the current (post-manual-removal) Phase 1 set.

        Delegates to the same logic the admin dashboard uses — no duplicated
        business logic — but is reachable by any user who can view this run.
        """
        from app.services.scraping.maps_admin_service import maps_admin_service

        return await maps_admin_service.run_keep_drop(db, auth, run_id)

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
        settings = get_settings()
        if settings.maps_census_small_batch_enrichment_enabled and settings.maps_census_two_phase_pipeline_enabled:
            await self._enqueue_job(
                "run_maps_enrichment_batch_job",
                run_id,
                inline_runner=lambda: run_maps_enrichment_batch_job({}, run_id),
            )
            return
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
        # Defense in depth alongside the keep_drop_decision gate now enforced
        # in build_classification_query/build_expensive_pipeline_query: a
        # request right as discovery finishes (the same instant run_census is
        # about to call run_keep_drop_pass itself) used to silently start
        # cascading over undecided candidates. The query gate already makes
        # that harmless (zero rows selected), but this also gives a clear
        # error instead of a false "enrichment started" response.
        from app.services.scraping.maps_keep_drop_service import count_undecided

        if await count_undecided(db, run_id=run_id) > 0:
            raise ValidationError(
                "Keep/drop gate has not finished judging every candidate yet — "
                "enrichment cannot be requested until it completes."
            )
        if run.status not in {
            MapsCensusStatus.COMPLETED,
            MapsCensusStatus.COMPLETED_WITH_WARNINGS,
        }:
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
        if run.status not in {
            MapsCensusStatus.COMPLETED,
            MapsCensusStatus.COMPLETED_WITH_WARNINGS,
        }:
            raise ValidationError("Only a completed Maps census run can refresh missing websites.")
        run.status = MapsCensusStatus.RUNNING
        run.heartbeat_at = datetime.now(UTC)
        await db.commit()
        await self._enqueue_refresh(run_id)
        return await self.get_run(db, auth, run_id)

    async def run_website_refresh(self, db: AsyncSession | None, *, run_id: str) -> dict[str, int]:
        session_factory = self._session_factory(db)
        tracker = MapsQuotaTracker()
        set_active_tracker(tracker)
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
        async with session_factory() as provider_db:
            discovery_provider = resolve_discovery_provider(
                await provider_db.get(MapsCensusRun, run_id)
            )

        # === PHASE 0: Initialize safety guards ===
        # Circuit breaker: prevent cascading failures from external APIs
        openrouter_breaker: CircuitBreaker | None = None
        google_breaker: CircuitBreaker | None = None
        sonar_breaker: CircuitBreaker | None = None
        gosom_breaker: CircuitBreaker | None = None
        if settings.maps_circuit_breaker_enabled:
            breaker_config = CircuitBreakerConfig(
                failure_threshold=settings.maps_circuit_breaker_failure_threshold,
                recovery_timeout_seconds=settings.maps_circuit_breaker_recovery_timeout_seconds,
            )
            openrouter_breaker = CircuitBreaker("openrouter", breaker_config)
            google_breaker = CircuitBreaker("google_places", breaker_config)
            sonar_breaker = CircuitBreaker("sonar", breaker_config)
            if discovery_provider == "gosom":
                gosom_breaker = CircuitBreaker(
                    "gosom",
                    CircuitBreakerConfig(
                        failure_threshold=settings.maps_gosom_circuit_breaker_failure_threshold,
                        recovery_timeout_seconds=settings.maps_gosom_circuit_breaker_recovery_timeout_seconds,
                    ),
                )

        # LLM call budget per-run (hard ceiling to prevent cost spirals)
        run_llm_budget = LLMCallBudget(
            run_id=run_id,
            cell_id=None,
            max_calls=settings.maps_run_llm_budget_max_calls,
        )

        # Campaign timeout tracking
        campaign_start_time = datetime.now(UTC)
        campaign_timeout_seconds = settings.maps_census_campaign_timeout_seconds
        timeout_warning_issued = False

        async with session_factory() as start_db:
            run = await start_db.get(MapsCensusRun, run_id)
            if run is None:
                return {"error": 1}
            if run.status in {
                MapsCensusStatus.COMPLETED,
                MapsCensusStatus.COMPLETED_WITH_WARNINGS,
                MapsCensusStatus.CANCELLED,
            }:
                status_str = run.status.value if hasattr(run.status, "value") else str(run.status)
                logger.info(
                    f"maps_census_skip_terminal_run run_id={run_id} status={status_str}",
                    extra={"run_id": run_id, "status": status_str, "event": "skip_terminal"},
                )
                return {"skipped": 1}

            cell_status_rows = (
                await start_db.execute(
                    select(MapsCensusCell.status, func.count())
                    .where(MapsCensusCell.run_id == run_id)
                    .group_by(MapsCensusCell.status)
                )
            ).all()
            cell_counts = {
                (status.value if hasattr(status, "value") else str(status)): int(count)
                for status, count in cell_status_rows
            }
            existing_cells = sum(cell_counts.values())
            active_cells = cell_counts.get(MapsCensusCellStatus.PENDING.value, 0) + cell_counts.get(
                MapsCensusCellStatus.IN_PROGRESS.value, 0
            )
            # Stale watchdog must never re-plan a finished discovery grid. A
            # replan failure previously flipped Algeria to FAILED and stamped
            # "Maps census grid planning failed." over a completed campaign.
            if existing_cells > 0 and active_cells == 0:
                # A crash between discovery and the post-discovery stages must
                # not strand the run here — only skip when keep/drop and
                # enrichment genuinely have nothing left to do.
                from app.services.scraping.maps_keep_drop_service import (
                    build_keep_drop_query,
                )

                undecided = int(
                    (
                        await start_db.execute(
                            select(func.count()).select_from(
                                build_keep_drop_query(run_id).subquery()
                            )
                        )
                    ).scalar_one()
                    or 0
                )
                pending_enrichment = int(
                    (
                        await start_db.execute(
                            select(func.count())
                            .select_from(MapsPlace)
                            .where(
                                MapsPlace.run_id == run_id,
                                MapsPlace.enrichment_status.in_(
                                    [
                                        MapsPlaceEnrichmentStatus.PENDING.value,
                                        MapsPlaceEnrichmentStatus.RUNNING.value,
                                    ]
                                ),
                            )
                        )
                    ).scalar_one()
                    or 0
                )
                if undecided == 0 and pending_enrichment == 0:
                    logger.info(
                        f"maps_census_skip_discovery_already_complete run_id={run_id} cells={existing_cells}",
                        extra={"run_id": run_id, "existing_cells": existing_cells, "event": "skip_complete"},
                    )
                    return {"skipped": 1, "cells": existing_cells}
                logger.info(
                    f"maps_census_resume_post_discovery run_id={run_id} undecided={undecided} pending_enrichment={pending_enrichment}",
                    extra={
                        "run_id": run_id,
                        "undecided": undecided,
                        "pending_enrichment": pending_enrichment,
                        "event": "resume_post_discovery",
                    },
                )

            # Fencing token: claimed unconditionally (last writer wins) so a
            # watchdog-triggered duplicate run_census() for this run_id can
            # never race this execution to completion — it stamps its own
            # token and this execution notices the mismatch (via
            # ensure_run_execution_current) and stops instead of also
            # finishing the campaign and enqueuing Phase 2.
            execution_token = uuid4().hex
            run.execution_token = execution_token
            run.status = MapsCensusStatus.RUNNING
            if run.started_at is None:
                run.started_at = datetime.now(UTC)
            run.heartbeat_at = datetime.now(UTC)
            await start_db.commit()
            country_code = run.country_code
            country_name = run.country_name
            state_code = run.state_code
            state_name = run.state_name
            resume_existing = existing_cells > 0
            existing_country_profile_status = run.country_profile_status

        # A watchdog-triggered resume (e.g. after Gosom wedges and the stale-
        # heartbeat recovery relaunches this exact job) must never repeat an
        # already-successful paid AI call. Without this guard, every
        # recovery cycle re-ran build_profile_for_run (a real sonar-pro
        # country-profile call) from scratch, even though the profile from
        # the run's first attempt was still perfectly valid — confirmed via
        # worker logs showing maps_country_profile.j2 re-rendered on a
        # watchdog resume of run 0afe4ca7-2dd3-47aa-a2cd-566e6b19b208.
        if existing_country_profile_status != MapsCountryProfileStatus.COMPLETED.value:
            try:
                await maps_country_profile_service.build_profile_for_run(db, run_id=run_id)
            except Exception:  # noqa: BLE001
                # Profiling is a best-effort enrichment stage — a bug here must
                # never take down the whole census. build_profile_for_run
                # already records its own failures on the run; this is just
                # a last-resort guard.
                logger.warning(
                    "maps_census_country_profile_stage_failed run_id=%s", run_id, exc_info=True
                )
        else:
            logger.info(
                "maps_census_country_profile_already_completed run_id=%s — skipping repeat profile call",
                run_id,
            )

        async with session_factory() as profile_db:
            run = await profile_db.get(MapsCensusRun, run_id)
            country_profile = run.country_profile if run is not None else None
            country_profile_status = run.country_profile_status if run is not None else None

        geo_anchor_detail = resolve_geo_anchor_detailed(
            country_code=country_code, state_code=state_code, state_name=state_name
        )
        geo_anchor = geo_anchor_detail["bounds"]
        # The raw verified polygon (not just its bbox) — resolved once per
        # run, read-only from the local cache, never from the network.
        # Used to (a) distribute real polygon-filtered sub-boxes across
        # region-wide seed cells instead of handing every one of them the
        # same full bounding rectangle, and (b) filter out subdivision
        # quadrants that don't actually overlap the region. ``None``
        # whenever no dataset-backed boundary applies — in which case
        # behavior is byte-for-byte the pre-existing single-bbox behavior.
        geo_polygon_record = None
        if geo_anchor_detail["polygon_available"]:
            try:
                geo_polygon_record = maps_geo_boundaries.resolve_region_boundary(
                    country_code=country_code, state_code=state_code, state_name=state_name
                )
            except Exception:  # noqa: BLE001
                geo_polygon_record = None
        # Persisted (not just logged) so this is visible to the API/UI, not
        # only to someone reading logs: every run should clearly state
        # whether it used real geographic cells or stayed text-only — never
        # leave that implicit. Dataset-backed resolutions also record their
        # source/license/boundary id; the static-table fallback and the
        # unresolved case are each reported distinctly rather than folded
        # into a single "resolved" boolean.
        async with session_factory() as geo_state_db:
            run = await geo_state_db.get(MapsCensusRun, run_id)
            if run is not None:
                state = dict(run.processing_state or {})
                state["geo_anchor"] = (
                    {
                        "resolved": True,
                        "country_code": country_code,
                        "state_code": state_code,
                        "bounds": geo_anchor,
                        "source": geo_anchor_detail["source"],
                        "source_license": geo_anchor_detail["source_license"],
                        "source_boundary_id": geo_anchor_detail["source_boundary_id"],
                        "polygon_available": geo_anchor_detail["polygon_available"],
                    }
                    if geo_anchor is not None
                    else {
                        "resolved": False,
                        "country_code": country_code,
                        "state_code": state_code,
                        "reason": "no trusted boundary or bounding box for this country/state — text-only search",
                    }
                )
                run.processing_state = state
                await geo_state_db.commit()
        if geo_anchor is not None:
            logger.info(
                "maps_census_geo_anchor_resolved run_id=%s country_code=%s state_code=%s",
                run_id,
                country_code,
                state_code,
            )
        else:
            # Honest fallback, logged rather than silently claimed as
            # geographic coverage: every cell in this run will carry no
            # viewport_bounds, so Gosom gets no lat/lon/zoom bias and the
            # search stays purely keyword-driven, same as before this fix.
            logger.info(
                "maps_census_geo_anchor_unavailable run_id=%s country_code=%s state_code=%s "
                "state_name=%s — falling back to text-only search, no geographic bias",
                run_id,
                country_code,
                state_code,
                state_name,
            )

        # Optional AI planning layer (Task 4) — disabled by default
        # (settings.maps_geo_ai_planner_enabled). Runs at most once per
        # run, never per cell, and only ever suggests priorities/terms
        # within the region geo_anchor already verified; it is never
        # consulted for, and cannot supply, coordinates. A failure here
        # never fails the run — plan_geo_strategy already catches
        # everything and returns a skipped_reason instead of raising.
        geo_plan_result = await maps_geo_planner_service.plan_geo_strategy(
            country_code=country_code,
            country_name=country_name,
            state_code=state_code,
            state_name=state_name,
            resolved_region_label=(
                f"{state_name or state_code or ''} {country_name or country_code or ''}".strip()
                if geo_anchor is not None
                else None
            ),
        )
        async with session_factory() as geo_plan_db:
            run = await geo_plan_db.get(MapsCensusRun, run_id)
            if run is not None:
                state = dict(run.processing_state or {})
                state["geo_ai_plan"] = {
                    "used_ai": geo_plan_result.used_ai,
                    "cached": geo_plan_result.cached,
                    "model": geo_plan_result.model,
                    "skipped_reason": geo_plan_result.skipped_reason,
                    "suggestion": (
                        geo_plan_result.suggestion.model_dump(mode="json")
                        if geo_plan_result.suggestion is not None
                        else None
                    ),
                }
                run.processing_state = state
                await geo_plan_db.commit()

        configured_cells = (
            settings.maps_census_max_cells_per_campaign or settings.maps_census_max_cells_per_run
        )
        gosom_full_coverage = (
            discovery_provider == "gosom"
            and settings.maps_census_coverage_mode == "full"
        )
        # 1500 is a ceiling. A lower campaign setting still wins, so a 50-cell
        # run remains possible. Full mode does not raise the seed to that ceiling.
        if gosom_full_coverage:
            max_cells_per_campaign = min(
                max(1, int(configured_cells)),
                max(1, int(settings.maps_gosom_full_max_cells)),
            )
            seed_max = min(MAPS_CENSUS_ADAPTIVE_SEED_CELLS, max_cells_per_campaign)
        else:
            max_cells_per_campaign = max(1, int(configured_cells))
            seed_max = min(MAPS_CENSUS_SEED_CELLS, max_cells_per_campaign)

        if resume_existing:
            # Self-heal cells_total/cells_completed before resuming: a worker
            # crash, a watchdog-triggered duplicate execution, or any other
            # prior write-path bug could have left them inconsistent with
            # the actual persisted cell rows. Resuming is the natural, cheap
            # checkpoint to reconcile — every resume starts from a
            # trustworthy baseline instead of carrying stale corruption
            # forward indefinitely.
            await reconcile_run_cell_counters(session_factory, run_id=run_id)

            # Resume pending/in-progress cells only — never call the seed planner
            # again (that path destroyed terminal campaigns via false failures).
            async with session_factory() as resume_db:
                existing_rows = (
                    await resume_db.execute(
                        select(MapsCensusCell).where(MapsCensusCell.run_id == run_id)
                    )
                ).scalars().all()
                seen_query_texts = {
                    (cell.query_text or "").strip().casefold()
                    for cell in existing_rows
                    if (cell.query_text or "").strip()
                }
                region_ids_by_name = {
                    cell.region_name: cell.region_id
                    for cell in existing_rows
                    if cell.region_id and cell.region_name
                }
                cell_ids = [
                    cell.id
                    for cell in existing_rows
                    if cell.status
                    in {MapsCensusCellStatus.PENDING, MapsCensusCellStatus.IN_PROGRESS}
                ]
                campaign_cells_used = len(existing_rows)
            seed_cells = []
        else:
            try:
                seed_cells = await maps_grid_planner.plan(
                    country_code=country_code,
                    country_name=country_name,
                    max_cells=seed_max,
                    country_profile=country_profile,
                    state_code=state_code,
                    state_name=state_name,
                    full_coverage=gosom_full_coverage,
                )
            except MapsGridPlanningError as exc:
                async with session_factory() as fail_db:
                    run = await fail_db.get(MapsCensusRun, run_id)
                    if run is not None:
                        # Never fail a campaign that already has persisted cells.
                        existing = int(
                            (
                                await fail_db.execute(
                                    select(func.count())
                                    .select_from(MapsCensusCell)
                                    .where(MapsCensusCell.run_id == run_id)
                                )
                            ).scalar_one()
                            or 0
                        )
                        if existing > 0:
                            logger.warning(
                                "maps_census_grid_plan_failed_ignored run_id=%s existing_cells=%s",
                                run_id,
                                existing,
                            )
                            return {"skipped": 1, "cells": existing}
                        run.status = MapsCensusStatus.FAILED
                        run.error_message = str(exc)[:2000]
                        run.completed_at = datetime.now(UTC)
                        await fail_db.commit()
                return {"error": 1}

        if not resume_existing:
            if not seed_cells:
                async with session_factory() as fail_db:
                    run = await fail_db.get(MapsCensusRun, run_id)
                    if run is not None:
                        run.status = MapsCensusStatus.FAILED
                        run.error_message = "Grid planning returned no cells."
                        run.completed_at = datetime.now(UTC)
                        await fail_db.commit()
                return {"error": 1}

            ai_added_cell_count = 0
            ai_suggestion = geo_plan_result.suggestion
            if ai_suggestion is not None and ai_suggestion.additional_search_terms:
                # The ONLY way the optional AI planner is allowed to
                # influence what actually gets searched: extra query TEXT,
                # reusing a region name the deterministic grid planner
                # already produced (never a new region, never city_name,
                # never coordinates). ``priority_areas`` is deliberately
                # NOT used here — an unverified place name from the model
                # has no gazetteer to check it against, so turning it into
                # a live search target would be exactly the kind of
                # hallucinated/out-of-scope area this task forbids. Plain
                # search terms carry no such risk: they're query
                # vocabulary, not a claim about geography.
                existing_regions = sorted({cell.region_name for cell in seed_cells if cell.region_name})
                extra_terms = ai_suggestion.additional_search_terms[:5]
                ai_added_cells = [
                    MapsGridCell(region_name=region_name, city_name=None, query_text=term)
                    for region_name in existing_regions
                    for term in extra_terms
                ]
                if ai_added_cells:
                    seed_cells = list(seed_cells) + ai_added_cells
                    ai_added_cell_count = len(ai_added_cells)
            if ai_added_cell_count:
                async with session_factory() as geo_plan_cells_db:
                    run = await geo_plan_cells_db.get(MapsCensusRun, run_id)
                    if run is not None:
                        state = dict(run.processing_state or {})
                        plan_state = dict(state.get("geo_ai_plan") or {})
                        plan_state["cells_added"] = ai_added_cell_count
                        state["geo_ai_plan"] = plan_state
                        run.processing_state = state
                        await geo_plan_cells_db.commit()

            region_ids_by_name = {}
            seen_query_texts = set()
            seed_cells = _cap_to_budget(
                seed_cells, max_cells_per_campaign=max_cells_per_campaign, already_persisted=0
            )
            cell_ids = await self._persist_cells(
                session_factory,
                run_id=run_id,
                cells=seed_cells,
                region_ids_by_name=region_ids_by_name,
                seen_query_texts=seen_query_texts,
                geo_anchor=geo_anchor,
                geo_polygon_record=geo_polygon_record,
            )
            campaign_cells_used = await increment_run_cells_total(
                session_factory, run_id=run_id, by=len(cell_ids)
            )

        client = create_places_client(discovery_provider)
        summary = {"cells": campaign_cells_used, "places_found": 0, "cell_failures": 0}
        if not client.is_configured():
            if discovery_provider == "gosom":
                config_error = "Gosom scraper is not configured (GOSOM_BASE_URL)."
            else:
                config_error = "Google Places API key is not configured."
            async with session_factory() as fail_db:
                run = await fail_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.status = MapsCensusStatus.FAILED
                    run.error_message = config_error
                    run.completed_at = datetime.now(UTC)
                    await fail_db.commit()
            return summary

        classification_model = get_model(settings.maps_census_model)
        classification_provider = get_provider_registry().get_provider(classification_model.provider)
        tracker = MapsQuotaTracker()
        set_active_tracker(tracker)
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
                execution_token=execution_token,
                gosom_breaker=gosom_breaker,
                discovery_provider=discovery_provider,
                geo_polygon_record=geo_polygon_record,
            )
            campaign_cells_used = campaign_budget["used"]

            # _execute_cells returning is not the same as it having made
            # progress: its own per-cell loop breaks immediately (without
            # claiming/attempting anything) when the Gosom breaker is OPEN
            # and not yet due for a recovery probe, or when the run was
            # cancelled/paused mid-iteration. Checking only
            # campaign_cells_used / expanding_names below would miss all
            # three cases and keep calling the paid gpt-4.1 grid planner
            # (maps_grid_planner.plan) every loop iteration to manufacture
            # more PENDING cells that can never be executed — cells_total
            # climbing while cells_completed stays frozen, with no
            # resulting work and no spending control. Stop expanding the
            # instant further execution isn't possible, before incurring
            # the cost of another planning call or another pending-cell
            # wave; the existing non_terminal_cells check further below
            # already keeps a run in this state from being marked
            # COMPLETED, and the watchdog cron (recover_maps_census_runs)
            # is the existing, already-bounded (10-minute cadence) path
            # back to a fresh attempt once conditions allow — never a
            # tight loop within this call.
            if gosom_breaker is not None and gosom_breaker.state == CircuitState.OPEN:
                if not gosom_breaker._should_attempt_recovery():
                    logger.error(
                        "maps_census_campaign_stopped_gosom_breaker_open run_id=%s "
                        "failures=%s recovery_in_seconds=%s — stopping geography "
                        "expansion and paid planning calls until Gosom recovers",
                        run_id,
                        gosom_breaker.metrics.failures,
                        gosom_breaker._seconds_until_recovery(),
                    )
                    stopped_reason = "gosom_breaker_open"
                    break
            if await is_run_cancelled(session_factory, run_id=run_id):
                stopped_reason = "cancelled"
                break
            if await is_campaign_paused(session_factory, run_id=run_id):
                stopped_reason = "paused"
                break

            # === PHASE 0: Check campaign timeout ===
            if settings.maps_census_campaign_timeout_seconds:
                elapsed = (datetime.now(UTC) - campaign_start_time).total_seconds()
                if elapsed > campaign_timeout_seconds:
                    logger.error(
                        f"maps_census_campaign_timeout run_id={run_id} elapsed_seconds={int(elapsed)} "
                        f"timeout_seconds={campaign_timeout_seconds} cells_used={campaign_cells_used}",
                        extra={
                            "run_id": run_id,
                            "elapsed_seconds": int(elapsed),
                            "timeout_seconds": campaign_timeout_seconds,
                            "cells_used": campaign_cells_used,
                            "event": "campaign_timeout",
                        },
                    )
                    stopped_reason = "campaign_timeout"
                    break
                elif elapsed > campaign_timeout_seconds * 0.75 and not timeout_warning_issued:
                    remaining_seconds = int(campaign_timeout_seconds - elapsed)
                    logger.warning(
                        f"maps_census_campaign_timeout_approaching run_id={run_id} "
                        f"remaining_seconds={remaining_seconds}",
                        extra={
                            "run_id": run_id,
                            "remaining_seconds": remaining_seconds,
                            "event": "timeout_approaching",
                        },
                    )
                    timeout_warning_issued = True

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
                    state_code=state_code,
                    state_name=state_name,
                    full_coverage=gosom_full_coverage,
                )
            except MapsGridPlanningError as exc:
                logger.warning(
                    f"maps_census_expansion_planning_failed run_id={run_id} regions={expanding_names}",
                    extra={
                        "run_id": run_id,
                        "regions": expanding_names,
                        "error": str(exc),
                        "event": "expansion_planning_failed",
                    },
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
                geo_anchor=geo_anchor,
                geo_polygon_record=geo_polygon_record,
            )
            if not new_cell_ids:
                stopped_reason = "no_expanding_regions"
                break

            campaign_cells_used = await increment_run_cells_total(
                session_factory, run_id=run_id, by=len(new_cell_ids)
            )
            # Re-sync the shared budget tracker to the authoritative DB
            # value too — without this, campaign_budget["used"] only ever
            # grew from _execute_cells's own subdivision increments and
            # silently drifted from (undercounted) the true total every
            # time an expansion wave like this one added cells, letting the
            # campaign-ceiling check below under-enforce the real limit.
            if campaign_budget is not None:
                campaign_budget["used"] = campaign_cells_used
            work_queue = new_cell_ids

        # A newer run_census() execution may have taken over while this one
        # was finishing its last batch of cells — never run keep/drop,
        # website validation, or mark the campaign COMPLETED (which would
        # enqueue Phase 2) on a superseded execution's stale, partial view.
        await ensure_run_execution_current(session_factory, run_id=run_id, token=execution_token)

        # The while-loop's own cells_completed/cells_total counters can be
        # thrown off by a (now-fenced-out) concurrent execution, so the gate
        # for "is discovery actually done" is a direct count of non-terminal
        # cell rows, not those counters. A batch completing — or this
        # execution's local view of the loop ending — must never be enough
        # on its own to start keep/drop, website validation, or Phase 2.
        async with session_factory() as pending_check_db:
            non_terminal_cells = int(
                (
                    await pending_check_db.execute(
                        select(func.count())
                        .select_from(MapsCensusCell)
                        .where(
                            MapsCensusCell.run_id == run_id,
                            MapsCensusCell.status.in_(
                                [MapsCensusCellStatus.PENDING, MapsCensusCellStatus.IN_PROGRESS]
                            ),
                        )
                    )
                ).scalar_one()
                or 0
            )
        if non_terminal_cells > 0:
            logger.warning(
                "maps_census_discovery_not_terminal run_id=%s non_terminal_cells=%s; "
                "leaving run RUNNING instead of completing the campaign",
                run_id,
                non_terminal_cells,
            )
            return summary

        # Strict keep/drop gate replaces the old batch relevance classifier:
        # one low-cost call per facility, Sonar fallback on low confidence,
        # uncertain → drop. Only keeps flow into website validation + detail
        # enrichment + the Eligible export sheet.
        from app.services.scraping.maps_keep_drop_service import run_keep_drop_pass

        keep_drop_summary = await run_keep_drop_pass(
            session_factory,
            run_id=run_id,
            country_code=country_code,
            country_name=country_name,
            state_code=state_code,
            state_name=state_name,
        )
        summary.update({f"keep_drop_{key}": value for key, value in keep_drop_summary.items()})

        # Mandatory eligibility-evidence classification — ownership,
        # organization scope, facility type, residential accommodation,
        # addiction focus — runs automatically here for every KEEP and
        # REVIEW candidate, completely independent of the optional "Enrich
        # with AI" trigger / MAPS_CENSUS_AUTO_ENRICHMENT_ENABLED. This is
        # the evidence compute_client_eligibility() needs to ever promote a
        # candidate past REVIEW to ELIGIBLE; a user must never have to
        # click a separate, optional button for an eligible facility to
        # appear. (Previously this only ran as part of the same gated
        # cascade as optional contact/detail enrichment — see
        # _keep_drop_gate_passed's fix for the root cause.)
        await self._run_mandatory_eligibility_classification(
            session_factory,
            run_id=run_id,
            country_code=country_code,
            country_name=country_name,
            tracker=tracker,
        )

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
                # Authoritative counters: derive cells_total/cells_completed
                # from the actual persisted cell rows rather than trust the
                # incrementally-maintained run.cells_total/cells_completed,
                # which (even with atomic per-cell claiming) is still a
                # running tally that could drift from a crash between
                # claiming a cell and committing its completion. This is the
                # one place the campaign's final numbers get written, so it
                # is also the one place they must be unconditionally correct.
                terminal_cell_statuses = {
                    MapsCensusCellStatus.COMPLETED,
                    MapsCensusCellStatus.CAPPED,
                    MapsCensusCellStatus.FAILED,
                }
                run.cells_total = len(all_cells)
                run.cells_completed = sum(
                    1 for c in all_cells if c.status in terminal_cell_statuses
                )
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
                permanently_failed_cells = sum(
                    1 for c in all_cells if c.status == MapsCensusCellStatus.FAILED
                )
                # Honest partial-coverage signal: some cells never produced
                # results (exhausted retries, hit the absolute per-cell
                # deadline, or were left pending when the Gosom circuit
                # breaker opened and the campaign still reached a terminal
                # state around them) — this run must not read as full,
                # clean coverage.
                run.status = (
                    MapsCensusStatus.COMPLETED_WITH_WARNINGS
                    if permanently_failed_cells > 0
                    else MapsCensusStatus.COMPLETED
                )
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
        geo_anchor: dict[str, float] | None = None,
        geo_polygon_record: Any = None,
    ) -> list[str]:
        """Insert planner cells as ``MapsCensusCell`` rows linked to a region,
        deduping against every query_text already persisted for this run
        (across seed and prior expansion rounds).

        ``geo_anchor``, when the run's country/state is in
        ``maps_geo_regions``'s trusted static table, seeds a cell's
        ``viewport_bounds`` with that box — but ONLY for cells with no
        ``city_name`` (genuinely region/state-wide queries). A city-named
        cell (the normal case — the grid planner always names a city) never
        gets the state-wide box: that box is not a valid bounding area for
        any specific city in the state, and attaching it would point Gosom's
        ``@lat,lon,zoom`` targeting at a location the query text never named
        (verified in production data — a Phenix City query, in the state's
        southeast corner, subdivided into quadrants covering the state's
        northwest, nowhere near Phenix City). Without a verified per-city
        coordinate source (which this codebase does not have), the only
        honest choice for a city-named cell is the pre-existing text-only
        search — never a fabricated or mismatched box.

        ``geo_polygon_record``, when given, replaces that single repeated
        bbox with real polygon-filtered sub-boxes (``maps_geo_boundaries.
        polygon_cells``) distributed round-robin across the region-wide
        cells — so an irregularly-shaped region's multiple seed cells cover
        actual area instead of all pointing at the same full rectangle. If
        the polygon yields no usable sub-boxes (e.g. a degenerate/too-small
        geometry), this falls back to the single-bbox behavior unchanged.
        """
        from itertools import cycle

        polygon_box_cycle = None
        if geo_polygon_record is not None:
            settings = get_settings()
            try:
                polygon_boxes = maps_geo_boundaries.polygon_cells(
                    geo_polygon_record, cell_degrees=settings.maps_geo_polygon_cell_degrees
                )
            except Exception:  # noqa: BLE001
                polygon_boxes = []
            if polygon_boxes:
                polygon_box_cycle = cycle(polygon_boxes)

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
                cell_geo_anchor = geo_anchor if geo_anchor and not cell.city_name else None
                if cell_geo_anchor is not None and polygon_box_cycle is not None:
                    cell_geo_anchor = next(polygon_box_cycle)
                row = MapsCensusCell(
                    run_id=run_id,
                    region_id=region_id,
                    region_name=cell.region_name,
                    city_name=cell.city_name,
                    query_text=cell.query_text,
                    query_family=cell.query_family,
                    query_language=cell.query_language,
                    viewport_bounds=dict(cell_geo_anchor) if cell_geo_anchor else None,
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
                        if is_coverage_evidence(
                            status=c.status, pagination_error=c.pagination_error
                        )
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
        execution_token: str | None = None,
        gosom_breaker: CircuitBreaker | None = None,
        geo_polygon_record: Any = None,
        discovery_provider: str | None = None,
    ) -> None:
        """Run a paginated Places search for each queued cell, classify newly
        discovered places immediately (so saturation reflects real
        plausibility rather than raw discovery), subdivide any cell that hit
        its pagination/result cap into child cells, and record per-cell/region
        discovery counters. Never holds a DB session across a Places/LLM
        network await. Child cells generated mid-run are appended to the same
        work queue so they execute within this call (bounded by
        ``maps_census_max_subdivision_depth`` and the campaign cell budget).

        ``geo_polygon_record``, when given, filters viewport-subdivision
        quadrants against the real verified polygon (see
        ``maps_geo_boundaries.bbox_intersects_polygon``) so a quadrant that
        falls entirely outside the region's actual shape is never created.
        """
        from collections import deque

        polygon_filter = None
        if geo_polygon_record is not None:
            def polygon_filter(bbox: dict[str, Any]) -> bool:
                try:
                    return maps_geo_boundaries.bbox_intersects_polygon(geo_polygon_record, bbox)
                except Exception:  # noqa: BLE001
                    return True  # fail open — never block subdivision on a geometry bug

        queue: deque[str] = deque(cell_ids)
        local_seen_query_texts = seen_query_texts if seen_query_texts is not None else set()
        max_depth = max(0, int(settings.maps_census_max_subdivision_depth))
        max_cell_attempts = max(1, int(settings.maps_census_cell_max_attempts))
        max_cell_timeout_attempts = max(1, int(settings.maps_census_cell_timeout_max_attempts))
        timeout_retry_base_seconds = max(1, int(settings.maps_census_cell_timeout_retry_base_seconds))

        while queue:
            cell_id = queue.popleft()

            if await is_run_cancelled(session_factory, run_id=run_id):
                break
            if await is_campaign_paused(session_factory, run_id=run_id):
                break

            if gosom_breaker is not None and gosom_breaker.state == CircuitState.OPEN:
                if not gosom_breaker._should_attempt_recovery():
                    # Several consecutive job timeouts already recorded —
                    # treat that as "gosom's internal dispatcher is not
                    # making real progress even though its REST API may
                    # still answer" (see github.com/gosom/google-maps-
                    # scraper#328) and stop submitting new jobs into an
                    # already-saturated queue. The cell stays PENDING
                    # (never claimed) — left for a later run_census
                    # invocation, once the breaker's recovery window has
                    # passed, to probe again.
                    logger.error(
                        "maps_census_gosom_breaker_open run_id=%s failures=%s "
                        "recovery_in_seconds=%s — pausing new Gosom submissions; "
                        "if this persists across multiple campaign resumes, the "
                        "gosom service likely needs an operator restart",
                        run_id,
                        gosom_breaker.metrics.failures,
                        gosom_breaker._seconds_until_recovery(),
                    )
                    break
                # Recovery window elapsed: let exactly one job through as a
                # health probe (the breaker itself flips to HALF_OPEN on
                # the next call) rather than resuming full-speed submission
                # before we know the dispatcher is actually back.

            if (
                discovery_provider or settings.maps_discovery_provider
            ) == "gosom" and await gosom_health_monitor.is_restart_in_progress(
                session_factory
            ):
                # The host-side supervisor has flagged (and is about to, or
                # is currently, executing) a `docker ... restart gosom` —
                # persisted outside this process's in-memory breaker, so it
                # is seen even on a fresh run_census() invocation. Pause new
                # submissions the same way an OPEN breaker does: the cell
                # stays PENDING, unclaimed, for a later invocation once
                # /restart/complete clears the flag.
                logger.warning(
                    "maps_census_gosom_restart_in_progress run_id=%s — pausing new "
                    "Gosom submissions until the supervisor reports completion",
                    run_id,
                )
                break

            async with session_factory() as heartbeat_db:
                run = await heartbeat_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.heartbeat_at = datetime.now(UTC)
                    await heartbeat_db.commit()

            if execution_token is not None:
                # A newer run_census() call for this run_id has taken over
                # (e.g. the stale-heartbeat watchdog fired while this
                # execution was just slow, not dead) — stop claiming cells
                # instead of racing that execution to completion.
                await ensure_run_execution_current(
                    session_factory, run_id=run_id, token=execution_token
                )

            # Atomic claim (guarded UPDATE ... WHERE status = 'pending', the
            # same primitive maps_cell_runner.claim_cells uses) instead of a
            # plain read-then-write: a concurrent execution that already
            # claimed this exact cell_id makes this a no-op skip rather than
            # both executions processing (and double-subdividing) the same
            # cell. A cell already COMPLETED/CAPPED/FAILED, or already
            # IN_PROGRESS under a concurrent claim, is simply not PENDING
            # anymore, so the guarded UPDATE naturally claims nothing for it.
            claimed = await claim_cell_by_id(
                session_factory, cell_id=cell_id, worker_id=execution_token or "unknown"
            )
            if not claimed:
                continue

            async with session_factory() as cell_db:
                cell = await cell_db.get(MapsCensusCell, cell_id)
                if cell is None:
                    continue
                query_text = cell.query_text
                region_name = cell.region_name
                city_name = cell.city_name
                region_id = cell.region_id
                query_family = cell.query_family
                query_language = cell.query_language
                expansion_depth = cell.expansion_depth or 0
                viewport_bounds = cell.viewport_bounds
                resume_token = cell.pagination_resume_token
                parent_cell_id = cell.parent_cell_id
                current_attempt_count = cell.attempt_count or 0
                # Set once, on this cell's very first claim, and never
                # touched again — the fixed reference point for the
                # absolute deadline below. Persisted (not just an
                # in-process monotonic clock) so it survives a worker
                # crash/restart: a cell resumed by a new process still
                # can't get a fresh 20 minutes just because the process
                # that was working it died.
                if cell.first_attempted_at is None:
                    cell.first_attempted_at = datetime.now(UTC)
                    await cell_db.commit()
                first_attempted_at = cell.first_attempted_at

            absolute_deadline_seconds = max(
                1, int(settings.maps_census_cell_absolute_deadline_seconds)
            )
            # SQLite (tests/local dev) returns naive datetimes even for a
            # DateTime(timezone=True) column — strip tzinfo on both sides so
            # this subtraction is correct on SQLite and Postgres alike (same
            # pattern used by the watchdog's cell-heartbeat freshness check).
            elapsed_seconds = (
                datetime.now(UTC).replace(tzinfo=None) - first_attempted_at.replace(tzinfo=None)
            ).total_seconds()
            remaining_budget_seconds = absolute_deadline_seconds - elapsed_seconds

            if remaining_budget_seconds <= 0:
                # Already past the absolute per-cell deadline (e.g. resumed
                # after a crash having already burned the whole budget) —
                # terminal now, without even attempting another call.
                summary["cell_failures"] += 1
                async with session_factory() as deadline_db:
                    cell = await deadline_db.get(MapsCensusCell, cell_id)
                    if cell is not None:
                        cell.status = MapsCensusCellStatus.FAILED
                        cell.completed_at = datetime.now(UTC)
                        cell.last_error = (
                            f"absolute per-cell deadline ({absolute_deadline_seconds}s) "
                            "exceeded before this attempt could start"
                        )[:2000]
                        cell.claimed_by = None
                    run = await deadline_db.get(MapsCensusRun, run_id)
                    if run is not None:
                        run.cells_completed += 1
                    if region_id:
                        region = await deadline_db.get(MapsCensusRegion, region_id)
                        if region is not None:
                            region.cells_completed += 1
                    await deadline_db.commit()
                continue

            heartbeat_budget = {"last": datetime.now(UTC)}

            async def _keep_heartbeat_warm() -> None:
                # Throttled: a long Gosom poll loop ticks every few seconds
                # for up to ~900s — refreshing on every tick would be a lot
                # of DB writes for no benefit. Once every 30s is plenty to
                # stay well inside the watchdog's 10-minute cutoff.
                now = datetime.now(UTC)
                if (now - heartbeat_budget["last"]).total_seconds() < 30:
                    return
                heartbeat_budget["last"] = now
                await heartbeat_cell(session_factory, cell_id=cell_id)
                await heartbeat_run(session_factory, run_id=run_id)

            async def _call_provider() -> Any:
                # The outer wait_for is the absolute, independent backstop:
                # no heartbeat, no gosom-side max_time, and no retry-backoff
                # arithmetic can push a cell's total wall-clock cost past
                # this — it is always exactly "whatever's left of the 20
                # minutes since this cell's first attempt."
                return await asyncio.wait_for(
                    client.search_text_paginated(
                        query=query_text,
                        region_code=country_code,
                        page_size=settings.maps_census_places_page_size,
                        max_pages=settings.maps_census_max_pages_per_cell,
                        resume_page_token=resume_token,
                        heartbeat_cb=_keep_heartbeat_warm,
                        viewport_bounds=viewport_bounds,
                    ),
                    timeout=remaining_budget_seconds,
                )

            try:
                if gosom_breaker is not None:
                    # Routes success/failure into the breaker automatically
                    # (CircuitBreakerOpenError on the recovery probe's own
                    # failure is re-raised as the original exception by
                    # .call(), so the existing except blocks below still
                    # see PlacesProviderError/TimeoutError as normal).
                    outcome = await gosom_breaker.call(_call_provider)
                else:
                    outcome = await _call_provider()
            except CircuitBreakerOpenError:
                # Defense in depth: the pre-loop check above should already
                # have stopped us before reaching here, but if the breaker
                # opened mid-iteration, defer rather than fail this cell.
                # This cell was already claimed (claim_cell_by_id, above) —
                # release it back to PENDING/unclaimed explicitly, the same
                # claimed_by=None primitive recover_stale_running_cells uses,
                # so it is immediately claimable again once the campaign
                # resumes rather than sitting IN_PROGRESS indefinitely (no
                # heartbeat will ever refresh it, since nothing is actually
                # running it, so recover_stale_running_cells would eventually
                # catch it too — doing it here is immediate and avoids
                # relying on that watchdog path for a case we can resolve
                # directly). No backoff/attempt penalty: the breaker, not
                # this cell, is what decided not to try.
                async with session_factory() as release_db:
                    cell = await release_db.get(MapsCensusCell, cell_id)
                    if cell is not None and cell.status == MapsCensusCellStatus.IN_PROGRESS:
                        cell.status = MapsCensusCellStatus.PENDING
                        cell.claimed_by = None
                    await release_db.commit()
                logger.warning(
                    "maps_census_gosom_breaker_open_mid_iteration run_id=%s cell_id=%s",
                    run_id,
                    cell_id,
                )
                break
            except TimeoutError:
                # Our own absolute-deadline cutoff fired (distinct from
                # PlacesTimeoutError, gosom's own 8-minute job timeout) —
                # the budget for this cell is exhausted; terminal now, no
                # further retry regardless of attempt_count.
                summary["cell_failures"] += 1
                async with session_factory() as cell_db:
                    cell = await cell_db.get(MapsCensusCell, cell_id)
                    if cell is not None:
                        cell.status = MapsCensusCellStatus.FAILED
                        cell.completed_at = datetime.now(UTC)
                        cell.last_error = (
                            f"absolute per-cell deadline ({absolute_deadline_seconds}s) "
                            "exceeded"
                        )[:2000]
                        cell.claimed_by = None
                    run = await cell_db.get(MapsCensusRun, run_id)
                    if run is not None:
                        run.cells_completed += 1
                    if region_id:
                        region = await cell_db.get(MapsCensusRegion, region_id)
                        if region is not None:
                            region.cells_completed += 1
                    await cell_db.commit()
                continue
            except PlacesProviderError as exc:
                # A provider timeout (e.g. an 8-minute Gosom job that never
                # reported back — verified in production as gosom's own
                # shared job queue backing up, not a slow query) gets a
                # tighter attempt cap and a much longer backoff than a
                # generic transient error: repeatedly re-running an already
                # expensive, already-saturated operation wastes wall-clock
                # for little chance of success, and 30s (the general retry
                # base) gives a saturated backend no real time to drain.
                #
                # Either way, a retry is only worth scheduling if the
                # cell's absolute 20-minute budget actually has room left
                # for the backoff wait itself — "at most one retry after a
                # timeout, only when the remaining time budget ... permit
                # it." Recomputed fresh (not reused from before the call)
                # since real time has now elapsed.
                budget_after_failure = absolute_deadline_seconds - (
                    datetime.now(UTC).replace(tzinfo=None)
                    - first_attempted_at.replace(tzinfo=None)
                ).total_seconds()
                is_timeout = isinstance(exc, PlacesTimeoutError)
                if is_timeout and gosom_breaker is not None:
                    # Persisted counter, independent of the in-memory
                    # breaker, so a timeout streak is still visible to the
                    # supervisor's /status endpoint across a watchdog
                    # relaunch (which creates a brand-new CircuitBreaker).
                    await gosom_health_monitor.record_job_timeout(session_factory)
                configured_max_attempts = (
                    max_cell_timeout_attempts if is_timeout else max_cell_attempts
                )
                backoff_base = timeout_retry_base_seconds if is_timeout else None
                effective_max_attempts = configured_max_attempts
                if budget_after_failure <= (backoff_base or DEFAULT_RETRY_BASE_SECONDS):
                    # Not enough budget left to even wait out the backoff,
                    # let alone attempt again — force terminal now instead
                    # of scheduling a retry that can never actually fire.
                    effective_max_attempts = current_attempt_count
                retry_kwargs: dict[str, Any] = {
                    "max_attempts": max(1, effective_max_attempts),
                }
                if backoff_base is not None:
                    retry_kwargs["base_backoff_seconds"] = backoff_base
                retry_info = await fail_cell_for_retry(
                    session_factory,
                    cell_id=cell_id,
                    error=str(exc),
                    **retry_kwargs,
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
            if gosom_breaker is not None:
                await gosom_health_monitor.record_job_success(session_factory)

            try:
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
                            geo_boundary_match=_geo_boundary_match(
                                geo_polygon_record,
                                latitude=result.latitude,
                                longitude=result.longitude,
                                country_code=country_code,
                            ),
                            international_phone_number=result.international_phone_number,
                            raw_website=result.website,
                            operating_status=getattr(result, "operating_status", None),
                            discovered_via_query=query_text,
                            photo_reference=result.photo_reference,
                            discovery_sources=(
                                [result.maps_url] if getattr(result, "maps_url", None) else None
                            ),
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
                    async with session_factory() as children_check_db:
                        already_subdivided = bool(
                            await children_check_db.scalar(
                                select(func.count())
                                .select_from(MapsCensusCell)
                                .where(MapsCensusCell.parent_cell_id == cell_id)
                            )
                        )
                    if already_subdivided:
                        # This cell was already subdivided once — a cell
                        # claimed and processed more than once (e.g. by a
                        # since-superseded concurrent execution, or a crash
                        # between persisting children and marking this cell
                        # CAPPED) must never subdivide twice and persist a
                        # duplicate set of child queries.
                        logger.warning(
                            "maps_census_cell_already_subdivided run_id=%s cell_id=%s",
                            run_id,
                            cell_id,
                        )
                        needs_subdivision = False
                if needs_subdivision and expansion_depth > 0 and new_places == 0 and parent_cell_id:
                    # This cell is itself a subdivision child (not an
                    # original seed cell) and found zero new places. Before
                    # recursing into yet another round of (mostly
                    # query-text-only) subdivision, check whether its whole
                    # sibling batch — the cells created alongside it from the
                    # same parent — was unproductive too. If so, the strategy
                    # that produced this batch (e.g. appending a directional
                    # qualifier to the query text) is empirically not
                    # narrowing anything for this lineage, so stop recursing
                    # down it instead of repeatedly executing equivalent
                    # searches for more cells with the same near-zero yield.
                    async with session_factory() as siblings_db:
                        sibling_new_unique_total = int(
                            await siblings_db.scalar(
                                select(
                                    func.coalesce(func.sum(MapsCensusCell.new_unique_places), 0)
                                ).where(
                                    MapsCensusCell.parent_cell_id == parent_cell_id,
                                    MapsCensusCell.status.in_(
                                        [
                                            MapsCensusCellStatus.COMPLETED,
                                            MapsCensusCellStatus.CAPPED,
                                        ]
                                    ),
                                )
                            )
                            or 0
                        )
                    if sibling_new_unique_total == 0:
                        logger.info(
                            "maps_census_subdivision_ineffective run_id=%s cell_id=%s "
                            "parent_cell_id=%s expansion_depth=%s",
                            run_id,
                            cell_id,
                            parent_cell_id,
                            expansion_depth,
                        )
                        needs_subdivision = False
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
                        polygon_filter=polygon_filter,
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
                        if child_ids:
                            # Atomic SQL-level increment (see
                            # increment_run_cells_total) — not a Python-level
                            # read-modify-write — so this can never be
                            # clobbered back down by another concurrent
                            # execution's own stale local total.
                            new_total = await increment_run_cells_total(
                                session_factory, run_id=run_id, by=len(child_ids)
                            )
                            if campaign_budget is not None:
                                campaign_budget["used"] = new_total

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
            except Exception as exc:  # noqa: BLE001 - one bad cell must not kill a campaign
                logger.exception(
                    "maps_census_cell_processing_failed run_id=%s cell_id=%s",
                    run_id,
                    cell_id,
                )
                retry_info = await fail_cell_for_retry(
                    session_factory,
                    cell_id=cell_id,
                    error=f"{type(exc).__name__}: {exc}",
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
        circuit_breaker: CircuitBreaker | None = None,
        run_llm_budget: LLMCallBudget | None = None,
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
                circuit_breaker=circuit_breaker,
                run_llm_budget=run_llm_budget,
                run_id=run_id,
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
        circuit_breaker: CircuitBreaker | None = None,
        run_llm_budget: LLMCallBudget | None = None,
        run_id: str | None = None,
    ) -> list[MapsRelevanceDecision]:
        prompt = get_prompt_engine().render(
            "scraping/maps_relevance_classifier.j2",
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            places_json=json.dumps(payloads, ensure_ascii=True),
        )

        # === PHASE 0: Check budget before calling LLM ===
        if run_llm_budget and run_id:
            if not run_llm_budget.try_spend(1):
                log_budget_exceeded(run_id, "run_llm_classification", run_llm_budget.spent, run_llm_budget.max_calls)
                return [
                    MapsRelevanceDecision(
                        place_id=item["place_id"],
                        is_relevant=False,
                        reason="budget_exhausted",
                        confidence=0.0,
                    )
                    for item in payloads
                ]

        try:
            # === PHASE 0: Wrap LLM call with circuit breaker ===
            if circuit_breaker:
                response = await circuit_breaker.call(
                    asyncio.wait_for,
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
            else:
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

            if run_id:
                log_llm_call(
                    run_id=run_id,
                    model=model_slug,
                    provider="openrouter",
                    purpose="classification",
                    tokens_in=len(prompt),
                    tokens_out=len(response.text),
                    error=None,
                )

        except CircuitBreakerOpenError as exc:
            if run_id:
                log_circuit_breaker_event(run_id, "openrouter", "open", "open", failures=5)
            logger.warning(
                f"maps_census_classification_circuit_open count={len(payloads)} error={exc}",
                extra={"count": len(payloads), "error": str(exc), "event": "circuit_open"},
            )
            return [
                MapsRelevanceDecision(
                    place_id=item["place_id"],
                    is_relevant=False,
                    reason="circuit_breaker_open",
                    confidence=0.0,
                )
                for item in payloads
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"maps_census_classification_batch_failed count={len(payloads)} error={exc}",
                extra={"count": len(payloads), "error": str(exc), "event": "classification_failed"},
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

    async def _run_mandatory_eligibility_classification(
        self,
        session_factory,
        *,
        run_id: str,
        country_code: str,
        country_name: str,
        tracker: MapsQuotaTracker,
    ) -> dict[str, Any]:
        """Automatic, mandatory eligibility-evidence classification for every
        KEEP and REVIEW candidate — never gated by the optional "Enrich with
        AI" trigger. Writes its own ``eligibility_status`` key in
        processing_state (separate from ``enrichment_status``, which
        describes the independent, optional contact/detail enrichment
        stage) so _phase2_status() can report a genuinely authoritative
        "running"/"completed"/"failed" signal for mandatory verification
        alone, never conflated with whether optional enrichment has run.

        Resumable/idempotent via the existing claim-based
        build_classification_query (keep_drop_decision in {KEEP, REVIEW} or
        already ELIGIBLE, enrichment_status == PENDING): a second call for
        the same run_id (retry, concurrent job, or a later manual "Enrich
        with AI" click that still calls classify_run internally) finds zero
        remaining candidates and is a safe, free no-op — never re-judges an
        already-classified place or makes a duplicate paid call for it.
        """
        from app.services.scraping.maps_classification_service import maps_classification_service
        from app.services.scraping.maps_enrichment_cascade_service import (
            maps_enrichment_cascade_service,
        )
        from app.services.scraping.maps_enrichment_progress import persist_enrichment_progress
        from app.services.scraping.maps_enrichment_response_parser import EnrichmentParseStats
        from app.services.scraping.maps_sonar_fallback import SonarBudget, SonarFallbackStats

        settings = get_settings()

        # Finalize confident non-candidates (public/individual/unrelated/
        # etc.) first — same pre-filter the optional cascade uses, now also
        # correctly leaving KEEP and REVIEW candidates untouched for the
        # classification pass below (see _keep_drop_gate_passed).
        await maps_enrichment_cascade_service._finalize_cheap_skips(
            session_factory, run_id=run_id
        )

        async with session_factory() as relevant_db:
            relevant_total = int(
                await relevant_db.scalar(
                    select(func.count())
                    .select_from(MapsPlace)
                    .where(MapsPlace.run_id == run_id, MapsPlace.is_relevant.is_(True))
                )
                or 0
            )

        async def _set_eligibility_status(status: str, **extra: Any) -> None:
            await persist_enrichment_progress(
                session_factory,
                run_id=run_id,
                phase="eligibility_classification",
                extra={"eligibility_status": status, **extra},
            )

        await _set_eligibility_status("running")
        try:
            result = await maps_classification_service.classify_run(
                session_factory,
                run_id=run_id,
                country_code=country_code,
                country_name=country_name,
                tracker=tracker,
                sonar_budget=SonarBudget(
                    enabled=settings.maps_sonar_fallback_enabled,
                    max_percent=settings.maps_sonar_fallback_max_percent,
                    max_per_campaign=settings.maps_sonar_fallback_max_per_campaign,
                    selected_candidates=relevant_total,
                ),
                sonar_stats=SonarFallbackStats(),
                parse_stats=EnrichmentParseStats(),
            )
        except Exception as exc:  # noqa: BLE001 — never let this abort run_census
            logger.exception(
                "maps_census_eligibility_classification_failed run_id=%s error=%s", run_id, exc
            )
            await _set_eligibility_status("failed", eligibility_error=str(exc)[:500])
            return {"classified": 0, "error": str(exc)}

        await _set_eligibility_status(
            "paused" if result.get("paused") else "completed",
            eligibility_classified_count=result.get("classified", 0),
        )
        return result

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
                    if not set_unless_overridden(place, "official_website", None):
                        continue
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
                    if not set_unless_overridden(place, "official_website", shared_url):
                        continue
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
                        if set_unless_overridden(place, "official_website", url):
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
                        if set_unless_overridden(place, "official_website", selected.url):
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
                        if set_unless_overridden(place, "official_website", url):
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


def _geo_boundary_match(
    geo_polygon_record: Any,
    *,
    latitude: float | None,
    longitude: float | None,
    country_code: str | None = None,
) -> str | None:
    """Geographic result validation (Task 6): coordinate-based targeting
    is a discovery strategy, not proof of membership. Returns which of the
    six honest outcomes applies — never silently treats "no coordinates"
    or "no polygon to check against" as if the place were confirmed inside
    the region, and never labels a coarse bounding-box check as if it were
    a precise verified polygon.

    When no dataset-backed polygon is cached locally (``geo_polygon_record``
    is None), falls back to the coarse country-level bounding box in
    ``maps_geo_regions`` — e.g. Finland, see 0afe4ca7-2dd3-47aa-a2cd-
    566e6b19b208, where a Pompano Beach, FL result was never caught because
    "no_polygon_available" was treated as neutral, not "outside". This
    returns its own distinct ``*_bbox_fallback`` outcomes rather than
    reusing "inside_polygon"/"outside_polygon" — a plain rectangle is not
    a precise national border (it can't resolve a facility genuinely near
    a coastline or land border the box happens to cut through), so the
    audit trail must say which check actually ran. ``country_code`` not in
    the static table (or no coordinates) keeps the pre-existing
    "no_polygon_available" outcome — never silently treated as "inside".
    """
    if latitude is None or longitude is None:
        return "no_coordinates"
    if geo_polygon_record is None:
        fallback_box = resolve_geo_anchor(
            country_code=country_code, state_code=None, state_name=None
        )
        if fallback_box is None:
            return "no_polygon_available"
        inside = (
            fallback_box["south"] <= latitude <= fallback_box["north"]
            and fallback_box["west"] <= longitude <= fallback_box["east"]
        )
        return "inside_bbox_fallback" if inside else "outside_bbox_fallback"
    try:
        inside = maps_geo_boundaries.contains_point(
            geo_polygon_record, lat=latitude, lon=longitude
        )
    except Exception:  # noqa: BLE001
        return "no_polygon_available"
    return "inside_polygon" if inside else "outside_polygon"


async def recompute_geo_boundary_for_run(
    session_factory, *, run_id: str, apply: bool = False
) -> dict[str, Any]:
    """Re-evaluate every already-discovered place's geo_boundary_match from
    its stored latitude/longitude — using whatever boundary data (cached
    polygon, or the static bounding-box fallback) is available *now*,
    without rerunning Gosom/Phase 1 or touching discovery/keep-drop state.

    Works for any run (not just Finland): it looks up the boundary purely
    from the run's own country_code/state_code/state_name, exactly like
    the discovery-time check does, so a country added to maps_geo_regions
    later benefits here too without any run-specific code.

    Only changes a place's ``geo_boundary_match`` and, when that flips to
    "outside_polygon" or "outside_bbox_fallback" and the place was
    previously ELIGIBLE, its ``client_eligibility`` (downgraded to REVIEW
    via compute_client_eligibility — never silently dropped from the
    census, never a hard exclusion on a plain bounding box). A manual
    admin exclusion (``manually_excluded_at``) is never touched
    (compute_client_eligibility already refuses to override it). Dry-run
    by default (``apply=False``): reports exactly what would change
    without writing anything.
    """
    async with session_factory() as db:
        run = await db.get(MapsCensusRun, run_id)
        if run is None:
            return {"error": "run_not_found"}

        geo_polygon_record = None
        try:
            geo_polygon_record = maps_geo_boundaries.resolve_region_boundary(
                country_code=run.country_code,
                state_code=run.state_code,
                state_name=run.state_name,
            )
        except Exception:  # noqa: BLE001
            geo_polygon_record = None

        places = (
            await db.execute(
                select(MapsPlace).where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.latitude.is_not(None),
                    MapsPlace.longitude.is_not(None),
                )
            )
        ).scalars().all()

        changed = 0
        newly_outside = 0
        downgraded_eligibility = 0
        for place in places:
            new_match = _geo_boundary_match(
                geo_polygon_record,
                latitude=place.latitude,
                longitude=place.longitude,
                country_code=run.country_code,
            )
            if new_match == place.geo_boundary_match:
                continue
            changed += 1
            if new_match in {"outside_polygon", "outside_bbox_fallback"}:
                newly_outside += 1
            if apply:
                place.geo_boundary_match = new_match
                if (
                    new_match in {"outside_polygon", "outside_bbox_fallback"}
                    and place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
                ):
                    place.client_eligibility = compute_client_eligibility(place)
                    if place.client_eligibility != MapsClientEligibility.ELIGIBLE.value:
                        downgraded_eligibility += 1

        if apply:
            await db.commit()

        return {
            "run_id": run_id,
            "places_checked": len(places),
            "geo_boundary_match_changed": changed,
            "newly_outside_polygon": newly_outside,
            "eligibility_downgraded": downgraded_eligibility,
            "applied": apply,
            "polygon_available": geo_polygon_record is not None,
        }


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


def _accepted_facility_filter():
    """The single authoritative "accepted rehab center" predicate — must
    mirror maps_export_service.py's _is_eligible_center exactly:
    client_eligibility == eligible, keep_drop_decision in {keep, review},
    and both a phone number and a website, as the Google Places export
    required. Shared by list_places' accepted_only filter and the
    accepted-facility counts surfaced on run summaries, so the banner count,
    the Phase 2 tab, and the Excel export can never disagree.
    """
    return and_(
        MapsPlace.client_eligibility == MapsClientEligibility.ELIGIBLE.value,
        MapsPlace.keep_drop_decision.in_(["keep", "review"]),
        func.trim(func.coalesce(MapsPlace.international_phone_number, "")) != "",
        or_(
            func.trim(func.coalesce(MapsPlace.official_website, "")) != "",
            func.trim(func.coalesce(MapsPlace.raw_website, "")) != "",
        ),
    )


async def compute_phase2_breakdown(db: AsyncSession, *, run_id: str) -> dict[str, int]:
    """Meaningful, mutually-exclusive Phase 2 counts for the UI — never just
    "eligible or not": a KEEP/REVIEW candidate the keep/drop gate routed to
    Phase 2 lands in exactly one of these buckets, so they always sum to the
    total candidate count. Mirrors _accepted_facility_filter's predicate for
    "eligible" so this can never disagree with the banner count or the
    Excel export.

    - eligible: the accepted set (_accepted_facility_filter).
    - missing_contact: ELIGIBLE but without a phone or a website, so not
      delivered.
    - rejected: classification genuinely ran and excluded the candidate
      (client_eligibility == EXCLUDED) — a real negative verification, not
      a technical failure.
    - unresolved: client_eligibility == REVIEW — evidence insufficient or
      genuinely ambiguous, not a rejection.
    - classification_pending: still waiting for the mandatory classification
      pass to reach it (same pre-classification states build_classification_
      query claims from — see PRE_CLASSIFICATION_PIPELINE_STATES).
    - classification_failed: a retryable technical failure is currently
      recorded against the place (CLASSIFICATION_FAILED_RETRYABLE) — distinct
      from "unresolved", which is a completed-but-ambiguous verification.
    """
    from app.services.scraping.maps_enrichment_processing_state import (
        PRE_CLASSIFICATION_PIPELINE_STATES,
        MapsEnrichmentPipelineState,
    )
    from app.services.scraping.maps_keep_drop_service import KEEP, REVIEW

    candidate_filter = or_(
        MapsPlace.keep_drop_decision == KEEP,
        MapsPlace.keep_drop_decision == REVIEW,
        MapsPlace.client_eligibility == MapsClientEligibility.ELIGIBLE.value,
    )

    async def _count(*extra):
        return int(
            await db.scalar(
                select(func.count())
                .select_from(MapsPlace)
                .where(MapsPlace.run_id == run_id, MapsPlace.is_relevant.is_(True), candidate_filter, *extra)
            )
            or 0
        )

    # Precedence (checked in this order) keeps the five buckets mutually
    # exclusive. Critically, "pending" is decided from enrichment_pipeline_
    # state, not client_eligibility: apply_keep_drop stamps every fresh
    # KEEP/REVIEW candidate's client_eligibility as REVIEW *before*
    # classification ever runs (see maps_keep_drop_service.py), so a place
    # that simply hasn't been classified yet would otherwise be
    # misclassified as "unresolved" — "unresolved" must mean classification
    # ran and genuinely could not resolve it, not "not classified yet".
    pending_states = PRE_CLASSIFICATION_PIPELINE_STATES - {
        MapsEnrichmentPipelineState.CLASSIFICATION_FAILED_RETRYABLE.value
    }
    is_failed = (
        MapsPlace.enrichment_pipeline_state
        == MapsEnrichmentPipelineState.CLASSIFICATION_FAILED_RETRYABLE.value
    )
    is_pending = or_(
        MapsPlace.enrichment_pipeline_state.is_(None),
        MapsPlace.enrichment_pipeline_state.in_(tuple(pending_states)),
    )
    is_classified = and_(not_(is_failed), not_(is_pending))

    classification_failed = await _count(is_failed)
    classification_pending = await _count(not_(is_failed), is_pending)
    eligible = await _count(is_classified, _accepted_facility_filter())
    missing_contact = await _count(
        is_classified,
        MapsPlace.client_eligibility == MapsClientEligibility.ELIGIBLE.value,
        not_(_accepted_facility_filter()),
    )
    rejected = await _count(
        is_classified, MapsPlace.client_eligibility == MapsClientEligibility.EXCLUDED.value
    )
    unresolved = await _count(
        is_classified,
        MapsPlace.client_eligibility.notin_(
            [MapsClientEligibility.ELIGIBLE.value, MapsClientEligibility.EXCLUDED.value]
        ),
    )
    return {
        "eligible": eligible,
        "missing_contact": missing_contact,
        "rejected": rejected,
        "unresolved": unresolved,
        "classification_pending": classification_pending,
        "classification_failed": classification_failed,
    }


def _export_location(place: MapsPlace, *, country_name: str) -> str:
    address = (place.formatted_address or "").strip()
    if address:
        return address
    parts = [p for p in (place.city_name, place.region_name, country_name) if p]
    return ", ".join(parts)


def _export_eligible(place: MapsPlace) -> bool:
    """Export readiness: classification-complete facility identity/location.

    Optional detail-enrichment fields (addictions, languages, price) must
    never gate export — a confirmed eligible/needs-review facility without
    named addictions still exports with a "not specified" placeholder.
    Confidence is read from either the legacy discovery ``confidence_score``
    or the two-phase pipeline's ``classification_confidence`` — a place
    re-classified by the two-phase pipeline may only have the latter set.
    """
    if not place.is_relevant:
        return False
    if not (place.canonical_name or place.raw_name or "").strip():
        return False
    if not _has_export_location(place):
        return False
    confidence = max(
        float(place.confidence_score or 0),
        float(place.classification_confidence or 0),
    )
    return confidence >= CONFIDENCE_EXPORT_MIN


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
        viewport_bounds=cell.viewport_bounds,
        attempt_count=cell.attempt_count,
        last_error=cell.last_error,
        next_retry_at=cell.next_retry_at,
        new_unique_places=cell.new_unique_places,
        new_plausible_places=cell.new_plausible_places,
    )


def _keep_drop_heartbeat_is_stale(state: dict) -> bool:
    """True only when a keep_drop_heartbeat_at IS present and is older than
    maps_keep_drop_stale_minutes. A historical run with no heartbeat key at
    all (recorded before this field existed) is never treated as stale —
    there is no persisted evidence either way, so "running" (the prior,
    unconditional behavior) is preserved rather than guessing.
    """
    raw = state.get("keep_drop_heartbeat_at")
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        heartbeat = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
    cutoff_minutes = max(1, get_settings().maps_keep_drop_stale_minutes)
    return heartbeat < datetime.now(UTC) - timedelta(minutes=cutoff_minutes)


def _phase2_status(run: MapsCensusRun, *, keeps_awaiting_enrichment: int = 0) -> str:
    """Reliable, persisted-state-derived Phase 2 (mandatory eligibility
    verification) execution status — "pending" / "running" / "stalled" /
    "completed" / "failed" — never inferred from the accepted-facility
    count alone, and never from whether the separate, OPTIONAL "Enrich with
    AI" detail enrichment has run. Zero accepted facilities after a
    genuinely completed eligibility pass is a legitimate outcome, not the
    same thing as "Phase 2 never ran"; the frontend distinguishes those two
    using this field together with accepted_facilities_count.

    Authoritative signal: ``processing_state["eligibility_status"]``, set by
    run_census's automatic, unconditional call to mandatory eligibility
    classification (see MapsCensusService._run_mandatory_eligibility_
    classification) right after keep/drop — this never depends on the
    optional enrichment trigger. "paused" (classification hit its per-run
    call budget) is reported as "stalled": real work remains and it needs
    an operator/retry, same as a crashed pass.

    Runs created before this field existed have no ``eligibility_status``
    key at all — for those, and only those, this falls back to the prior
    heuristic (keep_drop_status + the optional enrichment's own status +
    keeps_awaiting_enrichment) so a historical run's displayed status does
    not regress. A new run always gets eligibility_status set, so this
    fallback path is dead for anything processed after this fix.

    "stalled" also covers keep_drop_status still "running" with a heartbeat
    that has gone cold (see _keep_drop_heartbeat_is_stale) — evidence of a
    crashed or stuck pass, not proof of one, reported distinctly rather
    than as "failed": this function never writes run state, so opening the
    UI can never itself flip a run to failed or stalled.
    """
    run_status = run.status.value if hasattr(run.status, "value") else str(run.status)
    if run_status == MapsCensusStatus.FAILED.value:
        return "failed"
    state = run.processing_state or {}
    keep_drop_status = state.get("keep_drop_status")
    if keep_drop_status == "running":
        return "stalled" if _keep_drop_heartbeat_is_stale(state) else "running"

    eligibility_status = state.get("eligibility_status")
    if eligibility_status is not None:
        if eligibility_status == "running":
            return "running"
        if eligibility_status in {"failed", "paused"}:
            return "stalled"
        if eligibility_status == "completed":
            return "completed"
        return "pending"

    # --- Historical-run fallback only (no eligibility_status key) ---
    enrichment = enrichment_status_from_run(run)
    if enrichment in {"running", "paused"}:
        return "running"
    if enrichment == ENRICHMENT_STATUS_STALE_FAILED:
        return "stalled"
    if keep_drop_status == "completed":
        if enrichment == "completed":
            return "completed"
        if keeps_awaiting_enrichment > 0:
            return "awaiting_enrichment"
        # Keep/drop finished and nothing is queued for enrichment (e.g.
        # every candidate was dropped) — still a completed Phase 2 pass.
        return "completed"
    if enrichment == "completed":
        return "completed"
    return "pending"


def _run_summary(
    run: MapsCensusRun,
    *,
    accepted_facilities_count: int = 0,
    keeps_awaiting_enrichment: int = 0,
) -> MapsCensusRunSummary:
    return MapsCensusRunSummary(
        id=run.id,
        accepted_facilities_count=accepted_facilities_count,
        phase2_status=_phase2_status(run, keeps_awaiting_enrichment=keeps_awaiting_enrichment),
        country_code=run.country_code,
        country_name=run.country_name,
        state_code=run.state_code,
        state_name=run.state_name,
        discovery_provider=resolve_discovery_provider(run),
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
        total_cost_usd=float((run.quota_metrics or {}).get("total_cost_usd") or 0.0),
        geo_anchor=(run.processing_state or {}).get("geo_anchor"),
        geo_ai_plan=(run.processing_state or {}).get("geo_ai_plan"),
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
        contact_email=place.contact_email,
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
        inpatient_treatment=place.inpatient_treatment,
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
        bed_count=place.bed_count,
        verification_verdict=verification_verdict,
        verification_reason=place.verification_reason,
        verification_source_url=place.verification_source_url,
        keep_drop_decision=place.keep_drop_decision,
        manually_excluded=place.manually_excluded_at is not None,
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
    if status_value in {
        MapsCensusStatus.COMPLETED.value,
        MapsCensusStatus.COMPLETED_WITH_WARNINGS.value,
    } and (getattr(run, "places_found", 0) or 0) > 0:
        return True
    # A crash in a post-discovery stage leaves status=running with no
    # completed_at, which used to deadlock enrichment: the run could not be
    # terminalized while enrichment was pending, and enrichment could not be
    # requested until the run was terminal. A fully drained grid is proof
    # discovery itself is done, so treat it as finished.
    cells_total = int(getattr(run, "cells_total", 0) or 0)
    cells_completed = int(getattr(run, "cells_completed", 0) or 0)
    return (
        cells_total > 0
        and cells_completed >= cells_total
        and (getattr(run, "places_found", 0) or 0) > 0
    )


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
    except RunExecutionSuperseded:
        # A newer run_census() execution for this run_id took over (the
        # stale-heartbeat watchdog fired while this one was only slow, not
        # dead). Not a failure — the newer execution owns the run now.
        logger.info("maps_census_job_superseded", extra={"run_id": run_id})
    except Exception as exc:  # noqa: BLE001
        logger.exception("maps_census_job_failed", extra={"run_id": run_id})
        from app.db.session import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            run = await db.get(MapsCensusRun, run_id)
            if run is not None and run.status not in {
                MapsCensusStatus.COMPLETED,
                MapsCensusStatus.COMPLETED_WITH_WARNINGS,
            }:
                run.status = MapsCensusStatus.FAILED
                # Persist the real exception — "Unexpected error" left France
                # undiagnosable from the API (traceback only lived in logs).
                run.error_message = f"{type(exc).__name__}: {exc}"[:2000]
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
            if run is not None and run.status not in {
                MapsCensusStatus.COMPLETED,
                MapsCensusStatus.COMPLETED_WITH_WARNINGS,
            }:
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


async def run_maps_enrichment_batch_job(ctx: dict, run_id: str) -> None:
    """ARQ entrypoint: process exactly ONE small classification-or-detail batch,
    then self-enqueue the next batch (or stop cleanly) and exit.

    Deliberately short-lived: a worker crash mid-batch can only lose the small
    in-flight batch (MAPS_CLASSIFICATION_BATCH_SIZE / MAPS_DETAIL_ENRICHMENT_BATCH_SIZE
    places), never the hundreds of places a single long-lived job used to hold.
    The watchdog cron (``recover_maps_census_runs``) re-enqueues this same job
    if the campaign goes idle with pending work and a stale heartbeat.
    """
    del ctx
    from uuid import uuid4

    from app.db.session import AsyncSessionLocal
    from app.services.scraping.maps_enrichment_cascade_service import maps_enrichment_cascade_service
    from app.services.scraping.maps_enrichment_progress import (
        ENRICHMENT_STATUS_FAILED_RETRYABLE,
        persist_enrichment_progress,
    )

    batch_id = uuid4().hex[:12]
    logger.info(
        "maps_enrichment_batch_job_entered", extra={"run_id": run_id, "batch_id": batch_id}
    )
    try:
        outcome = await maps_enrichment_cascade_service.run_one_small_batch(
            None, run_id=run_id, batch_id=batch_id
        )
    except Exception as exc:  # noqa: BLE001 - never let one batch kill the chain silently
        logger.exception(
            "maps_enrichment_batch_job_failed", extra={"run_id": run_id, "batch_id": batch_id}
        )
        await persist_enrichment_progress(
            AsyncSessionLocal,
            run_id=run_id,
            phase="batch_job_fatal",
            enrichment_status=ENRICHMENT_STATUS_FAILED_RETRYABLE,
            error=str(exc),
            extra={"batch_id": batch_id, "enrichment_fatal": True},
        )
        # Do not self-requeue on a fatal orchestrator error — the watchdog cron
        # will pick this run back up (bounded by MAX_ENRICHMENT_AUTO_RESUMES)
        # once the heartbeat goes stale, avoiding a tight failure loop.
        return

    if outcome.get("has_more"):
        await maps_census_service._enqueue_job(
            "run_maps_enrichment_batch_job",
            run_id,
            inline_runner=lambda: run_maps_enrichment_batch_job({}, run_id),
        )
    else:
        logger.info(
            "maps_enrichment_batch_job_complete",
            extra={"run_id": run_id, "batch_id": batch_id, "phase": outcome.get("phase")},
        )
        # Enrichment drained with no more batches — stamp the run terminal so it
        # does not sit at status=running forever (Finland manual keep/drop path).
        await reconcile_run_if_drained(run_id)


async def reconcile_run_if_drained(run_id: str) -> None:
    """Best-effort terminal reconcile once a pipeline stage reports no work left.

    ``reconcile_run_finalization`` has its own safety gates (active cells /
    places / batch lock), so this is a no-op while any work remains. Never
    raises — a reconcile failure must not break the worker chain.
    """
    from app.db.session import AsyncSessionLocal
    from app.services.scraping.maps_run_finalization import reconcile_run_finalization

    try:
        async with AsyncSessionLocal() as db:
            await reconcile_run_finalization(db, run_id=run_id)
    except Exception:  # noqa: BLE001
        logger.warning("maps_run_auto_reconcile_failed run_id=%s", run_id, exc_info=True)


async def recover_maps_census_runs(ctx: dict) -> None:
    """Requeue Maps census runs whose worker died mid-flight (stale heartbeat).

    Also auto-resumes enrichment on discovery-completed campaigns when:
    - enrichment_refresh_completed_at is null
    - pending places remain
    - running == 0
    - enrichment heartbeat is stale
    without requiring a destructive Recover reset.

    A run's own ``heartbeat_at`` is refreshed before every cell and — via
    ``heartbeat_cb`` threaded into the Places/Gosom client — periodically
    *during* a single cell's external call, so it should never go stale while
    an execution is genuinely alive, even across a ~900s Gosom scrape. As a
    second, independent check (the run-level heartbeat write could in
    principle be skipped by a bug, a crash between writes, or clock skew),
    this also looks at each candidate run's own cell-level heartbeats before
    concluding it is dead: any ``IN_PROGRESS`` cell heartbeat-ed more
    recently than ``cutoff`` means a worker is actively claiming/processing
    cells for this run right now, so a duplicate ``run_maps_census_job`` is
    never enqueued on top of it. ``_execute_cells`` also now claims cells via
    the same atomic guarded-UPDATE primitive as ``maps_cell_runner.claim_cells``
    (``claim_cell_by_id``), and ``run_census`` stamps/checks a fencing token
    (``maps_cell_runner.claim_run_execution`` /
    ``ensure_run_execution_current``) around the post-discovery stages — so
    even in the residual case where this watchdog does enqueue a second
    execution (e.g. a genuine crash mid-batch), two concurrent executions can
    no longer double-claim/double-process a cell, double-subdivide, or both
    reach the COMPLETED/Phase-2 transition.
    """
    del ctx
    from datetime import timedelta
    from uuid import uuid4

    from app.db.session import AsyncSessionLocal
    from app.services.scraping.maps_enrichment_progress import (
        ENRICHMENT_STATUS_STALE_FAILED,
        MAX_ENRICHMENT_AUTO_RESUMES,
        count_places_by_enrichment_status,
        parse_enrichment_heartbeat,
        persist_enrichment_progress,
        release_batch_lock,
        try_acquire_batch_lock,
    )

    settings = get_settings()
    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    enrichment_cutoff = datetime.now(UTC) - timedelta(
        minutes=max(1, settings.maps_enrichment_watchdog_stale_minutes)
    )
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
        for run in stale:
            active_cells = int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(MapsCensusCell)
                        .where(
                            MapsCensusCell.run_id == run.id,
                            MapsCensusCell.status.in_(
                                [
                                    MapsCensusCellStatus.PENDING,
                                    MapsCensusCellStatus.IN_PROGRESS,
                                ]
                            ),
                        )
                    )
                ).scalar_one()
                or 0
            )
            total_cells = int(
                (
                    await db.execute(
                        select(func.count())
                        .select_from(MapsCensusCell)
                        .where(MapsCensusCell.run_id == run.id)
                    )
                ).scalar_one()
                or 0
            )
            # Finished discovery left RUNNING (e.g. website refresh / null
            # heartbeat) must not re-enter seed grid planning.
            if total_cells > 0 and active_cells == 0:
                logger.warning(
                    "maps_census_skip_stale_recover_discovery_done run_id=%s cells=%s",
                    run.id,
                    total_cells,
                )
                continue
            freshest_cell_heartbeat = await db.scalar(
                select(func.max(MapsCensusCell.heartbeat_at)).where(
                    MapsCensusCell.run_id == run.id,
                    MapsCensusCell.status == MapsCensusCellStatus.IN_PROGRESS,
                )
            )
            # SQLite (tests/local dev) returns naive datetimes even for a
            # DateTime(timezone=True) column — strip tzinfo on both sides so
            # this comparison is correct on SQLite and Postgres alike.
            if (
                freshest_cell_heartbeat is not None
                and freshest_cell_heartbeat.replace(tzinfo=None) >= cutoff.replace(tzinfo=None)
            ):
                # A cell is actively being worked right now (its own
                # heartbeat is fresh, kept warm during the external call by
                # heartbeat_cb) even though the coarser run-level heartbeat
                # looks stale — this run is alive, not dead. Never duplicate
                # a healthy execution just because one scrape is slow.
                logger.info(
                    "maps_census_skip_stale_recover_cell_heartbeat_fresh run_id=%s",
                    run.id,
                )
                continue
            stale_discovery_ids.append(run.id)

        # Bounded auto-resume: a run that keeps coming back here (Gosom
        # still unavailable every cycle) must eventually surface as FAILED
        # with a clear reason rather than relaunch forever every ~10
        # minutes. Mirrors the enrichment watchdog's own
        # MAX_ENRICHMENT_AUTO_RESUMES pattern. An atomic lock (reusing the
        # same primitive enrichment resume uses) also prevents two
        # back-to-back watchdog ticks from both deciding to relaunch the
        # same run before its heartbeat has had a chance to refresh.
        # A host-side supervisor restart in progress explains the stale
        # heartbeat on its own — the run isn't stuck, Gosom is mid-restart.
        # Skip the auto-resume penalty entirely for this tick rather than
        # spending one of the bounded attempts on something the supervisor
        # is already handling; the run is simply left for the next tick,
        # by which point /restart/complete should have cleared the flag.
        supervisor_restart_in_progress = await gosom_health_monitor.is_restart_in_progress(
            AsyncSessionLocal
        )
        # Relaunching while the persisted timeout streak is still inside the
        # breaker's own cooldown window would just hammer a Gosom instance
        # already confirmed (by this very streak) to need time to recover —
        # see is_within_breaker_cooldown's docstring for the confirmed
        # production case this closes. Computed once per tick, same as
        # supervisor_restart_in_progress above.
        within_breaker_cooldown = await gosom_health_monitor.is_within_breaker_cooldown(
            AsyncSessionLocal, settings
        )
        resumable_discovery_ids: list[str] = []
        for run_id in stale_discovery_ids:
            if supervisor_restart_in_progress:
                logger.info(
                    "maps_census_skip_stale_recover_supervisor_restart run_id=%s",
                    run_id,
                )
                continue
            run = await db.get(MapsCensusRun, run_id)
            if run is None:
                continue
            state = dict(run.processing_state or {})
            auto_resumes = int(state.get("discovery_auto_resume_count") or 0)
            max_auto_resumes = max(1, settings.maps_census_discovery_max_auto_resumes)
            # Exhaustion is checked BEFORE the cooldown skip below: if the
            # bounded budget is already spent, waiting out the breaker's
            # cooldown first would only delay reaching this same FAILED
            # outcome — there is no further attempt left to protect by
            # waiting, so fail fast and surface it to the operator now.
            if auto_resumes >= max_auto_resumes:
                logger.error(
                    "maps_census_discovery_auto_resume_exhausted run_id=%s "
                    "auto_resumes=%s max=%s — marking FAILED instead of retrying again",
                    run_id,
                    auto_resumes,
                    max_auto_resumes,
                )
                run.status = MapsCensusStatus.FAILED
                run.error_message = (
                    f"Discovery could not make progress after {auto_resumes} automatic "
                    "recovery attempts — the Gosom provider appears to be persistently "
                    "unavailable or stuck. Completed cells and discovered places are "
                    "preserved; an operator should verify Gosom, then resume this run "
                    "explicitly."
                )
                run.completed_at = datetime.now(UTC)
                await db.commit()
                continue
            if within_breaker_cooldown:
                logger.info(
                    "maps_census_skip_stale_recover_breaker_cooldown run_id=%s",
                    run_id,
                )
                continue
            state["discovery_auto_resume_count"] = auto_resumes + 1
            run.processing_state = state
            await db.commit()
            resumable_discovery_ids.append(run_id)
        stale_discovery_ids = resumable_discovery_ids

        # Broader than "enrichment_refresh_attempts > 0": small-batch enrichment
        # only increments that counter on full completion, so a campaign that
        # has run several batches but never finished must still be caught here.
        enrichment_candidates = (
            await db.execute(
                select(MapsCensusRun).where(
                    MapsCensusRun.status == MapsCensusStatus.COMPLETED,
                    MapsCensusRun.enrichment_refresh_completed_at.is_(None),
                )
            )
        ).scalars().all()
        for run in enrichment_candidates:
            state = run.processing_state or {}
            ever_started = (
                int(run.enrichment_refresh_attempts or 0) > 0
                or bool(state.get("enrichment_heartbeat_at"))
                or bool(state.get("enrichment_status"))
            )
            if not ever_started:
                continue
            enrichment_snapshots.append(
                {
                    "id": run.id,
                    "heartbeat": parse_enrichment_heartbeat(run),
                    "updated_at": run.updated_at,
                    "auto_resumes": int(state.get("enrichment_auto_resume_count") or 0),
                }
            )
        await db.commit()

    for run_id in stale_discovery_ids:
        logger.warning("maps_census_recovering_stale_run", extra={"run_id": run_id})
        asyncio.create_task(run_maps_census_job({}, run_id))

    for snap in enrichment_snapshots:
        heartbeat = snap["heartbeat"]
        if heartbeat is not None and heartbeat >= enrichment_cutoff:
            continue
        updated = snap["updated_at"]
        if heartbeat is None and updated is not None:
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            if updated >= enrichment_cutoff:
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
        # Atomic lock: never let two watchdog ticks (or a watchdog tick racing a
        # still-finishing batch job) both decide to enqueue for the same run.
        lock_batch_id = f"watchdog:{uuid4().hex[:12]}"
        acquired = await try_acquire_batch_lock(
            AsyncSessionLocal,
            run_id=run_id,
            batch_id=lock_batch_id,
            ttl_seconds=settings.maps_enrichment_batch_lock_ttl_seconds,
        )
        if not acquired:
            continue
        # Release immediately: this acquire is only a one-shot dedupe gate
        # against another watchdog tick deciding to resume the same run at
        # the same instant. The dispatched job now acquires this SAME lock
        # itself for the duration of its own tick (see
        # maps_enrichment_cascade_service.run_one_small_batch) — holding it
        # here across the enqueue would make that acquire fail every time
        # (a different batch_id already holds it) and strand the run until
        # this lock's TTL expires, since _enqueue_job's "inline" dispatch
        # schedules the job as a separate asyncio task rather than running
        # it synchronously before this function continues.
        await release_batch_lock(AsyncSessionLocal, run_id=run_id, batch_id=lock_batch_id)
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
        if settings.maps_census_small_batch_enrichment_enabled and settings.maps_census_two_phase_pipeline_enabled:
            await maps_census_service._enqueue_job(
                "run_maps_enrichment_batch_job",
                run_id,
                inline_runner=lambda run_id=run_id: run_maps_enrichment_batch_job({}, run_id),
            )
        else:
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
