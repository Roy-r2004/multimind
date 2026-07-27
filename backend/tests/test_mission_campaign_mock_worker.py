"""Deterministic, local-only Phase 2A campaign worker coverage."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_mission_campaign_lifecycle import (
    _approved_mission_with_team_plan,
    lebanon_structured_blueprint,
)

from app.db.models import (
    RehabilitationFacility,
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingEvent,
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingMissionStatus,
)
from app.services.scraping import mission_campaign_mock_worker
from app.services.scraping.execution_service import execution_service


@pytest.mark.asyncio
async def test_mock_worker_completes_deterministic_checkpoints_without_facilities(
    db: AsyncSession, auth, monkeypatch
) -> None:
    mission, _ = await _approved_mission_with_team_plan(db, auth)

    async def no_enqueue(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", no_enqueue)
    monkeypatch.setattr(execution_service, "_publish_event", no_enqueue)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)

    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)

    execution = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert execution is not None
    assert execution.status == ScrapingExecutionStatus.COMPLETED
    assert execution.progress_percent == 100
    assert execution.country_profile_json == {
        "phase": "mission_campaign",
        "provenance": "local_deterministic_mock",
        "blueprint_id": execution.blueprint_id,
        "blueprint_version": 7,
        "blueprint_version_snapshot": 7,
        "execution_plan_schema_version": execution.execution_plan_schema_version,
        "execution_plan_hash": execution.execution_plan_hash,
        "external_calls": False,
        "facility_generation": False,
    }
    events = (
        await db.execute(
            select(ScrapingEvent)
            .where(ScrapingEvent.execution_id == summary.id)
            .order_by(ScrapingEvent.sequence_number)
        )
    ).scalars().all()
    assert [event.event_type for event in events] == [
        "mission_campaign_queued",
        "mission_campaign_started",
        "clarification_not_required",
        "query_generation_completed",
        "stage_completed",
        "stage_completed",
        "stage_completed",
        "stage_completed",
        "mission_campaign_completed",
    ]
    query_gen = events[3]
    assert query_gen.event_type == "query_generation_completed"
    assert "deterministic query" in query_gen.message.lower()
    query_meta = dict(query_gen.metadata_json or {})
    assert set(query_meta.keys()) == {
        "discovery_round",
        "generated_count",
        "existing_count",
        "total_count",
    }
    assert isinstance(query_meta["discovery_round"], int)
    assert isinstance(query_meta["generated_count"], int)
    assert isinstance(query_meta["existing_count"], int)
    assert isinstance(query_meta["total_count"], int)
    assert query_meta["discovery_round"] >= 1
    assert query_meta["total_count"] >= query_meta["generated_count"]
    query_blob = f"{query_gen.message}\n{query_meta}".lower()
    for secret in (
        "query_job_fingerprint",
        "plan_hash_snapshot",
        "frozen_execution_plan",
        "resolved_execution_plan",
        "axes",
        "prompt",
        "api_key",
        "provider_credentials",
        "serper",
        "openrouter",
    ):
        assert secret not in query_blob
    assert [event.metadata_json.get("external_calls") for event in events[4:]] == [
        False,
        False,
        False,
        False,
        False,
    ]
    assert execution.clarification_status == "not_required"
    assert execution.resolved_execution_plan_hash
    facilities = await db.execute(
        select(RehabilitationFacility).where(RehabilitationFacility.execution_id == summary.id)
    )
    assert facilities.scalars().all() == []


@pytest.mark.asyncio
async def test_mock_worker_survives_live_blueprint_version_mutation_with_frozen_plan(
    db: AsyncSession, auth, monkeypatch
) -> None:
    """Campaign-owned frozen plan must not fail when the live blueprint row changes."""
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)

    async def no_enqueue(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", no_enqueue)
    monkeypatch.setattr(execution_service, "_publish_event", no_enqueue)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    original_hash = (await db.get(ScrapingExecution, summary.id)).execution_plan_hash
    row = await db.get(ScrapingBlueprint, blueprint.id)
    assert row is not None
    row.version += 1
    await db.commit()
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)

    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)

    execution = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert execution is not None
    assert execution.status == ScrapingExecutionStatus.COMPLETED
    assert execution.execution_plan_hash == original_hash
    assert execution.blueprint_version_snapshot == 7


@pytest.mark.asyncio
async def test_mock_worker_legacy_without_step1_fails_when_blueprint_version_drifts(
    db: AsyncSession, auth, monkeypatch
) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)

    async def no_enqueue(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", no_enqueue)
    monkeypatch.setattr(execution_service, "_publish_event", no_enqueue)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    # Simulate a historical pre-026 campaign row.
    execution.blueprint_snapshot_json = None
    execution.frozen_execution_plan_json = None
    execution.execution_plan_schema_version = None
    execution.execution_plan_hash = None
    execution.execution_plan_compiled_at = None
    await db.commit()
    row = await db.get(ScrapingBlueprint, blueprint.id)
    assert row is not None
    row.version += 1
    await db.commit()
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)

    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)

    execution = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert execution is not None
    assert execution.status == ScrapingExecutionStatus.FAILED
    assert execution.error_message == "Campaign blueprint provenance no longer matches its snapshot."


@pytest.mark.asyncio
async def test_mock_worker_uses_v3_snapshot_after_v4_supersedes(
    db: AsyncSession, auth, monkeypatch
) -> None:
    mission, blueprint_v3 = await _approved_mission_with_team_plan(db, auth)

    async def no_enqueue(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", no_enqueue)
    monkeypatch.setattr(execution_service, "_publish_event", no_enqueue)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    original_snapshot = dict(execution.blueprint_snapshot_json)
    original_plan = dict(execution.frozen_execution_plan_json)
    original_hash = execution.execution_plan_hash

    blueprint_v3.status = ScrapingBlueprintStatus.SUPERSEDED
    v4_payload = lebanon_structured_blueprint()
    v4_payload["regions"] = ["Beirut", "Mount Lebanon"]
    v4_payload["important_cities"] = [
        {"name": "Beirut", "region_name": "Beirut"},
        {"name": "Jounieh", "region_name": "Mount Lebanon"},
    ]
    v4_payload["weak_areas"] = ["Completely different weak area for v4"]
    blueprint_v4 = ScrapingBlueprint(
        mission_id=mission.id,
        version=8,
        status=ScrapingBlueprintStatus.APPROVED,
        structured_blueprint=v4_payload,
        model_set_id="research-set",
    )
    db.add(blueprint_v4)
    await db.flush()
    mission.active_blueprint_id = blueprint_v4.id
    mission.status = ScrapingMissionStatus.APPROVED
    await db.commit()

    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)

    refreshed = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == ScrapingExecutionStatus.COMPLETED
    assert refreshed.blueprint_snapshot_json == original_snapshot
    assert refreshed.frozen_execution_plan_json == original_plan
    assert refreshed.execution_plan_hash == original_hash
    assert refreshed.blueprint_version_snapshot == 7
    assert "Mount Lebanon" not in str(refreshed.frozen_execution_plan_json)


@pytest.mark.asyncio
async def test_historical_mock_execution_with_null_step1_fields_still_runs(
    db: AsyncSession, auth, monkeypatch
) -> None:
    mission, _ = await _approved_mission_with_team_plan(db, auth)

    async def no_enqueue(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", no_enqueue)
    monkeypatch.setattr(execution_service, "_publish_event", no_enqueue)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    execution.blueprint_snapshot_json = None
    execution.frozen_execution_plan_json = None
    execution.execution_plan_schema_version = None
    execution.execution_plan_hash = None
    execution.execution_plan_compiled_at = None
    await db.commit()

    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)

    refreshed = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == ScrapingExecutionStatus.COMPLETED
