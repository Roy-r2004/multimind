import pytest
from conftest import create_model_set, valid_blueprint
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_country_blueprint_foundation import (
    valid_structured_blueprint,
    valid_structured_blueprint_v2,
)

from app.core.dependencies import AuthContext
from app.core.exceptions import ValidationError
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
from app.schemas.scraping_execution_plan import FrozenExecutionPlanV2, parse_frozen_execution_plan
from app.services.scraping.blueprint_execution_plan_service import sha256_hex
from app.services.scraping.execution_service import execution_service
from app.services.scraping.mission_service import mission_service


def lebanon_structured_blueprint() -> dict:
    payload = valid_structured_blueprint_v2()
    payload["country_dossier"] = {
        "country_name": "Lebanon",
        "country_iso3": "LBN",
        "continent": "Asia",
    }
    payload["regions"] = ["Beirut"]
    payload["languages"] = ["Arabic", "English"]
    payload["language_profiles"] = [
        {"name": "Arabic", "code": "ar", "script": "Arab"},
        {"name": "English", "code": "en", "script": "Latn"},
    ]
    payload["important_cities"] = [{"name": "Beirut", "region_name": "Beirut"}]
    payload["local_terminology"] = ["rehabilitation", "علاج الإدمان"]
    payload["inpatient_residential_terminology"] = ["inpatient", "residential"]
    payload["private_paid_terminology"] = ["private", "paid"]
    payload["addiction_categories"] = ["alcohol", "opioids"]
    payload["query_matrix"] = [
        {
            "query": "Lebanon inpatient addiction rehabilitation",
            "language": "English",
            "purpose": "discovery",
        }
    ]
    payload["region_coverage_plan"] = [
        {"region_name": "Beirut", "coverage_actions": ["Search registry"]}
    ]
    # Distinct regulatory/commercial URLs avoid Step 2 source-category conflicts.
    payload["regulatory_sources"] = [
        {"url": "https://example.test/lb-reg", "title": "LB Regulatory"}
    ]
    payload["commercial_sources"] = [
        {"url": "https://example.test/lb-com", "title": "LB Commercial"}
    ]
    return payload


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
        structured_blueprint=lebanon_structured_blueprint(),
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
    assert summary.execution_plan_schema_version == "2"
    assert summary.execution_plan_hash
    assert summary.execution_plan_compiled_at is not None
    assert summary.created_by == auth.user.id
    assert queued == [(summary.id, "run_mission_campaign_mock")]
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    assert execution.blueprint_snapshot_json is not None
    assert execution.frozen_execution_plan_json is not None
    plan = parse_frozen_execution_plan(execution.frozen_execution_plan_json)
    assert isinstance(plan, FrozenExecutionPlanV2)
    assert sha256_hex(plan.model_dump(mode="json")) == execution.execution_plan_hash
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
    assert event.metadata_json["execution_plan_hash"] == execution.execution_plan_hash
    detail = await execution_service.get_mission_campaign_detail(
        db, auth, mission.id, summary.id
    )
    assert detail.mock is True
    assert detail.execution.execution_origin == "mission_campaign_mock"
    assert detail.execution.execution_plan_hash == execution.execution_plan_hash
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


@pytest.mark.asyncio
async def test_mission_campaign_compilation_failure_creates_no_execution(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)
    blueprint.structured_blueprint = {
        **lebanon_structured_blueprint(),
        "regions": [],
    }
    await db.commit()

    async def unexpected_enqueue(*args, **kwargs) -> None:
        raise AssertionError("Compilation failure must not enqueue a worker.")

    monkeypatch.setattr(execution_service, "enqueue_execution", unexpected_enqueue)
    with pytest.raises(ValidationError):
        await execution_service.start_mission_campaign(db, auth, mission.id)
    count = (
        await db.execute(
            select(ScrapingExecution).where(ScrapingExecution.mission_id == mission.id)
        )
    ).scalars().all()
    assert count == []


@pytest.mark.asyncio
async def test_mission_campaign_rejects_approved_v1_before_persistence(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)
    v1 = valid_structured_blueprint()
    v1["country_dossier"] = {
        "country_name": "Lebanon",
        "country_iso3": "LBN",
        "continent": "Asia",
    }
    v1["regions"] = ["Beirut"]
    v1["languages"] = ["Arabic", "English"]
    v1["query_matrix"] = [
        {
            "query": "Lebanon inpatient addiction rehabilitation",
            "language": "English",
            "purpose": "discovery",
        }
    ]
    v1["region_coverage_plan"] = [
        {"region_name": "Beirut", "coverage_actions": ["Search registry"]}
    ]
    blueprint.structured_blueprint = v1
    await db.commit()

    async def unexpected_enqueue(*args, **kwargs) -> None:
        raise AssertionError("v1 campaign must not enqueue.")

    monkeypatch.setattr(execution_service, "enqueue_execution", unexpected_enqueue)
    with pytest.raises(ValidationError, match="schema version 2"):
        await execution_service.start_mission_campaign(db, auth, mission.id)
    executions = (
        await db.execute(
            select(ScrapingExecution).where(ScrapingExecution.mission_id == mission.id)
        )
    ).scalars().all()
    assert executions == []
    events = (
        await db.execute(
            select(ScrapingEvent).where(
                ScrapingEvent.execution_id.in_(
                    select(ScrapingExecution.id).where(ScrapingExecution.mission_id == mission.id)
                )
            )
        )
    ).scalars().all()
    assert events == []


@pytest.mark.asyncio
async def test_mission_campaign_rejects_incomplete_v2_before_persistence(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)
    incomplete = lebanon_structured_blueprint()
    incomplete["addiction_categories"] = []
    blueprint.structured_blueprint = incomplete
    await db.commit()

    async def unexpected_enqueue(*args, **kwargs) -> None:
        raise AssertionError("incomplete v2 campaign must not enqueue.")

    monkeypatch.setattr(execution_service, "enqueue_execution", unexpected_enqueue)
    with pytest.raises(ValidationError):
        await execution_service.start_mission_campaign(db, auth, mission.id)
    executions = (
        await db.execute(
            select(ScrapingExecution).where(ScrapingExecution.mission_id == mission.id)
        )
    ).scalars().all()
    assert executions == []


def test_mission_campaign_start_schema_allows_mock_only() -> None:
    assert MissionCampaignStartRequest().mode == "mock"
    with pytest.raises(PydanticValidationError):
        MissionCampaignStartRequest(mode="real")
