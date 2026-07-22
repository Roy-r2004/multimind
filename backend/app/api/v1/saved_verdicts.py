from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    SavedVerdictDeleteResponse,
    SavedVerdictListItemResponse,
    SavedVerdictPurgeResponse,
)
from app.services.saved_verdict_service import saved_verdict_service

router = APIRouter()


@router.get("", response_model=list[SavedVerdictListItemResponse])
async def list_saved_verdicts(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await saved_verdict_service.list_saved_verdicts(db, auth)


@router.delete("", response_model=SavedVerdictPurgeResponse)
async def purge_organization_saved_verdicts(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        response = await saved_verdict_service.purge_organization_saved_verdicts(db, auth)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return response


@router.delete("/{saved_verdict_id}", response_model=SavedVerdictDeleteResponse)
async def delete_saved_verdict(
    saved_verdict_id: UUID,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    try:
        response = await saved_verdict_service.delete_saved_verdict(db, auth, str(saved_verdict_id))
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return response
