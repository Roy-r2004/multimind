from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScrapingBlueprint, ScrapingMission, ScrapingRun
from app.schemas.api import ScrapingBlueprintContent, ScrapingExecutionCreate
from app.services.scraping.blueprint_service import blueprint_service
from app.services.scraping.execution_service import execution_service
from app.services.scraping.run_service import run_service
from conftest import create_model_set, valid_blueprint


class FakeOrchestrator:
    async def generate(self, mission, model_set, previous_blueprint=None, change_instructions=None):
        return ScrapingBlueprintContent.model_validate(valid_blueprint())


async def create_mission(db: AsyncSession, auth) -> ScrapingMission:
    model_set = await create_model_set(db, auth, slug="policy-blueprint")
    mission = ScrapingMission(
        org_id=auth.org_id,
        created_by=auth.user.id,
        model_set_id=model_set.slug,
        title="Private residential rehab sweep",
        original_prompt="Map private residential rehabilitation facilities in France",
        country_code="FR",
        country_name="France",
    )
    db.add(mission)
    await db.flush()
    return mission


@pytest.mark.asyncio
async def test_blueprint_generation_enriches_policy_snapshot_and_country_blueprint(
    db: AsyncSession, auth, monkeypatch
):
    mission = await create_mission(db, auth)
    monkeypatch.setattr(
        "app.services.scraping.blueprint_service.get_blueprint_orchestrator",
        lambda: FakeOrchestrator(),
    )

    blueprint = await blueprint_service.generate_blueprint(db, auth, mission.id)

    assert blueprint.blueprint_json is not None
    assert blueprint.blueprint_json.policy_snapshot["policy_id"] == "scraper-policy-v1"
    assert blueprint.blueprint_json.policy_snapshot["mission_profile"] == "private_residential"
    assert blueprint.blueprint_json.country_blueprint["country_code"] == "FR"
    assert blueprint.blueprint_json.country_blueprint["country_name"] == "France"
    assert blueprint.blueprint_json.country_blueprint["languages"]
    assert blueprint.blueprint_json.country_blueprint["phone_patterns"]


@pytest.mark.asyncio
async def test_execution_creation_locks_requested_policy_snapshot(
    db: AsyncSession, auth, monkeypatch
):
    mission = await create_mission(db, auth)
    monkeypatch.setattr(
        "app.services.scraping.blueprint_service.get_blueprint_orchestrator",
        lambda: FakeOrchestrator(),
    )
    blueprint = await blueprint_service.generate_blueprint(db, auth, mission.id)
    approved = await blueprint_service.approve_blueprint(db, auth, blueprint.id)
    run = await run_service.plan_team(db, auth, mission.id)

    async def fake_enqueue(execution_id):
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", fake_enqueue)
    execution = await execution_service.create_execution(
        db,
        auth,
        run.id,
        ScrapingExecutionCreate(
            execution_type="initial_full_country",
            mission_profile="private_residential",
        ),
    )
    detail = await execution_service.get_detail(db, auth, execution.id)

    assert detail.execution.mission_profile == "private_residential"
    assert detail.policy_snapshot is not None
    assert detail.policy_snapshot["policy_id"] == "scraper-policy-v1"
    assert detail.policy_snapshot["mission_profile"] == "private_residential"
    assert detail.policy_snapshot["security"]["robots_policy"] == "respect"
    assert detail.country_profile is not None
    assert detail.country_profile["policy_snapshot"]["mission_profile"] == "private_residential"

    blueprint_row = await db.get(ScrapingBlueprint, approved.id)
    assert blueprint_row is not None
    run_row = await db.get(ScrapingRun, run.id)
    assert run_row is not None
