"""Scraping blueprint endpoints."""

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    ScrapingBlueprintApproveRequest,
    ScrapingBlueprintChangeRequest,
    ScrapingBlueprintEditRequest,
    ScrapingBlueprintRejectRequest,
    ScrapingBlueprintRenameRequest,
    ScrapingBlueprintResponse,
    ScrapingBlueprintRevisionRequest,
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


@router.patch("/{blueprint_id}", response_model=ScrapingBlueprintResponse)
async def edit_blueprint(
    blueprint_id: str,
    data: ScrapingBlueprintEditRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.edit_blueprint(
        db,
        auth,
        blueprint_id,
        human_readable_blueprint=data.human_readable_blueprint,
        structured_blueprint=data.structured_blueprint.model_dump(mode="json"),
    )


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


@router.post("/{blueprint_id}/request-revision", response_model=ScrapingBlueprintResponse, status_code=202)
async def request_blueprint_revision(
    blueprint_id: str,
    data: ScrapingBlueprintRevisionRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.request_changes(
        db,
        auth,
        blueprint_id,
        ScrapingBlueprintChangeRequest(change_instructions=data.revision_instruction),
    )


@router.post("/{blueprint_id}/regenerate", response_model=ScrapingBlueprintResponse, status_code=202)
async def regenerate_blueprint(
    blueprint_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.regenerate_blueprint(db, auth, blueprint_id)


@router.post("/{blueprint_id}/discard", response_model=ScrapingBlueprintResponse)
async def discard_blueprint(
    blueprint_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.discard_blueprint(db, auth, blueprint_id)
