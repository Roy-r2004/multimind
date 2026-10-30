"""Maps census admin service — dashboard, paginated lists, review actions, campaign controls."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.db.models import (
    MapsCensusCell,
    MapsCensusCellStatus,
    MapsCensusRegion,
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsCountryProfileStatus,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
    MapsPlaceReviewAction,
)
from app.schemas.api import (
    MapsCampaignActionResponse,
    MapsCellListResponse,
    MapsCensusRegionItem,
    MapsCensusRunAdminDetail,
    MapsExportSummaryResponse,
    MapsPlaceDetail,
    MapsPlaceListResponse,
    MapsPlaceReviewActionItem,
    MapsPlaceReviewRequest,
    MapsRegionListResponse,
    PaginatedMeta,
)
from app.services.scraping.maps_census_service import (
    _cell_item,
    _place_item,
    _run_summary,
    maps_census_service,
)
from app.services.scraping.maps_eligibility import (
    compute_client_eligibility,
    derive_is_relevant,
    derive_legacy_verification_verdict,
)
from app.services.scraping.maps_export_service import maps_export_service

EVIDENCE_EXCERPT_MAX_CHARS = 240

_REVIEW_ACTION_TARGETS: dict[str, tuple[str | None, str | None]] = {
    "mark_eligible": (MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value, MapsClientEligibility.ELIGIBLE.value),
    "mark_review": (MapsLifecycleStatus.NEEDS_REVIEW.value, MapsClientEligibility.REVIEW.value),
    "mark_excluded": (MapsLifecycleStatus.UNRELATED.value, MapsClientEligibility.EXCLUDED.value),
    "mark_public": (MapsLifecycleStatus.CONFIRMED_PUBLIC.value, MapsClientEligibility.EXCLUDED.value),
    "mark_individual": (
        MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value,
        MapsClientEligibility.EXCLUDED.value,
    ),
}

_OVERRIDABLE_FIELDS = frozenset(
    {
        "lifecycle_status",
        "client_eligibility",
        "operator_type",
        "ownership_status",
        "funding_type",
        "facility_type",
        "care_setting",
        "organization_scope",
        "operator_name",
        "contact_status",
        "addiction_focus_confirmed",
        "medical_detox",
        "residential_accommodation",
        "inpatient_treatment",
        "operating_status",
        "official_website",
        "relevance_reason",
    }
)


def _require_admin_enabled() -> None:
    if not get_settings().maps_census_admin_ui_enabled:
        raise ForbiddenError("Maps census admin UI is disabled")


async def _get_run_for_org(db: AsyncSession, auth: AuthContext, run_id: str) -> MapsCensusRun:
    run = await db.get(MapsCensusRun, run_id)
    if run is None or run.organization_id != auth.org_id:
        raise NotFoundError("Maps census run", run_id)
    return run


def _campaign_paused(run: MapsCensusRun) -> bool:
    return bool((run.processing_state or {}).get("campaign_paused"))


def _keep_drop_already_running(run: MapsCensusRun) -> bool:
    """Best-effort, non-atomic early check so a blocked retry/revalidate
    action gets an immediate, clear error instead of a silent no-op minutes
    later. This is a UX convenience only — the actual correctness guard
    against two concurrent passes is try_start_keep_drop_pass's atomic
    SELECT ... FOR UPDATE claim inside run_keep_drop_pass itself, which
    still applies even if two requests both pass this check at once.
    """
    return (run.processing_state or {}).get("keep_drop_status") == "running"


def _derive_current_stage(
    run: MapsCensusRun, stages: dict[str, str] | None = None
) -> str:
    status = run.status.value if hasattr(run.status, "value") else str(run.status)
    state = run.processing_state or {}
    stages = stages or state.get("stage_statuses") or {}
    from app.services.scraping.maps_enrichment_progress import enrichment_status_from_run

    enrichment_status = enrichment_status_from_run(run)
    overall = stages.get("overall_status") or state.get("overall_status")

    if _campaign_paused(run):
        return "paused"
    if status == MapsCensusStatus.QUEUED.value:
        return "queued"
    if status == MapsCensusStatus.CANCELLED.value:
        return "cancelled"
    # Prefer a reconciled stage overall over a stale run.status=failed left
    # by a watchdog after enrichment already finished — this is a
    # DB-reconciled signal, so it always wins outright, including over the
    # status==COMPLETED checks below.
    if overall in {"completed", "completed_with_warnings"}:
        return "completed"
    if status == MapsCensusStatus.FAILED.value:
        return "failed"
    # Discovery may be completed (run.status already flipped to COMPLETED)
    # while the run's OWN enrichment-retry loop is still active — these
    # checks must run BEFORE the blanket "status in {COMPLETED, ...}"
    # shortcut below, or a still-retrying/still-paused enrichment loop on a
    # formally "completed" run is misreported as fully done (the detailed
    # COMPLETED-specific checks further below exist for exactly this; they
    # were previously unreachable dead code because this blanket shortcut
    # used to also fire unconditionally on status in
    # {COMPLETED, COMPLETED_WITH_WARNINGS}).
    if enrichment_status in {"running", "failed_retryable", "stale_failed", "paused"}:
        return "enrichment"
    if stages.get("classification_status") == "running" or stages.get(
        "detail_enrichment_status"
    ) == "running":
        return "enrichment"
    if stages.get("website_discovery_status") == "running" or state.get(
        "website_search_paused"
    ):
        return "website_refresh"
    if stages.get("discovery_status") == "running":
        return "discovery"
    if status == MapsCensusStatus.COMPLETED_WITH_WARNINGS.value:
        return "completed"
    if status == MapsCensusStatus.COMPLETED.value:
        if state.get("enrichment_paused"):
            return "enrichment"
        if run.enrichment_refresh_completed_at is None and (
            (run.enrichment_refresh_attempts or 0) > 0
            or state.get("enrichment_heartbeat_at")
        ):
            return "enrichment"
        if state.get("website_search_paused"):
            return "website_refresh"
        return "completed"
    if status == MapsCensusStatus.RUNNING.value:
        if run.country_profile_status == MapsCountryProfileStatus.PENDING.value:
            return "country_profile"
        if state.get("website_search_paused"):
            return "website_refresh"
        if state.get("enrichment_paused"):
            return "enrichment"
        # Compare against actual persisted cells when available — never use a
        # stale seed denominator that makes 1134 completed look unfinished.
        metrics = state.get("cell_metrics") or {}
        total = int(metrics.get("total_cells") or run.cells_total or 0)
        completed = int(metrics.get("completed_cells") or run.cells_completed or 0)
        pending = int(metrics.get("pending_cells") or 0)
        running = int(metrics.get("running_cells") or 0)
        if pending > 0 or running > 0 or (total > 0 and completed < total):
            return "discovery"
        return "post_processing"
    return status


def _truncate_classification_evidence(
    evidence: dict[str, Any] | None,
    *,
    max_chars: int = EVIDENCE_EXCERPT_MAX_CHARS,
) -> dict[str, Any] | None:
    if not evidence:
        return evidence

    def _truncate_value(value: Any) -> Any:
        if isinstance(value, str):
            return value[:max_chars]
        if isinstance(value, dict):
            truncated = dict(value)
            for key in ("quote", "excerpt", "text", "snippet"):
                if key in truncated and isinstance(truncated[key], str):
                    truncated[key] = truncated[key][:max_chars]
            return truncated
        if isinstance(value, list):
            return [_truncate_value(item) for item in value]
        return value

    return {key: _truncate_value(value) for key, value in evidence.items()}


def _review_action_item(action: MapsPlaceReviewAction) -> MapsPlaceReviewActionItem:
    return MapsPlaceReviewActionItem(
        id=action.id,
        place_id=action.place_id,
        run_id=action.run_id,
        reviewer_user_id=action.reviewer_user_id,
        action=action.action,
        field_name=action.field_name,
        previous_value=action.previous_value,
        new_value=action.new_value,
        reason=action.reason,
        created_at=action.created_at,
    )


def _region_item(region: MapsCensusRegion) -> MapsCensusRegionItem:
    return MapsCensusRegionItem(
        id=region.id,
        region_name=region.region_name,
        cells_planned=region.cells_planned,
        cells_completed=region.cells_completed,
        unique_places_found=region.unique_places_found,
        new_unique_places_last_window=region.new_unique_places_last_window,
        plausible_providers_found=region.plausible_providers_found,
        new_plausible_providers_last_window=region.new_plausible_providers_last_window,
        duplicate_rate=region.duplicate_rate,
        query_languages_used=region.query_languages_used,
        provider_terms_used=region.provider_terms_used,
        saturation_status=region.saturation_status,
        eligible_candidates_found=region.eligible_candidates_found,
        review_candidates_found=region.review_candidates_found,
        confirmed_public_found=region.confirmed_public_found,
        individuals_found=region.individuals_found,
        unrelated_found=region.unrelated_found,
    )


def _sync_place_legacy_fields(place: MapsPlace) -> None:
    place.is_relevant = derive_is_relevant(place.lifecycle_status)
    place.verification_verdict = derive_legacy_verification_verdict(place.lifecycle_status)


class MapsAdminService:
    async def get_dashboard(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCensusRunAdminDetail:
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        summary = _run_summary(run)

        from app.services.scraping.maps_run_finalization import (
            collect_run_stage_snapshot,
            derive_stage_statuses,
        )

        snap = await collect_run_stage_snapshot(db, run_id=run_id)
        state = run.processing_state or {}
        metrics = dict(state.get("cell_metrics") or {})
        # Prefer live counts so the denominator never lags behind persisted cells.
        total_cells = int(snap["total_cells"])
        completed_cells = int(snap["completed_cells"])
        pending_cells = int(snap["pending_cells"])
        failed_cells = int(snap["failed_cells"])
        capped_cells = int(snap["capped_cells"])
        initial_cells = int(
            metrics.get("initial_cells")
            or state.get("initial_cells")
            or (
                run.cells_total
                if run.cells_total and run.cells_total < total_cells
                else snap["initial_cells"]
            )
            or total_cells
        )
        expansion_cells = max(0, total_cells - initial_cells)
        # Always derive live from persisted rows so the badge cannot stick on a
        # stale processing_state after adaptive expansion or enrichment finish.
        stages = derive_stage_statuses(run, snap)

        place_counts = dict(
            (
                await db.execute(
                    select(MapsPlace.client_eligibility, func.count())
                    .where(MapsPlace.run_id == run_id)
                    .group_by(MapsPlace.client_eligibility)
                )
            ).all()
        )
        keep_drop_counts = dict(
            (
                await db.execute(
                    select(MapsPlace.keep_drop_decision, func.count())
                    .where(MapsPlace.run_id == run_id)
                    .group_by(MapsPlace.keep_drop_decision)
                )
            ).all()
        )
        regions_total = await db.scalar(
            select(func.count()).select_from(MapsCensusRegion).where(MapsCensusRegion.run_id == run_id)
        )

        from app.services.scraping.maps_enrichment_progress import (
            enrichment_status_from_run,
            parse_enrichment_heartbeat,
        )

        last_activity_raw = state.get("last_activity_at")
        last_activity_at = None
        if last_activity_raw:
            try:
                last_activity_at = datetime.fromisoformat(
                    str(last_activity_raw).replace("Z", "+00:00")
                )
            except ValueError:
                last_activity_at = None
        if last_activity_at is None:
            last_activity_at = snap.get("last_place_activity") or snap.get("last_cell_activity")

        summary_data = summary.model_dump()
        # Dashboard denominator must be the actual persisted cell total.
        summary_data["cells_total"] = total_cells
        summary_data["cells_completed"] = completed_cells
        if stages.get("overall_status"):
            summary_data["status"] = stages["overall_status"]

        return MapsCensusRunAdminDetail(
            **summary_data,
            current_stage=_derive_current_stage(run, stages=stages),
            campaign_paused=_campaign_paused(run),
            country_profile_status=run.country_profile_status,
            country_profile_error=run.country_profile_error,
            funnel_metrics=run.funnel_metrics,
            saturation_summary=run.saturation_summary,
            processing_state=run.processing_state,
            quota_metrics=run.quota_metrics,
            regions_total=int(regions_total or 0),
            cells_pending=pending_cells,
            cells_failed=failed_cells,
            cells_capped=capped_cells,
            initial_cells=initial_cells,
            expansion_cells=expansion_cells,
            places_eligible=int(place_counts.get(MapsClientEligibility.ELIGIBLE.value, 0)),
            places_review=int(place_counts.get(MapsClientEligibility.REVIEW.value, 0)),
            places_excluded=int(place_counts.get(MapsClientEligibility.EXCLUDED.value, 0)),
            places_keep=int(keep_drop_counts.get("keep", 0)),
            places_dropped=int(keep_drop_counts.get("drop", 0)),
            places_undecided=int(keep_drop_counts.get(None, 0)),
            keep_drop_status=state.get("keep_drop_status"),
            website_refresh_attempts=run.website_refresh_attempts,
            enrichment_refresh_attempts=run.enrichment_refresh_attempts,
            enrichment_status=enrichment_status_from_run(run),
            enrichment_heartbeat_at=parse_enrichment_heartbeat(run),
            discovery_status=stages.get("discovery_status"),
            website_discovery_status=stages.get("website_discovery_status"),
            crawl_status=stages.get("crawl_status"),
            classification_status=stages.get("classification_status"),
            detail_enrichment_status=stages.get("detail_enrichment_status"),
            overall_status=stages.get("overall_status"),
            last_activity_at=last_activity_at,
            website_refresh_completed_at=run.website_refresh_completed_at,
            country_profile=run.country_profile,
        )

    async def list_regions(
        self,
        db: AsyncSession,
        auth: AuthContext,
        run_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> MapsRegionListResponse:
        _require_admin_enabled()
        await _get_run_for_org(db, auth, run_id)
        limit = max(1, min(limit, 500))
        offset = max(0, offset)

        total = await db.scalar(
            select(func.count()).select_from(MapsCensusRegion).where(MapsCensusRegion.run_id == run_id)
        )
        rows = (
            await db.execute(
                select(MapsCensusRegion)
                .where(MapsCensusRegion.run_id == run_id)
                .order_by(MapsCensusRegion.region_name)
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
        return MapsRegionListResponse(
            items=[_region_item(region) for region in rows],
            meta=PaginatedMeta(total=int(total or 0), limit=limit, offset=offset),
        )

    async def list_cells(
        self,
        db: AsyncSession,
        auth: AuthContext,
        run_id: str,
        *,
        status: str | None = None,
        region: str | None = None,
        query_family: str | None = None,
        query_language: str | None = None,
        capped_only: bool = False,
        failed_only: bool = False,
        expanded_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> MapsCellListResponse:
        _require_admin_enabled()
        await _get_run_for_org(db, auth, run_id)
        limit = max(1, min(limit, 500))
        offset = max(0, offset)

        query = select(MapsCensusCell).where(MapsCensusCell.run_id == run_id)
        count_query = select(func.count()).select_from(MapsCensusCell).where(MapsCensusCell.run_id == run_id)
        if status:
            normalized_status = status.strip().lower()
            query = query.where(MapsCensusCell.status == normalized_status)
            count_query = count_query.where(MapsCensusCell.status == normalized_status)
        if region:
            region_filter = MapsCensusCell.region_name.ilike(f"%{region.strip()}%")
            query = query.where(region_filter)
            count_query = count_query.where(region_filter)
        if query_family:
            family_filter = MapsCensusCell.query_family == query_family.strip()
            query = query.where(family_filter)
            count_query = count_query.where(family_filter)
        if query_language:
            language_filter = MapsCensusCell.query_language == query_language.strip()
            query = query.where(language_filter)
            count_query = count_query.where(language_filter)
        if capped_only:
            capped_filter = MapsCensusCell.status == MapsCensusCellStatus.CAPPED
            query = query.where(capped_filter)
            count_query = count_query.where(capped_filter)
        if failed_only:
            failed_filter = MapsCensusCell.status == MapsCensusCellStatus.FAILED
            query = query.where(failed_filter)
            count_query = count_query.where(failed_filter)
        if expanded_only:
            expanded_filter = MapsCensusCell.parent_cell_id.is_not(None)
            query = query.where(expanded_filter)
            count_query = count_query.where(expanded_filter)

        total = await db.scalar(count_query)
        rows = (
            await db.execute(
                query.order_by(
                    MapsCensusCell.region_name,
                    MapsCensusCell.city_name,
                    MapsCensusCell.query_text,
                )
                .limit(limit)
                .offset(offset)
            )
        ).scalars().all()
        return MapsCellListResponse(
            items=[_cell_item(cell) for cell in rows],
            meta=PaginatedMeta(total=int(total or 0), limit=limit, offset=offset),
        )

    async def list_places(
        self,
        db: AsyncSession,
        auth: AuthContext,
        run_id: str,
        *,
        search: str | None = None,
        client_eligibility: str | None = None,
        lifecycle_status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> MapsPlaceListResponse:
        _require_admin_enabled()
        await _get_run_for_org(db, auth, run_id)
        limit = max(1, min(limit, 500))
        offset = max(0, offset)

        query = select(MapsPlace).where(MapsPlace.run_id == run_id)
        count_query = select(func.count()).select_from(MapsPlace).where(MapsPlace.run_id == run_id)
        if search:
            term = f"%{search.strip()}%"
            search_filter = or_(
                MapsPlace.canonical_name.ilike(term),
                MapsPlace.raw_name.ilike(term),
                MapsPlace.formatted_address.ilike(term),
                MapsPlace.operator_name.ilike(term),
            )
            query = query.where(search_filter)
            count_query = count_query.where(search_filter)
        if client_eligibility:
            eligibility_filter = MapsPlace.client_eligibility == client_eligibility.strip().lower()
            query = query.where(eligibility_filter)
            count_query = count_query.where(eligibility_filter)
        if lifecycle_status:
            lifecycle_filter = MapsPlace.lifecycle_status == lifecycle_status.strip().lower()
            query = query.where(lifecycle_filter)
            count_query = count_query.where(lifecycle_filter)

        total = await db.scalar(count_query)
        rows = (
            await db.execute(query.order_by(MapsPlace.canonical_name).limit(limit).offset(offset))
        ).scalars().all()
        return MapsPlaceListResponse(
            items=[_place_item(place) for place in rows],
            meta=PaginatedMeta(total=int(total or 0), limit=limit, offset=offset),
        )

    async def get_place_detail(
        self, db: AsyncSession, auth: AuthContext, run_id: str, place_id: str
    ) -> MapsPlaceDetail:
        _require_admin_enabled()
        await _get_run_for_org(db, auth, run_id)
        place = await db.get(MapsPlace, place_id)
        if place is None or place.run_id != run_id:
            raise NotFoundError("Maps place", place_id)

        review_rows = (
            await db.execute(
                select(MapsPlaceReviewAction)
                .where(
                    MapsPlaceReviewAction.run_id == run_id,
                    MapsPlaceReviewAction.place_id == place_id,
                )
                .order_by(MapsPlaceReviewAction.created_at.desc())
            )
        ).scalars().all()

        base = _place_item(place)
        evidence = _truncate_classification_evidence(place.classification_evidence)
        payload = base.model_dump()
        payload["classification_evidence"] = evidence
        return MapsPlaceDetail(
            **payload,
            enrichment_pages_crawled=list(place.enrichment_pages_crawled or []),
            enrichment_error_message=place.enrichment_error_message,
            operating_status=place.operating_status,
            review_actions=[_review_action_item(row) for row in review_rows],
        )

    async def pause_run(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        state = dict(run.processing_state or {})
        state["campaign_paused"] = True
        state["campaign_paused_at"] = datetime.now(UTC).isoformat()
        run.processing_state = state
        await db.commit()
        status = run.status.value if hasattr(run.status, "value") else str(run.status)
        return MapsCampaignActionResponse(
            run_id=run.id,
            status=status,
            campaign_paused=True,
            message="Campaign paused",
        )

    async def resume_run(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        state = dict(run.processing_state or {})
        state["campaign_paused"] = False
        state.pop("campaign_paused_at", None)
        run.processing_state = state
        await db.commit()
        status = run.status.value if hasattr(run.status, "value") else str(run.status)
        return MapsCampaignActionResponse(
            run_id=run.id,
            status=status,
            campaign_paused=False,
            message="Campaign resumed",
        )

    async def cancel_run(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        run.status = MapsCensusStatus.CANCELLED
        run.completed_at = datetime.now(UTC)
        run.heartbeat_at = datetime.now(UTC)
        await db.commit()

        # A cancelled run's displayed counters must be honest, not whatever
        # an in-memory running tally last wrote (possibly from a since-
        # superseded concurrent execution) — this is the terminal state the
        # operator sees, so reconcile cells_total/cells_completed from the
        # actual persisted cell rows before returning.
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.services.scraping.maps_cell_runner import reconcile_run_cell_counters

        bind = db.bind if db.bind is not None else db.get_bind()
        session_factory = async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)
        await reconcile_run_cell_counters(session_factory, run_id=run_id)
        await db.refresh(run)
        return MapsCampaignActionResponse(
            run_id=run.id,
            status=MapsCensusStatus.CANCELLED.value,
            campaign_paused=_campaign_paused(run),
            message="Campaign cancelled",
        )

    async def recover_run(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        """Re-enqueue the census job for a failed or stalled run.

        ``run_census`` resumes from persisted state: finished discovery grids
        are never re-planned, leftover pending/in-progress cells are
        reprocessed, and post-discovery stages (keep/drop, websites,
        enrichment) run when they have remaining work.
        """
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        current = run.status
        if current in {
            MapsCensusStatus.COMPLETED,
            MapsCensusStatus.COMPLETED_WITH_WARNINGS,
            MapsCensusStatus.CANCELLED,
        }:
            status = current.value if hasattr(current, "value") else str(current)
            return MapsCampaignActionResponse(
                run_id=run.id,
                status=status,
                campaign_paused=_campaign_paused(run),
                message=f"Run is already {status} — nothing to recover",
            )
        run.status = MapsCensusStatus.RUNNING
        run.error_message = None
        run.completed_at = None
        run.heartbeat_at = datetime.now(UTC)
        state = dict(run.processing_state or {})
        state["campaign_paused"] = False
        run.processing_state = state
        await db.commit()

        await maps_census_service._enqueue(run_id)

        return MapsCampaignActionResponse(
            run_id=run.id,
            status=MapsCensusStatus.RUNNING.value,
            campaign_paused=False,
            message="Recovery queued — census resumes from persisted state",
        )

    async def retry_failed_cells(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        result = await db.execute(
            select(MapsCensusCell).where(
                MapsCensusCell.run_id == run_id,
                MapsCensusCell.status == MapsCensusCellStatus.FAILED,
            )
        )
        failed_cells = result.scalars().all()
        for cell in failed_cells:
            cell.status = MapsCensusCellStatus.PENDING
            cell.error_message = None
            cell.last_error = None
            cell.next_retry_at = None
            cell.completed_at = None
            cell.claimed_by = None
        await db.commit()
        status = run.status.value if hasattr(run.status, "value") else str(run.status)
        return MapsCampaignActionResponse(
            run_id=run.id,
            status=status,
            campaign_paused=_campaign_paused(run),
            message=f"Reset {len(failed_cells)} failed cells to pending",
        )

    async def retry_websites(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        _require_admin_enabled()
        await maps_census_service.request_website_refresh(db, auth, run_id)
        run = await _get_run_for_org(db, auth, run_id)
        status = run.status.value if hasattr(run.status, "value") else str(run.status)
        return MapsCampaignActionResponse(
            run_id=run.id,
            status=status,
            campaign_paused=_campaign_paused(run),
            message="Website refresh queued",
        )

    async def retry_enrichment(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        _require_admin_enabled()
        await maps_census_service.request_enrichment(db, auth, run_id)
        run = await _get_run_for_org(db, auth, run_id)
        status = run.status.value if hasattr(run.status, "value") else str(run.status)
        return MapsCampaignActionResponse(
            run_id=run.id,
            status=status,
            campaign_paused=_campaign_paused(run),
            message="Enrichment queued",
        )

    async def pause_enrichment(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        state = dict(run.processing_state or {})
        state["enrichment_pipeline_paused"] = True
        state["enrichment_pipeline_paused_at"] = datetime.now(UTC).isoformat()
        run.processing_state = state
        await db.commit()
        status = run.status.value if hasattr(run.status, "value") else str(run.status)
        return MapsCampaignActionResponse(
            run_id=run.id,
            status=status,
            campaign_paused=_campaign_paused(run),
            message="Enrichment pipeline paused",
        )

    async def run_keep_drop(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        """Enqueue the strict keep/drop gate over existing places.

        Resumable: places with a persisted decision are skipped, nothing is
        rediscovered or deleted, and detail enrichment is enqueued for keeps
        when the sweep finishes.
        """
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        from app.services.scraping.maps_keep_drop_service import (
            count_undecided,
            run_maps_keep_drop_job,
        )

        undecided = await count_undecided(db, run_id=run_id)
        if undecided == 0:
            return MapsCampaignActionResponse(
                run_id=run.id,
                status=run.status.value if hasattr(run.status, "value") else str(run.status),
                campaign_paused=_campaign_paused(run),
                message="Keep/drop already decided for every place — nothing to do",
            )
        await maps_census_service._enqueue_job(
            "run_maps_keep_drop_job",
            run_id,
            inline_runner=lambda: run_maps_keep_drop_job({}, run_id),
        )
        return MapsCampaignActionResponse(
            run_id=run.id,
            status=run.status.value if hasattr(run.status, "value") else str(run.status),
            campaign_paused=_campaign_paused(run),
            message=f"Keep/drop queued for {undecided} undecided places",
        )

    async def recompute_eligibility(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        """Reapply the current eligibility rules to already-classified places.

        Pure recompute over fields already stored on each place (facility_type,
        ownership_status, operator_type, organization_scope,
        addiction_focus_confirmed, residential_accommodation,
        inpatient_treatment, care_setting) — no LLM calls, no rediscovery,
        and it never invents a value for a field that was never confirmed:
        compute_client_eligibility only ever treats inpatient_treatment as
        satisfying the mandatory residential/inpatient requirement when it
        is stored as exactly ``True``; ``None``/unset stays unresolved
        (REVIEW), never promoted to ELIGIBLE. Use this after an eligibility
        rule change (e.g. the inpatient/residential override, or a newly
        excluded facility_type) to apply the new rule to an already-
        completed campaign instead of rerunning discovery/classification
        from scratch.

        ``keep_drop_decision == "drop"`` places are excluded — that is a
        deliberate, confident AI rejection this bulk pass must never
        reopen. ``keep``/``review``/unset places ARE included: their
        keep_drop_decision itself is never touched here (only the derived
        lifecycle_status/client_eligibility/is_relevant/verification_
        verdict fields are recomputed), so including them is not
        "overwriting" that verdict — this previously excluded virtually
        every real-world Maps census place (every KEEP/REVIEW candidate
        routes through keep/drop and therefore always has a non-null
        keep_drop_decision — see apply_keep_drop), making this endpoint a
        no-op for exactly the cases an eligibility rule change like the
        inpatient override needs it for.
        """
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        from app.services.scraping.maps_place_enrichment_service import (
            _derive_lifecycle_status,
        )

        rows = (
            (
                await db.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        # NOT `.isnot("drop")` — PostgreSQL's `IS NOT` only
                        # accepts NULL or a boolean literal, never an
                        # arbitrary string; asyncpg rejects it as a syntax
                        # error. `IS DISTINCT FROM` is the correct NULL-
                        # safe operator (a NULL keep_drop_decision is
                        # still correctly treated as distinct from "drop").
                        MapsPlace.keep_drop_decision.is_distinct_from("drop"),
                        MapsPlace.facility_type.isnot(None),
                    )
                )
            )
            .scalars()
            .all()
        )

        changed = 0
        for place in rows:
            was_relevant = place.is_relevant
            place.lifecycle_status = _derive_lifecycle_status(place)
            place.client_eligibility = compute_client_eligibility(place)
            place.is_relevant = derive_is_relevant(place.lifecycle_status)
            place.verification_verdict = derive_legacy_verification_verdict(place.lifecycle_status)
            if place.is_relevant != was_relevant:
                changed += 1

        relevant_count = (
            await db.execute(
                select(func.count())
                .select_from(MapsPlace)
                .where(MapsPlace.run_id == run_id, MapsPlace.is_relevant.is_(True))
            )
        ).scalar_one() or 0
        run.places_classified_relevant = relevant_count

        await db.commit()
        return MapsCampaignActionResponse(
            run_id=run.id,
            status=run.status.value if hasattr(run.status, "value") else str(run.status),
            campaign_paused=_campaign_paused(run),
            message=(
                f"Recomputed eligibility for {len(rows)} places — "
                f"{changed} changed relevance, {relevant_count} now relevant"
            ),
        )

    async def revalidate_keeps(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        """Re-judge already-"keep" places against the current keep/drop prompt.

        The keep/drop pass is resumable and skips any place with a persisted
        ``keep_drop_decision`` — correct for normal resume, but it also means a
        prompt change (e.g. adding a new exclusion rule) never touches places
        that were already decided under the old prompt. This clears the
        decision on current keeps only (drops stay decided — a stricter rule
        can't turn a drop back into a keep) so the next keep-drop pass re-judges
        them with one fresh LLM call each, still using only already-stored
        Maps fields and already-crawled website content — no rediscovery.
        """
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        if _keep_drop_already_running(run):
            raise ConflictError(
                "A keep/drop pass is already running for this run — wait for it to "
                "finish before revalidating keeps."
            )
        from app.services.scraping.maps_keep_drop_service import (
            KEEP,
            run_maps_keep_drop_job,
        )

        rows = (
            (
                await db.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.keep_drop_decision == KEEP,
                    )
                )
            )
            .scalars()
            .all()
        )
        for place in rows:
            place.keep_drop_decision = None
            place.keep_drop_reason = None
            place.keep_drop_confidence = None
            place.keep_drop_source = None
            place.keep_drop_evidence = None
        await db.commit()

        reset_count = len(rows)
        if reset_count == 0:
            return MapsCampaignActionResponse(
                run_id=run.id,
                status=run.status.value if hasattr(run.status, "value") else str(run.status),
                campaign_paused=_campaign_paused(run),
                message="No kept places to revalidate",
            )

        await maps_census_service._enqueue_job(
            "run_maps_keep_drop_job",
            run_id,
            inline_runner=lambda: run_maps_keep_drop_job({}, run_id),
        )
        return MapsCampaignActionResponse(
            run_id=run.id,
            status=run.status.value if hasattr(run.status, "value") else str(run.status),
            campaign_paused=_campaign_paused(run),
            message=f"Revalidating {reset_count} previously-kept places against the current prompt",
        )

    def _keep_drop_retry_candidates_query(self, run_id: str):
        """Places auto-dropped (or stuck review, post-fix) purely because
        every classifier attempt errored — never a genuine model verdict.
        Shared by retry_failed_keep_drop and its read-only projection so
        the two can never drift apart.
        """
        from app.services.scraping.maps_keep_drop_service import (
            DROP,
            REVIEW,
            TECHNICAL_FAILURE_REASON,
        )

        return select(MapsPlace).where(
            MapsPlace.run_id == run_id,
            # DROP: legacy rows from before the fix below existed (every
            # total-failure verdict used to be stamped DROP). REVIEW: the
            # current, correct failure state — same technical-failure
            # marker, never a confident verdict either way, so both are
            # safe to re-judge.
            MapsPlace.keep_drop_decision.in_([DROP, REVIEW]),
            or_(
                MapsPlace.keep_drop_reason == TECHNICAL_FAILURE_REASON,
                MapsPlace.keep_drop_reason.like("keep_drop_error%"),
            ),
        )

    async def keep_drop_retry_projection(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> dict[str, object]:
        """Dry-run preview for retry_failed_keep_drop: how many places
        would be re-queued, and the resulting upper-bound AI call estimate
        — no mutation, no AI calls. Each candidate costs at most one nano
        call plus one Sonar fallback call (maps_keep_drop_sonar_fallback_enabled;
        not percentage-capped the way the classification-tier Sonar is).
        """
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        settings = get_settings()

        candidate_count = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(self._keep_drop_retry_candidates_query(run_id).subquery())
                )
            ).scalar_one()
            or 0
        )
        sonar_enabled = settings.maps_keep_drop_sonar_fallback_enabled
        max_calls_per_candidate = 2 if sonar_enabled else 1
        return {
            "run_id": run_id,
            "candidate_count": candidate_count,
            "keep_drop_sonar_fallback_enabled": sonar_enabled,
            "max_total_calls_upper_bound": candidate_count * max_calls_per_candidate,
            "campaign_paused": _campaign_paused(run),
            "keep_drop_already_running": _keep_drop_already_running(run),
        }

    async def retry_failed_keep_drop(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        """Re-judge places that were auto-dropped because the classifier failed.

        When every classifier attempt errors, the pass records
        ``drop`` / ``classifier_unavailable`` (or ``keep_drop_error: …``) with
        confidence 0.0 so the sweep can finish — but those places were never
        actually judged, and the resumable pass will never revisit a persisted
        decision. ``apply_keep_drop``'s DROP branch also stamps
        ``lifecycle_status=unrelated`` / ``client_eligibility=excluded`` /
        ``is_relevant=False``, which makes ``build_keep_drop_query``'s
        candidate filter skip them and ``stamp_non_candidate_drops`` silently
        re-drop them as ``preexisting_exclusion`` without ever calling the
        classifier — so clearing the keep_drop_* fields alone is not enough.
        This also restores lifecycle_status/client_eligibility/is_relevant to
        the same "needs review" candidate state a freshly-classified relevant
        place is in before its first keep/drop judgment, then re-enqueues the
        pass. Genuine model drops keep their verdicts untouched.
        """
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        if _keep_drop_already_running(run):
            raise ConflictError(
                "A keep/drop pass is already running for this run — wait for it to "
                "finish before retrying failed keep/drop judgments."
            )
        from app.services.scraping.maps_keep_drop_service import run_maps_keep_drop_job

        rows = (
            (await db.execute(self._keep_drop_retry_candidates_query(run_id)))
            .scalars()
            .all()
        )
        for place in rows:
            place.keep_drop_decision = None
            place.keep_drop_reason = None
            place.keep_drop_confidence = None
            place.keep_drop_source = None
            place.keep_drop_evidence = None
            place.lifecycle_status = MapsLifecycleStatus.NEEDS_REVIEW.value
            place.client_eligibility = MapsClientEligibility.REVIEW.value
            place.is_relevant = True
        await db.commit()

        reset_count = len(rows)
        if reset_count == 0:
            return MapsCampaignActionResponse(
                run_id=run.id,
                status=run.status.value if hasattr(run.status, "value") else str(run.status),
                campaign_paused=_campaign_paused(run),
                message="No failure-dropped places to retry",
            )

        await maps_census_service._enqueue_job(
            "run_maps_keep_drop_job",
            run_id,
            inline_runner=lambda: run_maps_keep_drop_job({}, run_id),
        )
        return MapsCampaignActionResponse(
            run_id=run.id,
            status=run.status.value if hasattr(run.status, "value") else str(run.status),
            campaign_paused=_campaign_paused(run),
            message=f"Re-judging {reset_count} places whose keep/drop classification previously failed",
        )

    def _eligibility_retry_candidates_query(self, run_id: str):
        """Places whose inpatient_treatment AND residential_accommodation —
        the two fields that actually decide eligibility — are BOTH still
        unresolved. Deliberately narrower than build_classification_query:
        - client_eligibility != ELIGIBLE: an already-confirmed eligible
          decision is never reopened, by construction, not merely by the
          downstream merge-protection in _apply_structured_fields_filling_
          unresolved_only — the safest guarantee is never selecting it at
          all.
        - manually_excluded_at IS NULL: a human audit decision is never
          touched by anything automatic (see compute_client_eligibility's
          own guard).
        - enrichment_status != RUNNING: never interrupt a place another
          process is actively working right now.
        This intentionally does NOT require enrichment_status ==
        COMPLETED — it also catches a place that was reset for some other
        reason and is sitting PENDING/FAILED with the same two fields still
        unresolved, without excluding it just because it isn't COMPLETED.
        """
        return select(MapsPlace).where(
            MapsPlace.run_id == run_id,
            MapsPlace.is_relevant.is_(True),
            MapsPlace.manually_excluded_at.is_(None),
            MapsPlace.client_eligibility != MapsClientEligibility.ELIGIBLE.value,
            MapsPlace.enrichment_status != MapsPlaceEnrichmentStatus.RUNNING.value,
            MapsPlace.inpatient_treatment.is_(None),
            MapsPlace.residential_accommodation.is_(None),
        )

    async def eligibility_retry_projection(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> dict[str, object]:
        """Dry-run preview for retry_unresolved_eligibility_evidence: how
        many places would be reset, and the resulting upper-bound AI call
        estimate — no mutation, no AI calls, no enqueue.
        """
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        settings = get_settings()

        candidate_count = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(self._eligibility_retry_candidates_query(run_id).subquery())
                )
            ).scalar_one()
            or 0
        )
        relevant_total = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(MapsPlace)
                    .where(MapsPlace.run_id == run_id, MapsPlace.is_relevant.is_(True))
                )
            ).scalar_one()
            or 0
        )
        # The Sonar cap is computed against the WHOLE run's relevant_total
        # (maps_census_service._run_mandatory_eligibility_classification /
        # the enrichment cascade both build SonarBudget this same way) — it
        # is shared with every other classification call in this run, not
        # scoped to just these candidates, so it is reported separately
        # rather than implying these candidates get their own private cap.
        sonar_cap_for_run = min(
            settings.maps_sonar_fallback_max_per_campaign,
            int(relevant_total * settings.maps_sonar_fallback_max_percent / 100.0),
        )
        return {
            "run_id": run_id,
            "candidate_count": candidate_count,
            "relevant_total_in_run": relevant_total,
            "max_structured_classification_calls": candidate_count,
            "sonar_fallback_enabled": settings.maps_sonar_fallback_enabled,
            "max_sonar_fallback_calls_for_whole_run": sonar_cap_for_run,
            "max_total_calls_upper_bound": candidate_count + min(candidate_count, sonar_cap_for_run),
            "campaign_paused": _campaign_paused(run),
        }

    async def retry_unresolved_eligibility_evidence(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        """Reset ONLY places with unresolved inpatient_treatment AND
        residential_accommodation evidence back into mandatory eligibility
        classification (Phase 2) — the fields the Sonar research extension
        exists to resolve. Never touches Phase 1 discovery/cells, never
        resets keep_drop_decision/website evidence/already-confirmed
        structured fields, and never selects an already-ELIGIBLE place (see
        _eligibility_retry_candidates_query).

        Idempotent and safe against duplicate execution: re-running this
        simply re-selects whatever is STILL unresolved (already-fixed
        places stop matching on their own), and the actual per-place work
        is claimed row-by-row by the same production batch engine every
        other enrichment trigger in this codebase already uses (see
        maps_census_service.request_enrichment /
        maps_batch_claim.py's SELECT ... FOR UPDATE SKIP LOCKED) — so even
        a second concurrent call can, at worst, enqueue one redundant empty
        batch, never double-process the same place or double-spend on it.
        """
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        from app.services.scraping.maps_enrichment_processing_state import (
            MapsEnrichmentPipelineState,
        )
        from app.services.scraping.maps_enrichment_progress import batch_lock_is_active

        if batch_lock_is_active(run):
            raise ConflictError(
                "An enrichment batch is already actively running for this run — wait for it "
                "to finish before retrying unresolved eligibility evidence."
            )

        rows = (
            (await db.execute(self._eligibility_retry_candidates_query(run_id)))
            .scalars()
            .all()
        )
        reset_count = len(rows)
        if reset_count == 0:
            return MapsCampaignActionResponse(
                run_id=run.id,
                status=run.status.value if hasattr(run.status, "value") else str(run.status),
                campaign_paused=_campaign_paused(run),
                message="No places with unresolved inpatient/residential evidence to retry",
            )

        for place in rows:
            place.enrichment_status = MapsPlaceEnrichmentStatus.PENDING.value
            place.enrichment_pipeline_state = MapsEnrichmentPipelineState.PREFILTER_PENDING.value
            place.enrichment_error_message = None
            place.enrichment_completed_at = None
            place.enrichment_attempts = 0
        await db.commit()

        await maps_census_service.request_enrichment(db, auth, run_id)

        return MapsCampaignActionResponse(
            run_id=run.id,
            status=run.status.value if hasattr(run.status, "value") else str(run.status),
            campaign_paused=_campaign_paused(run),
            message=(
                f"Reprocessing {reset_count} places with unresolved inpatient/residential evidence"
            ),
        )

    async def re_enrich_keeps(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        """Reset keep places to pending Phase 2 and enqueue detail enrichment.

        Used to backfill fields (e.g. treatment_price) after the enricher prompt
        changes. Discovery / keep-drop decisions are left untouched.
        """
        _require_admin_enabled()
        run = await _get_run_for_org(db, auth, run_id)
        from app.db.models import MapsPlace, MapsPlaceEnrichmentStatus
        from app.services.scraping.maps_enrichment_processing_state import (
            MapsEnrichmentPipelineState,
        )

        places = (
            await db.execute(
                select(MapsPlace).where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.keep_drop_decision == "keep",
                )
            )
        ).scalars().all()
        reset = 0
        for place in places:
            place.enrichment_status = MapsPlaceEnrichmentStatus.PENDING.value
            place.enrichment_pipeline_state = (
                MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value
            )
            place.enrichment_completed_at = None
            place.enrichment_error_message = None
            # Allow another Phase 2 attempt even if earlier attempts were spent.
            place.enrichment_attempts = 0
            reset += 1

        run.enrichment_refresh_completed_at = None
        state = dict(run.processing_state or {})
        state["enrichment_paused"] = False
        state["enrichment_pipeline_paused"] = False
        state.pop("enrichment_cursor", None)
        run.processing_state = state
        await db.commit()

        if reset:
            await maps_census_service.request_enrichment(db, auth, run_id)

        status = run.status.value if hasattr(run.status, "value") else str(run.status)
        return MapsCampaignActionResponse(
            run_id=run.id,
            status=status,
            campaign_paused=_campaign_paused(run),
            message=(
                f"Re-queued detail enrichment for {reset} keep places"
                if reset
                else "No keep places to re-enrich"
            ),
        )

    async def recover_enrichment(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsCampaignActionResponse:
        _require_admin_enabled()
        await _get_run_for_org(db, auth, run_id)
        from app.db.session import AsyncSessionLocal
        from app.services.scraping.maps_enrichment_cascade_service import (
            maps_enrichment_cascade_service,
        )

        reset = await maps_enrichment_cascade_service.reset_for_recovery(
            AsyncSessionLocal, run_id=run_id
        )
        return MapsCampaignActionResponse(
            run_id=run_id,
            status=MapsCensusStatus.COMPLETED.value,
            campaign_paused=_campaign_paused(await _get_run_for_org(db, auth, run_id)),
            message=f"Reset {reset.get('reset_places', 0)} places for cascaded enrichment recovery",
        )

    async def reconcile_finalization(
        self, db: AsyncSession, auth: AuthContext, run_id: str, *, force: bool = False
    ) -> dict[str, Any]:
        """Reconcile stage statuses/counters without re-running any processing."""
        _require_admin_enabled()
        await _get_run_for_org(db, auth, run_id)
        from app.services.scraping.maps_run_finalization import reconcile_run_finalization

        result = await reconcile_run_finalization(db, run_id=run_id, force=force)
        # Reload so response reflects committed counters/status.
        run = await _get_run_for_org(db, auth, run_id)
        result["campaign_paused"] = _campaign_paused(run)
        result["current_stage"] = _derive_current_stage(run)
        result["cells_total"] = run.cells_total
        result["cells_completed"] = run.cells_completed
        return result

    async def enrichment_cost_projection(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> dict[str, object]:
        _require_admin_enabled()
        from app.core.config import get_settings
        from app.services.scraping.maps_enrichment_selection import build_selection_report

        run = await _get_run_for_org(db, auth, run_id)
        settings = get_settings()
        report = await build_selection_report(db, run_id=run_id)
        sonar_max = min(
            settings.maps_sonar_fallback_max_per_campaign,
            int(report.selected_count * settings.maps_sonar_fallback_max_percent / 100.0),
        )
        return {
            "selected_primary_extraction_candidates": report.selected_count,
            "skipped_candidates": report.skipped_count,
            "skip_reasons": report.skip_reasons,
            "selection_sql": report.selection_sql,
            "projected_primary_extraction_calls": report.selected_count,
            "max_sonar_fallback_calls": sonar_max,
            "sonar_fallback_enabled": settings.maps_sonar_fallback_enabled,
            "projected_total_calls_upper_bound": report.selected_count + sonar_max,
        }

    async def apply_review_action(
        self,
        db: AsyncSession,
        auth: AuthContext,
        run_id: str,
        place_id: str,
        payload: MapsPlaceReviewRequest,
    ) -> MapsPlaceDetail:
        _require_admin_enabled()
        await _get_run_for_org(db, auth, run_id)
        place = await db.get(MapsPlace, place_id)
        if place is None or place.run_id != run_id:
            raise NotFoundError("Maps place", place_id)

        reason = payload.reason.strip()
        if not reason:
            raise ValidationError("Review reason is required")

        action = payload.action.strip().lower()
        field_name = payload.field_name.strip() if payload.field_name else None
        new_value = payload.new_value.strip() if isinstance(payload.new_value, str) else payload.new_value

        previous_value: str | None = None
        stored_new_value: str | None = None
        stored_field_name: str | None = field_name

        if action in _REVIEW_ACTION_TARGETS:
            lifecycle_target, eligibility_target = _REVIEW_ACTION_TARGETS[action]
            previous_value = f"lifecycle={place.lifecycle_status};client_eligibility={place.client_eligibility}"
            if lifecycle_target is not None:
                place.lifecycle_status = lifecycle_target
            if eligibility_target is not None:
                place.client_eligibility = eligibility_target
            stored_new_value = f"lifecycle={place.lifecycle_status};client_eligibility={place.client_eligibility}"
            _sync_place_legacy_fields(place)
            if action == "mark_excluded":
                place.manually_excluded_at = datetime.now(UTC)
                # A place already judged "keep" or "review" by the strict
                # gate must drop out of the Phase 2 eligible list
                # immediately — otherwise a user-removed row would still
                # show up there until the next keep/drop pass (which never
                # re-judges an already-decided place).
                if place.keep_drop_decision in {"keep", "review"}:
                    place.keep_drop_decision = "drop"
                    place.keep_drop_reason = "manually excluded after keep/drop"
                    place.keep_drop_source = "manual_override"
                    if place.enrichment_status in {
                        MapsPlaceEnrichmentStatus.PENDING.value,
                        MapsPlaceEnrichmentStatus.RUNNING.value,
                        MapsPlaceEnrichmentStatus.FAILED.value,
                    }:
                        place.enrichment_status = MapsPlaceEnrichmentStatus.SKIPPED.value
        elif action == "override_lifecycle":
            if not new_value:
                raise ValidationError("new_value is required for override_lifecycle")
            stored_field_name = "lifecycle_status"
            previous_value = place.lifecycle_status
            place.lifecycle_status = str(new_value)
            place.client_eligibility = compute_client_eligibility(place)
            stored_new_value = place.lifecycle_status
            _sync_place_legacy_fields(place)
        elif action == "override_client_eligibility":
            if not new_value:
                raise ValidationError("new_value is required for override_client_eligibility")
            stored_field_name = "client_eligibility"
            previous_value = place.client_eligibility
            place.client_eligibility = str(new_value)
            stored_new_value = place.client_eligibility
            _sync_place_legacy_fields(place)
        elif action == "override_field":
            if not field_name or new_value is None:
                raise ValidationError("field_name and new_value are required for override_field")
            if field_name not in _OVERRIDABLE_FIELDS:
                raise ValidationError(f"Field '{field_name}' cannot be overridden")
            previous_value = str(getattr(place, field_name, None))
            if field_name in {
                "addiction_focus_confirmed",
                "medical_detox",
                "residential_accommodation",
                "inpatient_treatment",
            }:
                normalized = str(new_value).strip().lower()
                setattr(place, field_name, normalized in {"true", "1", "yes"})
            else:
                setattr(place, field_name, new_value)
            if field_name in {
                "lifecycle_status",
                "ownership_status",
                "facility_type",
                "organization_scope",
                "operator_type",
                "operating_status",
                "addiction_focus_confirmed",
                "residential_accommodation",
                "inpatient_treatment",
            }:
                place.client_eligibility = compute_client_eligibility(place)
                _sync_place_legacy_fields(place)
            stored_new_value = str(getattr(place, field_name, None))
        elif action == "reopen_for_keep_drop":
            # Manual escape hatch: forces one place back into the keep/drop
            # candidate pool regardless of its current keep_drop_reason text.
            # Needed because a place's failure reason can itself get
            # overwritten (e.g. by stamp_non_candidate_drops re-stamping it
            # as "preexisting_exclusion" once is_relevant/lifecycle_status
            # already reflect a drop) — at that point no reason-text filter
            # can find it again, but the operator still knows which place_id
            # needs re-judging.
            previous_value = (
                f"keep_drop_decision={place.keep_drop_decision};"
                f"lifecycle={place.lifecycle_status};"
                f"client_eligibility={place.client_eligibility}"
            )
            place.keep_drop_decision = None
            place.keep_drop_reason = None
            place.keep_drop_confidence = None
            place.keep_drop_source = None
            place.keep_drop_evidence = None
            place.lifecycle_status = MapsLifecycleStatus.NEEDS_REVIEW.value
            place.client_eligibility = MapsClientEligibility.REVIEW.value
            place.is_relevant = True
            stored_new_value = (
                f"keep_drop_decision=None;"
                f"lifecycle={place.lifecycle_status};"
                f"client_eligibility={place.client_eligibility}"
            )
        else:
            raise ValidationError(f"Unsupported review action: {action}")

        review_row = MapsPlaceReviewAction(
            place_id=place.id,
            run_id=run_id,
            reviewer_user_id=auth.user.id,
            action=action,
            field_name=stored_field_name,
            previous_value=previous_value,
            new_value=stored_new_value,
            reason=reason,
        )
        db.add(review_row)
        await db.commit()
        await db.refresh(place)
        return await self.get_place_detail(db, auth, run_id, place_id)

    async def get_export_summary(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> MapsExportSummaryResponse:
        _require_admin_enabled()
        await _get_run_for_org(db, auth, run_id)
        sheets = await maps_export_service.get_export_summary(db, auth, run_id)
        total_places = sum(sheets.values())
        return MapsExportSummaryResponse(run_id=run_id, sheets=sheets, total_places=total_places)


maps_admin_service = MapsAdminService()
