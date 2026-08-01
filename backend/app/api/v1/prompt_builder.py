from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import PromptBuilderImproveRequest, PromptBuilderImproveResponse
from app.services.prompt_builder_service import prompt_builder_service

router = APIRouter()


@router.post("/improve", response_model=PromptBuilderImproveResponse)
async def improve_prompt(
    data: PromptBuilderImproveRequest,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await prompt_builder_service.improve(auth, data.raw_prompt, db)
