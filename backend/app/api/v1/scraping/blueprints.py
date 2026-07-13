"""Scraping blueprint endpoints."""

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    ScrapingBlueprintApproveRequest,
    ScrapingBlueprintChangeRequest,
    ScrapingBlueprintRejectRequest,
    ScrapingBlueprintRenameRequest,
    ScrapingBlueprintResponse,
)
from app.services.scraping.blueprint_service import blueprint_service

router = APIRouter()


@router.get("/{blueprint_id}", response_model=ScrapingBlueprintResponse)
async def get_blueprint(
    blueprint_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.get_blueprint(db, auth, blueprint_id)


@router.patch("/{blueprint_id}/rename", response_model=ScrapingBlueprintResponse)
async def rename_blueprint(
    blueprint_id: str,
    data: ScrapingBlueprintRenameRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.rename_blueprint(db, auth, blueprint_id, data)


@router.delete("/{blueprint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_blueprint(
    blueprint_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await blueprint_service.delete_blueprint(db, auth, blueprint_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{blueprint_id}/approve", response_model=ScrapingBlueprintResponse)
async def approve_blueprint(
    blueprint_id: str,
    _data: ScrapingBlueprintApproveRequest | None = None,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.approve_blueprint(db, auth, blueprint_id)


@router.post("/{blueprint_id}/reject", response_model=ScrapingBlueprintResponse)
async def reject_blueprint(
    blueprint_id: str,
    data: ScrapingBlueprintRejectRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.reject_blueprint(db, auth, blueprint_id, data)


@router.post("/{blueprint_id}/request-changes", response_model=ScrapingBlueprintResponse)
async def request_blueprint_changes(
    blueprint_id: str,
    data: ScrapingBlueprintChangeRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.request_changes(db, auth, blueprint_id, data)
