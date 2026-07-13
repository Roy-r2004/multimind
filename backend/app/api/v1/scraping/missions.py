"""Scraping mission endpoints."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    MessageResponse,
    ScrapingBlueprintGenerateRequest,
    ScrapingBlueprintResponse,
    ScrapingMissionCreate,
    ScrapingMissionDetail,
    ScrapingMissionSummary,
    ScrapingMissionUpdate,
)
from app.services.scraping.blueprint_service import blueprint_service
from app.services.scraping.mission_service import mission_service

router = APIRouter()


@router.post("", response_model=ScrapingMissionDetail, status_code=status.HTTP_201_CREATED)
async def create_mission(
    data: ScrapingMissionCreate,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_service.create_mission(db, auth, data)


@router.get("", response_model=list[ScrapingMissionSummary])
async def list_missions(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_service.list_missions(db, auth)


@router.get("/{mission_id}", response_model=ScrapingMissionDetail)
async def get_mission(
    mission_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_service.get_mission(db, auth, mission_id)


@router.patch("/{mission_id}", response_model=ScrapingMissionDetail)
async def update_mission(
    mission_id: str,
    data: ScrapingMissionUpdate,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_service.update_mission(db, auth, mission_id, data)


@router.delete("/{mission_id}", response_model=MessageResponse)
async def delete_mission(
    mission_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await mission_service.delete_mission(db, auth, mission_id)
    return MessageResponse(message="Scraping mission deleted")


@router.post(
    "/{mission_id}/blueprints",
    response_model=ScrapingBlueprintResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_blueprint(
    mission_id: str,
    _data: ScrapingBlueprintGenerateRequest | None = None,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.generate_blueprint(db, auth, mission_id)


@router.get("/{mission_id}/blueprints", response_model=list[ScrapingBlueprintResponse])
async def list_blueprints(
    mission_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.list_blueprints(db, auth, mission_id)
