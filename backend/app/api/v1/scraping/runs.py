"""Scraping run endpoints."""

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import ScrapingRunDetail
from app.services.scraping.run_service import run_service

router = APIRouter()


@router.get("/{run_id}", response_model=ScrapingRunDetail)
async def get_run(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await run_service.get_run(db, auth, run_id)


@router.post("/{run_id}/cancel", response_model=ScrapingRunDetail)
async def cancel_run(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await run_service.cancel_run(db, auth, run_id)


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await run_service.delete_run(db, auth, run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
