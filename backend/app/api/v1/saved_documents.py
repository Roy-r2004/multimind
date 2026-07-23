from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    MessageResponse,
    SavedDocumentCreateRequest,
    SavedDocumentResponse,
    SavedDocumentSuggestRequest,
    SavedDocumentSuggestResponse,
    SavedDocumentUpdateRequest,
)
from app.services.saved_document_service import saved_document_service

router = APIRouter()


@router.get("", response_model=list[SavedDocumentResponse])
async def search_documents(
    q: str | None = Query(default=None),
    label_id: str | None = Query(default=None),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_document_service.search(db, auth, q=q, label_id=label_id)


@router.post("/suggest", response_model=SavedDocumentSuggestResponse)
async def suggest_document(
    data: SavedDocumentSuggestRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_document_service.suggest(db, auth, data.turn_id)


@router.post("", response_model=SavedDocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_document(
    data: SavedDocumentCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_document_service.create_from_turn(
        db,
        auth,
        turn_id=data.turn_id,
        name=data.name,
        label_ids=data.label_ids,
        label_names=data.label_names,
    )


@router.get("/{document_id}", response_model=SavedDocumentResponse)
async def get_document(
    document_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_document_service.get_document(db, auth, str(document_id))


@router.patch("/{document_id}", response_model=SavedDocumentResponse)
async def update_document(
    document_id: UUID,
    data: SavedDocumentUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_document_service.update_document(
        db,
        auth,
        str(document_id),
        name=data.name,
        label_ids=data.label_ids,
    )


@router.delete("/{document_id}", response_model=MessageResponse)
async def delete_document(
    document_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await saved_document_service.delete_document(db, auth, str(document_id))
    return MessageResponse(message="Document deleted")
