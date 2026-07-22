from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import SavedVerdictListItemResponse
from app.services.saved_verdict_service import saved_verdict_service

router = APIRouter()


@router.get("", response_model=list[SavedVerdictListItemResponse])
async def list_saved_verdicts(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_verdict_service.list_saved_verdicts(db, auth)
