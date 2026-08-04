from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    MessageResponse,
    SavedPromptCreateRequest,
    SavedPromptResponse,
    SavedPromptUpdateRequest,
)
from app.services.saved_prompt_service import saved_prompt_service

router = APIRouter()


@router.get("", response_model=list[SavedPromptResponse])
async def search_prompts(
    q: str | None = Query(default=None),
    label_id: str | None = Query(default=None),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_prompt_service.search(db, auth, q=q, label_id=label_id)


@router.post("", response_model=SavedPromptResponse, status_code=status.HTTP_201_CREATED)
async def create_prompt(
    data: SavedPromptCreateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_prompt_service.create_from_turn(
        db,
        auth,
        turn_id=data.turn_id,
        prompt_text=data.prompt_text,
        title=data.title,
        label_ids=data.label_ids,
        label_names=data.label_names,
    )


@router.get("/{prompt_id}", response_model=SavedPromptResponse)
async def get_prompt(
    prompt_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_prompt_service.get_prompt(db, auth, str(prompt_id))


@router.patch("/{prompt_id}", response_model=SavedPromptResponse)
async def update_prompt(
    prompt_id: UUID,
    data: SavedPromptUpdateRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_prompt_service.update_prompt(
        db,
        auth,
        str(prompt_id),
        title=data.title,
        prompt_text=data.prompt_text,
        label_ids=data.label_ids,
    )


@router.delete("/{prompt_id}", response_model=MessageResponse)
async def delete_prompt(
    prompt_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await saved_prompt_service.delete_prompt(db, auth, str(prompt_id))
    return MessageResponse(message="Prompt deleted")
