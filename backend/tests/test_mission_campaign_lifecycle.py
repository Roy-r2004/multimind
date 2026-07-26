import pytest
from conftest import create_model_set, valid_blueprint
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.db.models import (
    RehabilitationFacility,
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingEvent,
    ScrapingExecution,
    ScrapingMission,
    ScrapingMissionStatus,
    ScrapingRun,
    ScrapingRunAgent,
    ScrapingRunStatus,
)
from app.schemas.api import MissionCampaignStartRequest, ScrapingMissionCreate
from app.services.scraping.execution_service import execution_service
from app.services.scraping.mission_service import mission_service


async def _approved_mission_with_team_plan(
    db: AsyncSession, auth: AuthContext
) -> tuple[ScrapingMission, ScrapingBlueprint]:
    await create_model_set(db, auth)
    mission = await mission_service.create_mission(
        db,
        auth,
        ScrapingMissionCreate(
            title="Campaign mission",
            country_code="LB",
            original_prompt="Find facilities",
            model_set_id="research-set",
        ),
    )
    blueprint = ScrapingBlueprint(
        mission_id=mission.id,
        version=7,
        status=ScrapingBlueprintStatus.APPROVED,
        blueprint_json=valid_blueprint(),
        model_set_id="research-set",
    )
    db.add(blueprint)
    await db.flush()
    mission_row = await db.get(ScrapingMission, mission.id)
    assert mission_row is not None
    mission_row.active_blueprint_id = blueprint.id
    mission_row.status = ScrapingMissionStatus.APPROVED
    run = ScrapingRun(
        organization_id=auth.org_id,
        mission_id=mission.id,
        blueprint_id=blueprint.id,
        model_set_id="research-set",
        status=ScrapingRunStatus.PLANNED,
    )
    db.add(run)
    await db.flush()
    db.add(
        ScrapingRunAgent(
            run_id=run.id,
            sequence=1,
            name="Planner",
            role="planner",
            purpose="Prepare campaign checkpoints",
            instructions="Run deterministic checkpoints only.",
            model_id="gpt-4.1",
        )
    )
    await db.commit()
    return mission_row, blueprint


@pytest.mark.asyncio
async def test_mission_campaign_start_snapshots_approved_blueprint_provenance(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)
    queued: list[tuple[str, str]] = []

    async def fake_enqueue(execution_id: str, *, job_name: str) -> None:
        queued.append((execution_id, job_name))

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)

    assert summary.execution_type == "mission_campaign"
    assert summary.mode == "mock"
    assert summary.execution_origin == "mission_campaign_mock"
    assert summary.blueprint_version_snapshot == blueprint.version
    assert summary.created_by == auth.user.id
    assert queued == [(summary.id, "run_mission_campaign_mock")]
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    event = (
        await db.execute(
            select(ScrapingEvent).where(
                ScrapingEvent.execution_id == summary.id,
                ScrapingEvent.event_type == "mission_campaign_queued",
            )
        )
    ).scalar_one()
    assert event.metadata_json["external_calls"] is False
    assert event.metadata_json["facility_generation"] is False
    detail = await execution_service.get_mission_campaign_detail(
        db, auth, mission.id, summary.id
    )
    assert detail.mock is True
    assert detail.execution.execution_origin == "mission_campaign_mock"
    facilities = await db.execute(
        select(RehabilitationFacility).where(RehabilitationFacility.execution_id == summary.id)
    )
    assert facilities.scalars().all() == []


@pytest.mark.asyncio
async def test_mission_campaign_start_rejects_non_approved_active_blueprint(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)
    blueprint.status = ScrapingBlueprintStatus.DRAFT
    await db.commit()

    async def unexpected_enqueue(*args, **kwargs) -> None:
        raise AssertionError("A campaign with invalid provenance must not be queued.")

    monkeypatch.setattr(execution_service, "enqueue_execution", unexpected_enqueue)
    with pytest.raises(Exception, match="active approved blueprint"):
        await execution_service.start_mission_campaign(db, auth, mission.id)


def test_mission_campaign_start_schema_allows_mock_only() -> None:
    assert MissionCampaignStartRequest().mode == "mock"
    with pytest.raises(PydanticValidationError):
        MissionCampaignStartRequest(mode="real")
