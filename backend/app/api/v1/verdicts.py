from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import SavedVerdictSaveResponse, SavedVerdictUnsaveResponse
from app.services.saved_verdict_service import saved_verdict_service

router = APIRouter()


@router.post("/{verdict_id}/save", response_model=SavedVerdictSaveResponse)
async def save_verdict(
    verdict_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_verdict_service.save_verdict(db, auth, str(verdict_id))


@router.delete("/{verdict_id}/save", response_model=SavedVerdictUnsaveResponse)
async def unsave_verdict(
    verdict_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_verdict_service.unsave_verdict(db, auth, str(verdict_id))
