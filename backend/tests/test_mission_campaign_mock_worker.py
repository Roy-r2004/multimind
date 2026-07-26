"""Deterministic, local-only Phase 2A campaign worker coverage."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_mission_campaign_lifecycle import _approved_mission_with_team_plan

from app.db.models import (
    RehabilitationFacility,
    ScrapingBlueprint,
    ScrapingEvent,
    ScrapingExecution,
    ScrapingExecutionStatus,
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
        "stage_completed",
        "stage_completed",
        "stage_completed",
        "stage_completed",
        "mission_campaign_completed",
    ]
    assert [event.metadata_json.get("external_calls") for event in events[2:]] == [
        False,
        False,
        False,
        False,
        False,
    ]
    facilities = await db.execute(
        select(RehabilitationFacility).where(RehabilitationFacility.execution_id == summary.id)
    )
    assert facilities.scalars().all() == []


@pytest.mark.asyncio
async def test_mock_worker_fails_when_blueprint_snapshot_no_longer_matches(
    db: AsyncSession, auth, monkeypatch
) -> None:
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)

    async def no_enqueue(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", no_enqueue)
    monkeypatch.setattr(execution_service, "_publish_event", no_enqueue)
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
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
