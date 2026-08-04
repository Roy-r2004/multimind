"""Standalone Google Places Maps census endpoints."""

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context, require_org_admin
from app.db.session import get_db
from app.schemas.api import (
    MapsCampaignActionResponse,
    MapsCensusCellItem,
    MapsCellListResponse,
    MapsCensusRunAdminDetail,
    MapsCensusRunCreate,
    MapsCensusRunDetail,
    MapsCensusRunSummary,
    MapsExportSummaryResponse,
    MapsPlaceDetail,
    MapsPlaceItem,
    MapsPlaceListResponse,
    MapsPlaceReviewRequest,
    MapsRegionListResponse,
    MapsRunLiveStats,
)
from app.services.scraping.maps_admin_service import maps_admin_service
from app.services.scraping.maps_census_service import maps_census_service
from app.services.scraping.maps_export_service import MIME_XLSX, maps_export_service

router = APIRouter()


@router.post("/runs", response_model=MapsCensusRunDetail, status_code=status.HTTP_201_CREATED)
async def create_maps_census_run(
    data: MapsCensusRunCreate,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await maps_census_service.create_run(db, auth, data.country_code)


@router.get("/runs", response_model=list[MapsCensusRunSummary])
async def list_maps_census_runs(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await maps_census_service.list_runs(db, auth)


@router.get("/runs/{run_id}", response_model=MapsCensusRunDetail)
async def get_maps_census_run(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await maps_census_service.get_run(db, auth, run_id)


@router.get("/runs/{run_id}/live-stats")
async def get_maps_census_run_live_stats(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Get real-time stats from current database state (live counts, not cached).

    Returns: places_found_live, places_relevant_live (eligible), places_enriched_live,
    progress percentages, elapsed time.

    Use this endpoint to update stat tiles in real-time as run progresses.
    Poll every 2-5 seconds for smooth updates.
    """
    return await maps_census_service.get_run_live_stats(db, auth, run_id)


@router.get("/runs/{run_id}/places", response_model=list[MapsPlaceItem])
async def list_maps_census_places(
    run_id: str,
    relevant_only: bool = Query(default=False),
    with_website_only: bool = Query(default=False),
    client_eligibility: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await maps_census_service.list_places(
        db,
        auth,
        run_id,
        relevant_only=relevant_only,
        with_website_only=with_website_only,
        client_eligibility=client_eligibility,
        lifecycle_status=lifecycle_status,
    )


@router.get("/runs/{run_id}/cells", response_model=list[MapsCensusCellItem])
async def list_maps_census_cells(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await maps_census_service.list_cells(db, auth, run_id)


@router.get("/runs/{run_id}/export.csv")
async def export_maps_census_run_csv(
    run_id: str,
    tier: str = Query(default="all"),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    filename, csv_body = await maps_census_service.export_run_csv(
        db, auth, run_id, tier=tier
    )
    return Response(
        content=f"\ufeff{csv_body}",
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/runs/{run_id}/export.xlsx")
async def export_maps_census_run_xlsx(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    content, filename = await maps_export_service.build_workbook(db, auth, run_id)
    return Response(
        content=content,
        media_type=MIME_XLSX,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/runs/{run_id}/places/{place_id}/photo")
async def get_maps_census_place_photo(
    run_id: str,
    place_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    path = await maps_census_service.get_place_photo(db, auth, run_id, place_id)
    return FileResponse(path, media_type="image/jpeg")


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_maps_census_run(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await maps_census_service.delete_run(db, auth, run_id)


@router.post("/runs/{run_id}/refresh-websites", response_model=MapsCensusRunDetail)
async def refresh_maps_census_run_websites(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await maps_census_service.request_website_refresh(db, auth, run_id)


@router.post("/runs/{run_id}/enrich", response_model=MapsCensusRunDetail)
async def enrich_maps_census_run(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await maps_census_service.request_enrichment(db, auth, run_id)


# --- Admin UI (Phase 4) ---


@router.get("/runs/{run_id}/dashboard", response_model=MapsCensusRunAdminDetail)
async def get_maps_census_admin_dashboard(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.get_dashboard(db, auth, run_id)


@router.get("/runs/{run_id}/regions", response_model=MapsRegionListResponse)
async def list_maps_census_admin_regions(
    run_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.list_regions(db, auth, run_id, limit=limit, offset=offset)


@router.get("/runs/{run_id}/cells/paged", response_model=MapsCellListResponse)
async def list_maps_census_admin_cells(
    run_id: str,
    status: str | None = Query(default=None),
    region: str | None = Query(default=None),
    query_family: str | None = Query(default=None),
    query_language: str | None = Query(default=None),
    capped_only: bool = Query(default=False),
    failed_only: bool = Query(default=False),
    expanded_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.list_cells(
        db,
        auth,
        run_id,
        status=status,
        region=region,
        query_family=query_family,
        query_language=query_language,
        capped_only=capped_only,
        failed_only=failed_only,
        expanded_only=expanded_only,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}/places/paged", response_model=MapsPlaceListResponse)
async def list_maps_census_admin_places(
    run_id: str,
    search: str | None = Query(default=None),
    client_eligibility: str | None = Query(default=None),
    lifecycle_status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.list_places(
        db,
        auth,
        run_id,
        search=search,
        client_eligibility=client_eligibility,
        lifecycle_status=lifecycle_status,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}/places/{place_id}", response_model=MapsPlaceDetail)
async def get_maps_census_admin_place_detail(
    run_id: str,
    place_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.get_place_detail(db, auth, run_id, place_id)


@router.post("/runs/{run_id}/pause", response_model=MapsCampaignActionResponse)
async def pause_maps_census_run(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.pause_run(db, auth, run_id)


@router.post("/runs/{run_id}/resume", response_model=MapsCampaignActionResponse)
async def resume_maps_census_run(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.resume_run(db, auth, run_id)


@router.post("/runs/{run_id}/cancel", response_model=MapsCampaignActionResponse)
async def cancel_maps_census_run(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.cancel_run(db, auth, run_id)


@router.post("/runs/{run_id}/recover", response_model=MapsCampaignActionResponse)
async def recover_maps_census_run(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Re-enqueue the census job for a failed/stalled run (resumes, never re-plans)."""
    return await maps_admin_service.recover_run(db, auth, run_id)


@router.post("/runs/{run_id}/retry-failed-cells", response_model=MapsCampaignActionResponse)
async def retry_maps_census_failed_cells(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.retry_failed_cells(db, auth, run_id)


@router.post("/runs/{run_id}/retry-websites", response_model=MapsCampaignActionResponse)
async def retry_maps_census_websites(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.retry_websites(db, auth, run_id)


@router.post("/runs/{run_id}/retry-enrichment", response_model=MapsCampaignActionResponse)
async def retry_maps_census_enrichment(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.retry_enrichment(db, auth, run_id)


@router.post("/runs/{run_id}/pause-enrichment", response_model=MapsCampaignActionResponse)
async def pause_maps_census_enrichment(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.pause_enrichment(db, auth, run_id)


@router.post("/runs/{run_id}/recover-enrichment", response_model=MapsCampaignActionResponse)
async def recover_maps_census_enrichment(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.recover_enrichment(db, auth, run_id)


@router.post("/runs/{run_id}/keep-drop", response_model=MapsCampaignActionResponse)
async def run_maps_census_keep_drop(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Run the strict keep/drop gate over existing places (no rediscovery)."""
    return await maps_admin_service.run_keep_drop(db, auth, run_id)


@router.post("/runs/{run_id}/re-enrich-keeps", response_model=MapsCampaignActionResponse)
async def re_enrich_maps_census_keeps(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reset keep places to pending and re-run detail enrichment (e.g. for pricing)."""
    return await maps_admin_service.re_enrich_keeps(db, auth, run_id)


@router.post("/runs/{run_id}/reconcile-finalization")
async def reconcile_maps_census_finalization(
    run_id: str,
    force: bool = Query(default=False),
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reconcile campaign stage statuses and cell counters without reprocessing."""
    return await maps_admin_service.reconcile_finalization(db, auth, run_id, force=force)


@router.get("/runs/{run_id}/enrichment-projection")
async def get_maps_enrichment_projection(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.enrichment_cost_projection(db, auth, run_id)


@router.post("/runs/{run_id}/places/{place_id}/review", response_model=MapsPlaceDetail)
async def apply_maps_census_place_review(
    run_id: str,
    place_id: str,
    payload: MapsPlaceReviewRequest,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.apply_review_action(db, auth, run_id, place_id, payload)


@router.get("/runs/{run_id}/export-summary", response_model=MapsExportSummaryResponse)
async def get_maps_census_export_summary(
    run_id: str,
    auth: AuthContext = Depends(require_org_admin),
    db: AsyncSession = Depends(get_db),
):
    return await maps_admin_service.get_export_summary(db, auth, run_id)
