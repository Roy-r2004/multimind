"""Standalone Google Places Maps census endpoints."""

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    MapsCensusCellItem,
    MapsCensusRunCreate,
    MapsCensusRunDetail,
    MapsCensusRunSummary,
    MapsPlaceItem,
)
from app.services.scraping.maps_census_service import maps_census_service

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


@router.get("/runs/{run_id}/cells", response_model=list[MapsCensusCellItem])
async def list_maps_census_cells(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await maps_census_service.list_cells(db, auth, run_id)


@router.get("/runs/{run_id}/places", response_model=list[MapsPlaceItem])
async def list_maps_census_places(
    run_id: str,
    relevant_only: bool = Query(default=False),
    with_website_only: bool = Query(default=False),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await maps_census_service.list_places(
        db,
        auth,
        run_id,
        relevant_only=relevant_only,
        with_website_only=with_website_only,
    )


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
