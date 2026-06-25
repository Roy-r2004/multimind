from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import BrainResponse
from app.services.brain_service import brain_service

router = APIRouter()


@router.get("", response_model=BrainResponse)
async def get_brain(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await brain_service.get_brain(db, auth)
