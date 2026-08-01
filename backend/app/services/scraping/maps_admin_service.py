"""Maps census admin service — dashboard, paginated lists, review actions, campaign controls."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
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


def _derive_current_stage(run: MapsCensusRun) -> str:
    status = run.status.value if hasattr(run.status, "value") else str(run.status)
    state = run.processing_state or {}

    if _campaign_paused(run):
        return "paused"
    if status == MapsCensusStatus.QUEUED.value:
        return "queued"
    if status == MapsCensusStatus.CANCELLED.value:
        return "cancelled"
    if status == MapsCensusStatus.FAILED.value:
        return "failed"
    if status == MapsCensusStatus.COMPLETED.value:
        if state.get("enrichment_paused"):
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
        if run.cells_completed < run.cells_total:
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

        cell_count_rows = (
            await db.execute(
                select(MapsCensusCell.status, func.count())
                .where(MapsCensusCell.run_id == run_id)
                .group_by(MapsCensusCell.status)
            )
        ).all()
        cell_counts: dict[str, int] = {}
        for status_value, count in cell_count_rows:
            key = status_value.value if hasattr(status_value, "value") else str(status_value)
            cell_counts[key] = int(count)
        place_counts = dict(
            (
                await db.execute(
                    select(MapsPlace.client_eligibility, func.count())
                    .where(MapsPlace.run_id == run_id)
                    .group_by(MapsPlace.client_eligibility)
                )
            ).all()
        )
        regions_total = await db.scalar(
            select(func.count()).select_from(MapsCensusRegion).where(MapsCensusRegion.run_id == run_id)
        )

        return MapsCensusRunAdminDetail(
            **summary.model_dump(),
            current_stage=_derive_current_stage(run),
            campaign_paused=_campaign_paused(run),
            country_profile_status=run.country_profile_status,
            country_profile_error=run.country_profile_error,
            funnel_metrics=run.funnel_metrics,
            saturation_summary=run.saturation_summary,
            processing_state=run.processing_state,
            quota_metrics=run.quota_metrics,
            regions_total=int(regions_total or 0),
            cells_pending=cell_counts.get(MapsCensusCellStatus.PENDING.value, 0),
            cells_failed=cell_counts.get(MapsCensusCellStatus.FAILED.value, 0),
            cells_capped=cell_counts.get(MapsCensusCellStatus.CAPPED.value, 0),
            places_eligible=int(place_counts.get(MapsClientEligibility.ELIGIBLE.value, 0)),
            places_review=int(place_counts.get(MapsClientEligibility.REVIEW.value, 0)),
            places_excluded=int(place_counts.get(MapsClientEligibility.EXCLUDED.value, 0)),
            website_refresh_attempts=run.website_refresh_attempts,
            enrichment_refresh_attempts=run.enrichment_refresh_attempts,
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
        return MapsCampaignActionResponse(
            run_id=run.id,
            status=MapsCensusStatus.CANCELLED.value,
            campaign_paused=_campaign_paused(run),
            message="Campaign cancelled",
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
            if field_name in {"addiction_focus_confirmed", "medical_detox", "residential_accommodation"}:
                normalized = str(new_value).strip().lower()
                setattr(place, field_name, normalized in {"true", "1", "yes"})
            else:
                setattr(place, field_name, new_value)
            if field_name in {"lifecycle_status", "ownership_status", "facility_type", "organization_scope", "operator_type", "operating_status", "addiction_focus_confirmed"}:
                place.client_eligibility = compute_client_eligibility(place)
                _sync_place_legacy_fields(place)
            stored_new_value = str(getattr(place, field_name, None))
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
