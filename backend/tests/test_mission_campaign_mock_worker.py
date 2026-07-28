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
from test_phase4_discovery_execution import _stub_phase4_complete


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
    monkeypatch.setattr(mission_campaign_mock_worker, "_run_phase4_web_discovery", _stub_phase4_complete)

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
        "web_discovery_completed",
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
        "expected_raw_count",
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
    # Phase 4 stub completion — no mock stage_completed events on schema-v2 path.
    assert "stage_completed" not in [event.event_type for event in events]
    assert events[-1].event_type == "mission_campaign_completed"
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
    monkeypatch.setattr(mission_campaign_mock_worker, "_run_phase4_web_discovery", _stub_phase4_complete)

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
    monkeypatch.setattr(mission_campaign_mock_worker, "_run_phase4_web_discovery", _stub_phase4_complete)

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
    monkeypatch.setattr(mission_campaign_mock_worker, "_run_phase4_web_discovery", _stub_phase4_complete)
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
    monkeypatch.setattr(mission_campaign_mock_worker, "_run_phase4_web_discovery", _stub_phase4_complete)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)

    refreshed = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert refreshed is not None
    assert refreshed.status == ScrapingExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_worker_cancel_requested_on_restart_finishes_cancelled(
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
    from datetime import UTC, datetime

    execution.status = ScrapingExecutionStatus.CANCEL_REQUESTED
    execution.cancel_requested_at = datetime.now(UTC)
    await db.commit()

    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(mission_campaign_mock_worker, "_run_phase4_web_discovery", _stub_phase4_complete)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)

    done = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert done is not None
    assert done.status == ScrapingExecutionStatus.CANCELLED
    assert done.completed_at is not None
    events = (
        await db.execute(
            select(ScrapingEvent)
            .where(ScrapingEvent.execution_id == summary.id)
            .order_by(ScrapingEvent.sequence_number)
        )
    ).scalars().all()
    assert any(event.event_type == "execution_cancelled" for event in events)
    assert not any(event.event_type == "query_generation_completed" for event in events)


@pytest.mark.asyncio
async def test_worker_pause_requested_on_restart_becomes_paused(
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
    from datetime import UTC, datetime

    execution.status = ScrapingExecutionStatus.PAUSE_REQUESTED
    execution.pause_requested_at = datetime.now(UTC)
    await db.commit()

    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(mission_campaign_mock_worker, "_run_phase4_web_discovery", _stub_phase4_complete)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)

    done = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert done is not None
    assert done.status == ScrapingExecutionStatus.PAUSED
    assert done.paused_at is not None


@pytest.mark.asyncio
async def test_cancel_supersedes_pause_when_both_timestamps_exist(
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
    from datetime import UTC, datetime, timedelta

    earlier = datetime.now(UTC) - timedelta(seconds=30)
    later = datetime.now(UTC)
    execution.status = ScrapingExecutionStatus.PAUSE_REQUESTED
    execution.pause_requested_at = earlier
    execution.cancel_requested_at = later
    await db.commit()

    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(mission_campaign_mock_worker, "_run_phase4_web_discovery", _stub_phase4_complete)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)

    done = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert done is not None
    assert done.status == ScrapingExecutionStatus.CANCELLED
    assert done.completed_at is not None


@pytest.mark.asyncio
async def test_paused_campaign_resumes_same_execution_without_duplicate_jobs(
    db: AsyncSession, auth, monkeypatch
) -> None:
    import app.services.scraping.query_generation_service as qgs
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from app.db.models import ScrapingSourceDiscoveryQuery
    from app.schemas.scraping_clarification import ClarificationStatus
    from app.services.scraping.query_generation_service import query_generation_service
    from sqlalchemy import func, select

    mission, _ = await _approved_mission_with_team_plan(db, auth)
    monkeypatch.setattr(execution_service, "enqueue_execution", AsyncMock())
    monkeypatch.setattr(execution_service, "_publish_event", AsyncMock())
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution_id = summary.id
    execution = await db.get(ScrapingExecution, execution_id)
    assert execution is not None
    execution.clarification_status = ClarificationStatus.NOT_REQUIRED.value
    execution.status = ScrapingExecutionStatus.RUNNING
    await db.commit()

    batch_size = 2
    monkeypatch.setattr(qgs, "INSERT_BATCH_SIZE", batch_size)

    async def pause_after_first_committed_batch(session, row) -> bool:
        # Trigger only after at least one batch has been persisted (post-commit path).
        persisted = (
            await session.execute(
                select(func.count()).select_from(ScrapingSourceDiscoveryQuery).where(
                    ScrapingSourceDiscoveryQuery.execution_id == row.id
                )
            )
        ).scalar_one()
        if int(persisted or 0) < batch_size:
            return False
        row.status = ScrapingExecutionStatus.PAUSE_REQUESTED
        row.pause_requested_at = datetime.now(UTC)
        await session.commit()
        return await mission_campaign_mock_worker._pause_or_cancel(session, row)

    paused_gen = await query_generation_service.generate_for_execution(
        db, execution, discovery_round=1, check_interrupt=pause_after_first_committed_batch
    )
    assert paused_gen.status == "interrupted"
    await db.refresh(execution)
    assert execution.status == ScrapingExecutionStatus.PAUSED
    assert execution.paused_at is not None
    assert execution.completed_at is None
    partial = paused_gen.generated_count
    assert partial > 0

    fingerprints = set(
        (
            await db.execute(
                select(ScrapingSourceDiscoveryQuery.query_job_fingerprint).where(
                    ScrapingSourceDiscoveryQuery.execution_id == execution_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(fingerprints) == partial

    detail = await execution_service.get_mission_campaign_detail(
        db, auth, mission.id, execution_id
    )
    assert detail.can_resume is True
    assert detail.can_pause is False
    assert detail.can_cancel is True
    assert detail.execution.completed_at is None

    resumed = await execution_service.resume_mission_campaign(
        db, auth, mission.id, execution_id
    )
    assert resumed.id == execution_id
    assert resumed.status == ScrapingExecutionStatus.QUEUED.value
    assert resumed.completed_at is None

    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(mission_campaign_mock_worker, "_run_phase4_web_discovery", _stub_phase4_complete)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, execution_id)

    done = await db.get(ScrapingExecution, execution_id, populate_existing=True)
    assert done is not None
    assert done.id == execution_id
    assert done.status == ScrapingExecutionStatus.COMPLETED
    total = (
        await db.execute(
            select(func.count()).select_from(ScrapingSourceDiscoveryQuery).where(
                ScrapingSourceDiscoveryQuery.execution_id == execution_id
            )
        )
    ).scalar_one()
    # Interrupted total_count is persisted-so-far, not the eventual workload size.
    assert total > paused_gen.total_count
    fingerprints_after = set(
        (
            await db.execute(
                select(ScrapingSourceDiscoveryQuery.query_job_fingerprint).where(
                    ScrapingSourceDiscoveryQuery.execution_id == execution_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert fingerprints.issubset(fingerprints_after)
    assert len(fingerprints_after) == total
    completed_events = (
        await db.execute(
            select(ScrapingEvent).where(
                ScrapingEvent.execution_id == execution_id,
                ScrapingEvent.event_type == "query_generation_completed",
            )
        )
    ).scalars().all()
    assert len(completed_events) == 1


@pytest.mark.asyncio
async def test_cancelled_campaign_cannot_resume(db: AsyncSession, auth, monkeypatch) -> None:
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock

    from app.core.exceptions import ConflictError

    mission, _ = await _approved_mission_with_team_plan(db, auth)
    monkeypatch.setattr(execution_service, "enqueue_execution", AsyncMock())
    monkeypatch.setattr(execution_service, "_publish_event", AsyncMock())
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    execution.status = ScrapingExecutionStatus.CANCELLED
    execution.cancel_requested_at = datetime.now(UTC)
    execution.completed_at = datetime.now(UTC)
    await db.commit()

    with pytest.raises(ConflictError, match="cancelled"):
        await execution_service.resume_mission_campaign(db, auth, mission.id, summary.id)

    detail = await execution_service.get_mission_campaign_detail(
        db, auth, mission.id, summary.id
    )
    assert detail.can_resume is False
    assert detail.execution.status == ScrapingExecutionStatus.CANCELLED.value
