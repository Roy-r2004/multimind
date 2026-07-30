"""Standalone Google Places Maps census endpoints."""

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
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
