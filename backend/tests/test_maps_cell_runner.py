"""Unit tests for resumable Maps census cell execution primitives —
atomic claiming, stale-heartbeat recovery, retry-with-backoff, and
cancellation checks (Phase 2 gap #3).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.dependencies import AuthContext
from app.db.models import (
    MapsCensusCell,
    MapsCensusCellStatus,
    MapsCensusRun,
    MapsCensusStatus,
)
from app.services.scraping.maps_cell_runner import (
    claim_cells,
    fail_cell_for_retry,
    heartbeat_cell,
    is_run_cancelled,
    recover_stale_running_cells,
)


def _session_factory_for(db: AsyncSession):
    bind = db.bind if db.bind is not None else db.get_bind()
    return async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)


async def _create_run(db: AsyncSession, auth: AuthContext, *, status=MapsCensusStatus.RUNNING) -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FR",
        country_name="France",
        status=status,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run


async def _create_cell(
    db: AsyncSession,
    run: MapsCensusRun,
    *,
    query_text: str = "rehab clinic",
    status: MapsCensusCellStatus = MapsCensusCellStatus.PENDING,
    next_retry_at=None,
    heartbeat_at=None,
) -> MapsCensusCell:
    cell = MapsCensusCell(
        run_id=run.id,
        region_name="Ile-de-France",
        city_name="Paris",
        query_text=query_text,
        status=status,
        next_retry_at=next_retry_at,
        heartbeat_at=heartbeat_at,
    )
    db.add(cell)
    await db.flush()
    await db.commit()
    return cell


@pytest.mark.asyncio
async def test_claim_cells_marks_in_progress_and_sets_claim_metadata(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    cell = await _create_cell(db, run)
    session_factory = _session_factory_for(db)

    claimed = await claim_cells(session_factory, run_id=run.id, worker_id="worker-1", batch_size=5)

    assert claimed == [cell.id]
    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusCell, cell.id)
        assert refreshed.status == MapsCensusCellStatus.IN_PROGRESS
        assert refreshed.claimed_by == "worker-1"
        assert refreshed.attempt_count == 1
        assert refreshed.started_at is not None
        assert refreshed.heartbeat_at is not None


@pytest.mark.asyncio
async def test_claim_cells_two_workers_never_claim_the_same_cell(db: AsyncSession, auth: AuthContext):
    """Concurrency guarantee: even though both workers see the same pending
    cell as a claim candidate, the guarded ``UPDATE ... WHERE status =
    'pending'`` ensures only one of them actually claims it."""
    run = await _create_run(db, auth)
    cell = await _create_cell(db, run)
    session_factory = _session_factory_for(db)

    first = await claim_cells(session_factory, run_id=run.id, worker_id="worker-a", batch_size=5)
    second = await claim_cells(session_factory, run_id=run.id, worker_id="worker-b", batch_size=5)

    assert first == [cell.id]
    assert second == []
    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusCell, cell.id)
        assert refreshed.claimed_by == "worker-a"


@pytest.mark.asyncio
async def test_claim_cells_skips_cells_with_future_retry(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    future_retry_cell = await _create_cell(
        db, run, query_text="future retry", next_retry_at=datetime.now(UTC) + timedelta(hours=1)
    )
    ready_cell = await _create_cell(
        db, run, query_text="past retry", next_retry_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    session_factory = _session_factory_for(db)

    claimed = await claim_cells(session_factory, run_id=run.id, worker_id="worker-1", batch_size=5)

    assert ready_cell.id in claimed
    assert future_retry_cell.id not in claimed


@pytest.mark.asyncio
async def test_claim_cells_ignores_other_runs_cells(db: AsyncSession, auth: AuthContext):
    run_a = await _create_run(db, auth)
    run_b = await _create_run(db, auth)
    cell_a = await _create_cell(db, run_a)
    await _create_cell(db, run_b)
    session_factory = _session_factory_for(db)

    claimed = await claim_cells(session_factory, run_id=run_a.id, worker_id="worker-1", batch_size=5)

    assert claimed == [cell_a.id]


@pytest.mark.asyncio
async def test_heartbeat_cell_updates_timestamp(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    cell = await _create_cell(db, run, status=MapsCensusCellStatus.IN_PROGRESS)
    session_factory = _session_factory_for(db)

    await heartbeat_cell(session_factory, cell_id=cell.id)

    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusCell, cell.id)
        assert refreshed.heartbeat_at is not None


@pytest.mark.asyncio
async def test_fail_cell_for_retry_schedules_retry_when_attempts_remain(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    cell = await _create_cell(db, run, status=MapsCensusCellStatus.IN_PROGRESS)
    cell.attempt_count = 1
    await db.commit()
    session_factory = _session_factory_for(db)

    outcome = await fail_cell_for_retry(
        session_factory, cell_id=cell.id, error="transient timeout", max_attempts=3
    )

    assert outcome == {"terminal": False, "attempt_count": 1}
    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusCell, cell.id)
        assert refreshed.status == MapsCensusCellStatus.PENDING
        assert refreshed.next_retry_at is not None
        naive_next_retry = refreshed.next_retry_at.replace(tzinfo=None)
        assert naive_next_retry > datetime.now(UTC).replace(tzinfo=None)
        assert refreshed.last_error == "transient timeout"
        assert refreshed.claimed_by is None


@pytest.mark.asyncio
async def test_fail_cell_for_retry_marks_failed_when_attempts_exhausted(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    cell = await _create_cell(db, run, status=MapsCensusCellStatus.IN_PROGRESS)
    cell.attempt_count = 3
    await db.commit()
    session_factory = _session_factory_for(db)

    outcome = await fail_cell_for_retry(
        session_factory, cell_id=cell.id, error="persistent failure", max_attempts=3
    )

    assert outcome == {"terminal": True, "attempt_count": 3}
    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusCell, cell.id)
        assert refreshed.status == MapsCensusCellStatus.FAILED
        assert refreshed.completed_at is not None
        assert refreshed.next_retry_at is None


@pytest.mark.asyncio
async def test_recover_stale_running_cells_resets_dead_worker_cells(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    stale_cell = await _create_cell(
        db,
        run,
        query_text="stale",
        status=MapsCensusCellStatus.IN_PROGRESS,
        heartbeat_at=datetime.now(UTC) - timedelta(seconds=600),
    )
    fresh_cell = await _create_cell(
        db,
        run,
        query_text="fresh",
        status=MapsCensusCellStatus.IN_PROGRESS,
        heartbeat_at=datetime.now(UTC),
    )
    session_factory = _session_factory_for(db)

    reset_count = await recover_stale_running_cells(session_factory, run_id=run.id, stale_seconds=300)

    assert reset_count == 1
    async with session_factory() as check_db:
        stale = await check_db.get(MapsCensusCell, stale_cell.id)
        fresh = await check_db.get(MapsCensusCell, fresh_cell.id)
        assert stale.status == MapsCensusCellStatus.PENDING
        assert stale.claimed_by is None
        assert fresh.status == MapsCensusCellStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_recover_stale_running_cells_resets_missing_heartbeat(db: AsyncSession, auth: AuthContext):
    """A cell claimed but never heartbeat-ed (heartbeat_at is NULL) is
    treated as stale immediately — it can never satisfy the cutoff check."""
    run = await _create_run(db, auth)
    cell = await _create_cell(db, run, status=MapsCensusCellStatus.IN_PROGRESS, heartbeat_at=None)
    session_factory = _session_factory_for(db)

    reset_count = await recover_stale_running_cells(session_factory, run_id=run.id, stale_seconds=300)

    assert reset_count == 1
    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusCell, cell.id)
        assert refreshed.status == MapsCensusCellStatus.PENDING


@pytest.mark.asyncio
async def test_is_run_cancelled_reflects_run_status(db: AsyncSession, auth: AuthContext):
    running_run = await _create_run(db, auth, status=MapsCensusStatus.RUNNING)
    cancelled_run = await _create_run(db, auth, status=MapsCensusStatus.CANCELLED)
    session_factory = _session_factory_for(db)

    assert await is_run_cancelled(session_factory, run_id=running_run.id) is False
    assert await is_run_cancelled(session_factory, run_id=cancelled_run.id) is True
