from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    MessageResponse,
    ModelSetCreateRequest,
    ModelSetResponse,
    ModelSetUpdateRequest,
)
from app.services.domain_service import model_set_service

router = APIRouter()


@router.get("", response_model=list[ModelSetResponse])
async def list_model_sets(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await model_set_service.list(db, auth)


@router.post("", response_model=ModelSetResponse, status_code=status.HTTP_201_CREATED)
async def create_model_set(
    data: ModelSetCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await model_set_service.create(db, auth, data)


@router.patch("/{slug}", response_model=ModelSetResponse)
async def update_model_set(
    slug: str,
    data: ModelSetUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await model_set_service.update(db, auth, slug, data)


@router.delete("/{slug}", response_model=MessageResponse)
async def delete_model_set(
    slug: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await model_set_service.delete(db, auth, slug)
    return MessageResponse(message="Model set deleted")
