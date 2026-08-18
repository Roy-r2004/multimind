from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    PlaybookObservationResponse,
    PlaybookPendingResponse,
    PlaybookResponse,
    PlaybookRunResponse,
)
from app.services.playbook_generation_service import playbook_generation_service
from app.services.playbook_pending_service import playbook_pending_service
from app.services.playbook_service import playbook_service

router = APIRouter()


@router.get("/me", response_model=PlaybookResponse)
async def get_my_playbook(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    playbook = await playbook_service.get_or_create_for_current_user(db, auth)
    return playbook_service.playbook_response(playbook)


@router.get("/me/observations", response_model=list[PlaybookObservationResponse])
async def list_my_playbook_observations(
    category: str | None = Query(default=None),
    status: str | None = Query(default=None),
    include_excluded: bool = Query(default=False),
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await playbook_service.list_observations_for_current_user(
        db,
        auth,
        category=category,
        status=status,
        include_excluded=include_excluded,
    )


@router.post(
    "/me/generate",
    response_model=PlaybookRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_my_playbook(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await playbook_generation_service.start_full_generation(db, auth)


@router.get("/me/pending", response_model=PlaybookPendingResponse)
async def get_my_pending_playbook_sources(
    auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)
):
    return await playbook_pending_service.response(db, auth)


@router.post("/me/rerun", response_model=PlaybookRunResponse, status_code=status.HTTP_202_ACCEPTED)
async def rerun_my_playbook(
    auth: AuthContext = Depends(get_auth_context), db: AsyncSession = Depends(get_db)
):
    return await playbook_generation_service.start_incremental_generation(db, auth)


@router.get("/me/runs/latest", response_model=PlaybookRunResponse | None)
async def get_my_latest_playbook_run(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    """Return the newest run, or JSON ``null`` with HTTP 200 when none exists.

    Chosen over 204 so typed clients can poll a stable JSON body, matching
    optional-object responses rather than empty-list endpoints.
    """
    return await playbook_service.get_latest_run_for_current_user(db, auth)


@router.get("/me/runs/{run_id}", response_model=PlaybookRunResponse)
async def get_my_playbook_run(
    run_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await playbook_service.get_run_for_current_user(db, auth, run_id)
