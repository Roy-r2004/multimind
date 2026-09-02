from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    PromptBuilderContextResponse,
    PromptBuilderImproveRequest,
    PromptBuilderImproveResponse,
    PromptBuilderRefineRequest,
    PromptBuilderRefineResponse,
)
from app.services.prompt_builder_service import prompt_builder_service

router = APIRouter()


@router.post("/context", response_model=PromptBuilderContextResponse)
async def prompt_builder_context(
    data: PromptBuilderRefineRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Estimate the next lossless Builder request against real model limits."""
    return await prompt_builder_service.context(
        db, auth, messages=data.messages, model_set_id=data.model_set_id
    )


@router.post("/improve", response_model=PromptBuilderImproveResponse)
async def improve_prompt(
    data: PromptBuilderImproveRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    """Legacy one-shot improve. Prefer /refine for the multi-turn mini-chat."""
    return await prompt_builder_service.improve(auth, data.raw_prompt)


@router.post("/refine", response_model=PromptBuilderRefineResponse)
async def refine_prompt(
    data: PromptBuilderRefineRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Ephemeral Prompt Builder council. Does not create Chat/Turn rows."""
    return await prompt_builder_service.refine(
        db,
        auth,
        messages=data.messages,
        model_set_id=data.model_set_id,
    )
