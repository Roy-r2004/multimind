"""HTTP integration coverage for Phase 2A mission campaign routes."""

import pytest
from httpx import ASGITransport, AsyncClient
from test_mission_campaign_lifecycle import _approved_mission_with_team_plan

from app.core.dependencies import get_auth_context
from app.db.models import ScrapingExecution, ScrapingExecutionStatus
from app.db.session import get_db
from app.main import create_app
from app.services.audit_service import audit_service
from app.services.scraping.execution_service import execution_service


@pytest.fixture
async def campaign_client(db, auth, monkeypatch):
    queued: list[tuple[str, str]] = []

    async def record_enqueue(execution_id: str, *, job_name: str) -> None:
        queued.append((execution_id, job_name))

    async def no_audit(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(execution_service, "enqueue_execution", record_enqueue)
    monkeypatch.setattr(execution_service, "_publish_event", no_audit)
    monkeypatch.setattr(audit_service, "record_http", no_audit)
    app = create_app()

    async def override_db():
        yield db

    async def override_auth():
        return auth

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = override_auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, queued
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_mission_campaign_routes_start_observe_pause_resume_and_cancel(
    db, auth, campaign_client
) -> None:
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    client, queued = campaign_client
    base = f"/api/v1/scraping/missions/{mission.id}/executions"

    started = await client.post(base, json={"mode": "mock"})
    assert started.status_code == 202
    execution = started.json()
    execution_id = execution["id"]
    assert execution["status"] == "queued"
    assert execution["execution_origin"] == "mission_campaign_mock"
    assert queued == [(execution_id, "run_mission_campaign_mock")]

    listed = await client.get(base)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [execution_id]

    detail = await client.get(f"{base}/{execution_id}")
    assert detail.status_code == 200
    assert detail.json()["mock"] is True
    assert detail.json()["recent_events"][0]["event_type"] == "mission_campaign_queued"

    row = await db.get(ScrapingExecution, execution_id)
    assert row is not None
    row.status = ScrapingExecutionStatus.RUNNING
    await db.commit()

    paused = await client.post(f"{base}/{execution_id}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "pause_requested"

    row.status = ScrapingExecutionStatus.PAUSED
    await db.commit()
    resumed = await client.post(f"{base}/{execution_id}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "queued"
    assert queued[-1] == (execution_id, "run_mission_campaign_mock")

    cancelled = await client.post(f"{base}/{execution_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    events = await client.get(f"{base}/{execution_id}/events", params={"after_sequence": 0})
    assert events.status_code == 200
    assert [event["sequence_number"] for event in events.json()] == list(
        range(1, len(events.json()) + 1)
    )
    assert events.json()[-1]["event_type"] == "execution_cancelled"


@pytest.mark.asyncio
async def test_mission_campaign_routes_reject_invalid_mode_and_mismatched_mission(
    db, auth, campaign_client
) -> None:
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    client, _ = campaign_client
    base = f"/api/v1/scraping/missions/{mission.id}/executions"

    invalid = await client.post(base, json={"mode": "real"})
    assert invalid.status_code == 422

    started = await client.post(base)
    assert started.status_code == 202
    wrong_mission = await client.get(
        f"/api/v1/scraping/missions/not-the-mission/executions/{started.json()['id']}"
    )
    assert wrong_mission.status_code == 404
