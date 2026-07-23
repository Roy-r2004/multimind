from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    ContentLabelCreateRequest,
    ContentLabelResponse,
    ContentLabelUpdateRequest,
    MessageResponse,
)
from app.services.saved_document_service import saved_document_service

router = APIRouter()


@router.get("", response_model=list[ContentLabelResponse])
async def list_labels(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_document_service.list_labels(db, auth)


@router.post("", response_model=ContentLabelResponse, status_code=status.HTTP_201_CREATED)
async def create_label(
    data: ContentLabelCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_document_service.create_label(db, auth, data.name)


@router.patch("/{label_id}", response_model=ContentLabelResponse)
async def rename_label(
    label_id: UUID,
    data: ContentLabelUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_document_service.rename_label(db, auth, str(label_id), data.name)


@router.delete("/{label_id}", response_model=MessageResponse)
async def delete_label(
    label_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await saved_document_service.delete_label(db, auth, str(label_id))
    return MessageResponse(message="Label deleted")
