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
    RunExecutionSuperseded,
    claim_cell_by_id,
    claim_cells,
    claim_run_execution,
    ensure_run_execution_current,
    fail_cell_for_retry,
    heartbeat_cell,
    heartbeat_run,
    increment_run_cells_total,
    is_run_cancelled,
    is_run_execution_current,
    reconcile_run_cell_counters,
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


@pytest.mark.asyncio
async def test_claim_run_execution_last_writer_wins(db: AsyncSession, auth: AuthContext):
    """Two 'concurrent' run_census() calls for the same run_id both claim the
    run execution; only the second (later) token should end up current — this
    is what lets a watchdog-triggered duplicate supersede a run without
    coordination."""
    run = await _create_run(db, auth)
    session_factory = _session_factory_for(db)

    await claim_run_execution(session_factory, run_id=run.id, token="token-a")
    assert await is_run_execution_current(session_factory, run_id=run.id, token="token-a") is True

    await claim_run_execution(session_factory, run_id=run.id, token="token-b")

    assert await is_run_execution_current(session_factory, run_id=run.id, token="token-a") is False
    assert await is_run_execution_current(session_factory, run_id=run.id, token="token-b") is True


@pytest.mark.asyncio
async def test_ensure_run_execution_current_raises_when_superseded(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    session_factory = _session_factory_for(db)
    await claim_run_execution(session_factory, run_id=run.id, token="original")

    # A watchdog-triggered duplicate takes over.
    await claim_run_execution(session_factory, run_id=run.id, token="duplicate")

    with pytest.raises(RunExecutionSuperseded):
        await ensure_run_execution_current(session_factory, run_id=run.id, token="original")
    # The newer execution is unaffected.
    await ensure_run_execution_current(session_factory, run_id=run.id, token="duplicate")


@pytest.mark.asyncio
async def test_claim_cell_by_id_two_workers_only_one_wins(db: AsyncSession, auth: AuthContext):
    """Two concurrent run_census() executions both have this exact cell_id in
    their own local work queue (e.g. a watchdog-triggered duplicate racing
    the original). The guarded UPDATE ... WHERE status = 'pending' must let
    only one of them actually claim and process it."""
    run = await _create_run(db, auth)
    cell = await _create_cell(db, run)
    session_factory = _session_factory_for(db)

    first = await claim_cell_by_id(session_factory, cell_id=cell.id, worker_id="execution-a")
    second = await claim_cell_by_id(session_factory, cell_id=cell.id, worker_id="execution-b")

    assert first is True
    assert second is False
    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusCell, cell.id)
        assert refreshed.status == MapsCensusCellStatus.IN_PROGRESS
        assert refreshed.claimed_by == "execution-a"  # the second claim never overwrote it


@pytest.mark.asyncio
async def test_claim_cell_by_id_rejects_non_pending_cell(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    completed_cell = await _create_cell(db, run, status=MapsCensusCellStatus.COMPLETED)
    session_factory = _session_factory_for(db)

    claimed = await claim_cell_by_id(session_factory, cell_id=completed_cell.id, worker_id="worker-1")

    assert claimed is False
    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusCell, completed_cell.id)
        assert refreshed.status == MapsCensusCellStatus.COMPLETED  # untouched


@pytest.mark.asyncio
async def test_heartbeat_run_updates_run_heartbeat(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    run.heartbeat_at = datetime.now(UTC) - timedelta(minutes=30)
    await db.commit()
    session_factory = _session_factory_for(db)

    await heartbeat_run(session_factory, run_id=run.id)

    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusRun, run.id)
        naive_heartbeat = refreshed.heartbeat_at.replace(tzinfo=None)
        assert naive_heartbeat > (datetime.now(UTC) - timedelta(minutes=1)).replace(tzinfo=None)


@pytest.mark.asyncio
async def test_claim_cell_by_id_respects_backoff_not_yet_due(db: AsyncSession, auth: AuthContext):
    """A cell that failed and is scheduled for a backed-off retry must not
    be re-claimed before that time — without this, a cell cycling back
    around an in-memory work queue would retry immediately, giving a
    saturated backend (the verified cause of repeated Gosom timeouts) no
    real time to recover between attempts."""
    run = await _create_run(db, auth)
    cell = await _create_cell(
        db, run, status=MapsCensusCellStatus.PENDING, next_retry_at=datetime.now(UTC) + timedelta(minutes=5)
    )
    session_factory = _session_factory_for(db)

    claimed = await claim_cell_by_id(session_factory, cell_id=cell.id, worker_id="worker-1")

    assert claimed is False
    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusCell, cell.id)
        assert refreshed.status == MapsCensusCellStatus.PENDING  # untouched, not claimed early


@pytest.mark.asyncio
async def test_claim_cell_by_id_succeeds_once_backoff_has_passed(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    cell = await _create_cell(
        db, run, status=MapsCensusCellStatus.PENDING, next_retry_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    session_factory = _session_factory_for(db)

    claimed = await claim_cell_by_id(session_factory, cell_id=cell.id, worker_id="worker-1")

    assert claimed is True
    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusCell, cell.id)
        assert refreshed.status == MapsCensusCellStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_fail_cell_for_retry_terminates_after_configured_timeout_attempts(
    db: AsyncSession, auth: AuthContext
):
    """A tighter attempt cap (e.g. 2, for timeout-class failures) must
    actually bite — not loop forever retrying an expensive, already-failing
    operation."""
    run = await _create_run(db, auth)
    cell = await _create_cell(db, run, status=MapsCensusCellStatus.IN_PROGRESS)
    cell.attempt_count = 1
    await db.commit()
    session_factory = _session_factory_for(db)

    # First timeout: 1 of 2 allowed attempts used — must still schedule a
    # (long) retry, not give up yet.
    first = await fail_cell_for_retry(
        session_factory,
        cell_id=cell.id,
        error="gosom job exceeded 900s",
        max_attempts=2,
        base_backoff_seconds=300,
    )
    assert first == {"terminal": False, "attempt_count": 1}
    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusCell, cell.id)
        assert refreshed.status == MapsCensusCellStatus.PENDING
        naive_retry_at = refreshed.next_retry_at.replace(tzinfo=None)
        assert naive_retry_at > datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=250)

    # Second timeout (attempt_count now 2 of 2): must terminate — not loop
    # forever against an already-saturated backend.
    async with session_factory() as bump_db:
        cell2 = await bump_db.get(MapsCensusCell, cell.id)
        cell2.status = MapsCensusCellStatus.IN_PROGRESS
        cell2.attempt_count = 2
        await bump_db.commit()

    second = await fail_cell_for_retry(
        session_factory,
        cell_id=cell.id,
        error="gosom job exceeded 900s",
        max_attempts=2,
        base_backoff_seconds=300,
    )
    assert second == {"terminal": True, "attempt_count": 2}
    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusCell, cell.id)
        assert refreshed.status == MapsCensusCellStatus.FAILED


@pytest.mark.asyncio
async def test_increment_run_cells_total_is_additive_not_overwriting(
    db: AsyncSession, auth: AuthContext
):
    """The exact mechanism that produced the observed 531-completed/58-total
    state: two 'executions' each independently believe they are adding to
    cells_total. A Python-level ``run.cells_total = local_sum`` would let
    the second overwrite stomp the first's contribution (lost update). The
    atomic SQL-level increment must not — both contributions survive
    regardless of ordering.
    """
    run = await _create_run(db, auth)
    session_factory = _session_factory_for(db)

    first_total = await increment_run_cells_total(session_factory, run_id=run.id, by=30)
    assert first_total == 30

    # A second, concurrent-style caller adds its own batch without ever
    # reading back and combining with the first caller's value — exactly
    # how two independently-running executions behave.
    second_total = await increment_run_cells_total(session_factory, run_id=run.id, by=12)
    assert second_total == 42  # 30 + 12, not 12 (which a naive overwrite would produce)

    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusRun, run.id)
        assert refreshed.cells_total == 42


@pytest.mark.asyncio
async def test_increment_run_cells_total_survives_many_concurrent_increments(
    db: AsyncSession, auth: AuthContext
):
    """Simulates many overlapping subdivision/expansion-wave writers hitting
    the same run concurrently (asyncio.gather, real interleaving within one
    process) — the final total must equal the exact sum of every increment,
    with none lost."""
    import asyncio

    run = await _create_run(db, auth)
    session_factory = _session_factory_for(db)

    increments = [3, 4, 1, 6, 2, 5, 3, 4, 1, 2]
    await asyncio.gather(
        *[
            increment_run_cells_total(session_factory, run_id=run.id, by=n)
            for n in increments
        ]
    )

    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusRun, run.id)
        assert refreshed.cells_total == sum(increments)


@pytest.mark.asyncio
async def test_reconcile_run_cell_counters_fixes_the_observed_impossible_state(
    db: AsyncSession, auth: AuthContext
):
    """Directly reproduces the reported bug: cells_completed=531 while
    cells_total=58, on a run whose actual maps_census_cells rows look
    nothing like that. Reconciliation must replace both with the true,
    persisted-row-derived values — never just clamp cells_completed down to
    cells_total (that would hide the inconsistency rather than fix it).
    """
    run = await _create_run(db, auth)
    run.cells_total = 58
    run.cells_completed = 531
    await db.commit()

    # The actual ground truth: 10 cells, 7 terminal (completed/capped/failed
    # in a realistic mix), 2 pending, 1 in progress.
    statuses = (
        [MapsCensusCellStatus.COMPLETED] * 4
        + [MapsCensusCellStatus.CAPPED] * 2
        + [MapsCensusCellStatus.FAILED] * 1
        + [MapsCensusCellStatus.PENDING] * 2
        + [MapsCensusCellStatus.IN_PROGRESS] * 1
    )
    for i, status in enumerate(statuses):
        await _create_cell(db, run, query_text=f"query {i}", status=status)

    session_factory = _session_factory_for(db)
    result = await reconcile_run_cell_counters(session_factory, run_id=run.id)

    assert result == {"cells_total": 10, "cells_completed": 7}
    async with session_factory() as check_db:
        refreshed = await check_db.get(MapsCensusRun, run.id)
        assert refreshed.cells_total == 10
        assert refreshed.cells_completed == 7
        # The headline invariant: completed can never exceed total once
        # reconciled from ground truth.
        assert refreshed.cells_completed <= refreshed.cells_total
