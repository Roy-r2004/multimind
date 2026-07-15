"""Scraping run endpoints."""

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import ScrapingExecutionCreate, ScrapingExecutionSummary, ScrapingRunDetail
from app.services.scraping.execution_service import execution_service
from app.services.scraping.run_service import run_service

router = APIRouter()


@router.post(
    "/{run_id}/executions",
    response_model=ScrapingExecutionSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_execution(
    run_id: str,
    data: ScrapingExecutionCreate,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await execution_service.create_execution(db, auth, run_id, data)


@router.get("/{run_id}/executions", response_model=list[ScrapingExecutionSummary])
async def list_executions(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await execution_service.list_executions(db, auth, run_id)


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
