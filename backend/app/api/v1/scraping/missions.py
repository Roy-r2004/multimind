"""Scraping mission endpoints."""

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.session import get_db
from app.schemas.api import (
    MissionCampaignStartRequest,
    ScrapingBlueprintGenerateRequest,
    ScrapingBlueprintResponse,
    ScrapingEventResponse,
    ScrapingExecutionDetail,
    ScrapingExecutionSummary,
    ScrapingMissionCreate,
    ScrapingMissionDetail,
    ScrapingMissionSummary,
    ScrapingMissionUpdate,
    ScrapingRunDetail,
    ScrapingRunSummary,
)
from app.services.scraping.blueprint_service import blueprint_service
from app.services.scraping.execution_service import execution_service
from app.services.scraping.mission_campaign_lifecycle_service import (
    mission_campaign_lifecycle_service,
)
from app.services.scraping.mission_service import mission_service
from app.services.scraping.run_service import run_service

router = APIRouter()


@router.post("", response_model=ScrapingMissionDetail, status_code=status.HTTP_201_CREATED)
async def create_mission(
    data: ScrapingMissionCreate,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_service.create_mission(db, auth, data)


@router.get("", response_model=list[ScrapingMissionSummary])
async def list_missions(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_service.list_missions(db, auth)


@router.get("/{mission_id}", response_model=ScrapingMissionDetail)
async def get_mission(
    mission_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_service.get_mission(db, auth, mission_id)


@router.patch("/{mission_id}", response_model=ScrapingMissionDetail)
async def update_mission(
    mission_id: str,
    data: ScrapingMissionUpdate,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_service.update_mission(db, auth, mission_id, data)


@router.delete("/{mission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mission(
    mission_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await mission_service.delete_mission(db, auth, mission_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{mission_id}/blueprints",
    response_model=ScrapingBlueprintResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_blueprint(
    mission_id: str,
    _data: ScrapingBlueprintGenerateRequest | None = None,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.generate_blueprint(db, auth, mission_id)


@router.post(
    "/{mission_id}/blueprints/generate",
    response_model=ScrapingBlueprintResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_blueprint_generation(
    mission_id: str,
    _data: ScrapingBlueprintGenerateRequest | None = None,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.generate_blueprint(db, auth, mission_id)


@router.get(
    "/{mission_id}/blueprints/{blueprint_id}/status",
    response_model=ScrapingBlueprintResponse,
)
async def get_blueprint_generation_status(
    mission_id: str,
    blueprint_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    blueprint = await blueprint_service.get_blueprint_row(db, auth, blueprint_id)
    if blueprint.mission_id != mission_id:
        from app.core.exceptions import NotFoundError

        raise NotFoundError("ScrapingBlueprint", blueprint_id)
    return blueprint_service._response(blueprint)


@router.get("/{mission_id}/blueprints", response_model=list[ScrapingBlueprintResponse])
async def list_blueprints(
    mission_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.list_blueprints(db, auth, mission_id)


@router.get("/{mission_id}/blueprints/latest", response_model=ScrapingBlueprintResponse)
async def latest_blueprint(
    mission_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.latest_blueprint(db, auth, mission_id)


@router.get("/{mission_id}/blueprints/active", response_model=ScrapingBlueprintResponse)
async def active_blueprint(
    mission_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await blueprint_service.active_blueprint(db, auth, mission_id)


@router.post("/{mission_id}/runs/plan", response_model=ScrapingRunDetail)
async def plan_scraping_team(
    mission_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await run_service.plan_team(db, auth, mission_id)


@router.get("/{mission_id}/runs", response_model=list[ScrapingRunSummary])
async def list_scraping_runs(
    mission_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await run_service.list_runs(db, auth, mission_id)


@router.post(
    "/{mission_id}/executions",
    response_model=ScrapingExecutionSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_mission_campaign(
    mission_id: str,
    _data: MissionCampaignStartRequest | None = None,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_campaign_lifecycle_service.start(db, auth, mission_id)


@router.get(
    "/{mission_id}/executions",
    response_model=list[ScrapingExecutionSummary],
)
async def list_mission_campaigns(
    mission_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_campaign_lifecycle_service.list(db, auth, mission_id)


@router.get(
    "/{mission_id}/executions/{execution_id}",
    response_model=ScrapingExecutionDetail,
)
async def get_mission_campaign_status(
    mission_id: str,
    execution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_campaign_lifecycle_service.status(db, auth, mission_id, execution_id)


@router.get(
    "/{mission_id}/executions/{execution_id}/events",
    response_model=list[ScrapingEventResponse],
)
async def list_mission_campaign_events(
    mission_id: str,
    execution_id: str,
    after_sequence: int | None = None,
    limit: int = 200,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    await execution_service._mission_campaign_row(db, auth, mission_id, execution_id)
    return await execution_service.list_events(
        db, auth, execution_id, after_sequence=after_sequence, limit=limit
    )


@router.post(
    "/{mission_id}/executions/{execution_id}/pause",
    response_model=ScrapingExecutionSummary,
)
async def pause_mission_campaign(
    mission_id: str,
    execution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_campaign_lifecycle_service.pause(db, auth, mission_id, execution_id)


@router.post(
    "/{mission_id}/executions/{execution_id}/resume",
    response_model=ScrapingExecutionSummary,
)
async def resume_mission_campaign(
    mission_id: str,
    execution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_campaign_lifecycle_service.resume(db, auth, mission_id, execution_id)


@router.post(
    "/{mission_id}/executions/{execution_id}/cancel",
    response_model=ScrapingExecutionSummary,
)
async def cancel_mission_campaign(
    mission_id: str,
    execution_id: str,
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await mission_campaign_lifecycle_service.cancel(db, auth, mission_id, execution_id)
