"""Resumable Maps census cell execution — atomic claiming, stale-heartbeat
recovery, and retry-with-backoff bookkeeping for ``MapsCensusCell`` rows.

Claiming uses ``SELECT ... FOR UPDATE SKIP LOCKED`` on Postgres so concurrent
workers never block each other picking up work. SQLite (used in tests and
local dev) does not support ``SKIP LOCKED``; there the plain candidate select
is followed by a guarded ``UPDATE ... WHERE status = 'pending'`` per row,
which is atomic on its own and makes double-claiming impossible even without
row-level locking, at the cost of an occasional wasted read under real
concurrency (irrelevant for SQLite's single-writer model).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update

from app.db.models import MapsCensusCell, MapsCensusCellStatus, MapsCensusRun

DEFAULT_RETRY_BASE_SECONDS = 30


class RunExecutionSuperseded(Exception):
    """Raised when an execution notices a newer run_census() call has taken
    over its run_id, so it must stop without writing a terminal status or
    enqueuing Phase 2."""


async def claim_run_execution(session_factory, *, run_id: str, token: str) -> None:
    """Unconditionally stamp ``token`` as the current owner of ``run_id``.

    The latest caller always wins (last-writer-wins fencing). Any earlier
    execution still running will notice the mismatch next time it checks
    ``is_run_execution_current`` and stop itself — this is what makes a
    watchdog-triggered duplicate ``run_census()`` harmless instead of racing
    the original to completion.
    """
    async with session_factory() as db:
        await db.execute(
            update(MapsCensusRun).where(MapsCensusRun.id == run_id).values(execution_token=token)
        )
        await db.commit()


async def is_run_execution_current(session_factory, *, run_id: str, token: str) -> bool:
    async with session_factory() as db:
        current = await db.scalar(
            select(MapsCensusRun.execution_token).where(MapsCensusRun.id == run_id)
        )
        return current == token


async def ensure_run_execution_current(session_factory, *, run_id: str, token: str) -> None:
    if not await is_run_execution_current(session_factory, run_id=run_id, token=token):
        raise RunExecutionSuperseded(run_id)


async def claim_cells(
    session_factory,
    *,
    run_id: str,
    worker_id: str,
    batch_size: int,
) -> list[str]:
    """Atomically claim up to ``batch_size`` pending (or retry-due) cells.

    Returns the ids actually claimed — always a subset of the candidates a
    concurrent caller might have raced for.
    """
    now = datetime.now(UTC)
    async with session_factory() as db:
        stmt = (
            select(MapsCensusCell.id)
            .where(
                MapsCensusCell.run_id == run_id,
                MapsCensusCell.status == MapsCensusCellStatus.PENDING,
                or_(MapsCensusCell.next_retry_at.is_(None), MapsCensusCell.next_retry_at <= now),
            )
            .order_by(MapsCensusCell.created_at)
            .limit(max(1, batch_size))
        )
        bind = db.bind if db.bind is not None else db.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        candidate_ids = [row[0] for row in (await db.execute(stmt)).all()]

        claimed: list[str] = []
        for cell_id in candidate_ids:
            result = await db.execute(
                update(MapsCensusCell)
                .where(
                    MapsCensusCell.id == cell_id,
                    MapsCensusCell.status == MapsCensusCellStatus.PENDING,
                )
                .values(
                    status=MapsCensusCellStatus.IN_PROGRESS,
                    claimed_by=worker_id,
                    attempt_count=MapsCensusCell.attempt_count + 1,
                    started_at=now,
                    heartbeat_at=now,
                    next_retry_at=None,
                )
            )
            if result.rowcount:
                claimed.append(cell_id)
        await db.commit()
        return claimed


async def claim_cell_by_id(session_factory, *, cell_id: str, worker_id: str) -> bool:
    """Atomically transition one specific, already-known cell from PENDING to
    IN_PROGRESS — the same guarded ``UPDATE ... WHERE status = 'pending'``
    primitive as ``claim_cells``, just targeted at a cell id the caller
    already has (e.g. from its own local work queue) instead of selecting
    candidates by query. Also respects ``next_retry_at`` (same as
    ``claim_cells``) — a cell scheduled for a backed-off retry is not due
    yet, so this returns ``False`` for it exactly as if someone else had
    already claimed it. This matters: without it, a cell that failed with a
    retry delay could be re-claimed and re-attempted the moment it cycles
    back around an in-memory work queue, immediately burning another full
    attempt against whatever made it fail in the first place, instead of
    actually waiting out the backoff.

    Returns ``False`` without raising when the cell is no longer PENDING,
    not yet due for retry, or missing — the caller must skip it rather than
    process it again.
    """
    now = datetime.now(UTC)
    async with session_factory() as db:
        result = await db.execute(
            update(MapsCensusCell)
            .where(
                MapsCensusCell.id == cell_id,
                MapsCensusCell.status == MapsCensusCellStatus.PENDING,
                or_(MapsCensusCell.next_retry_at.is_(None), MapsCensusCell.next_retry_at <= now),
            )
            .values(
                status=MapsCensusCellStatus.IN_PROGRESS,
                claimed_by=worker_id,
                attempt_count=MapsCensusCell.attempt_count + 1,
                started_at=now,
                heartbeat_at=now,
                next_retry_at=None,
            )
        )
        await db.commit()
        return bool(result.rowcount)


async def heartbeat_cell(session_factory, *, cell_id: str) -> None:
    async with session_factory() as db:
        await db.execute(
            update(MapsCensusCell)
            .where(MapsCensusCell.id == cell_id)
            .values(heartbeat_at=datetime.now(UTC))
        )
        await db.commit()


async def heartbeat_run(session_factory, *, run_id: str) -> None:
    async with session_factory() as db:
        await db.execute(
            update(MapsCensusRun)
            .where(MapsCensusRun.id == run_id)
            .values(heartbeat_at=datetime.now(UTC))
        )
        await db.commit()


async def fail_cell_for_retry(
    session_factory,
    *,
    cell_id: str,
    error: str,
    max_attempts: int,
    base_backoff_seconds: int = DEFAULT_RETRY_BASE_SECONDS,
) -> dict[str, Any]:
    """Mark a claimed cell failed; schedule a retry unless attempts are exhausted.

    Returns ``{"terminal": bool, "attempt_count": int}`` for callers/tests.
    """
    async with session_factory() as db:
        cell = await db.get(MapsCensusCell, cell_id)
        if cell is None:
            return {"terminal": True, "attempt_count": 0}
        terminal = cell.attempt_count >= max_attempts
        if terminal:
            cell.status = MapsCensusCellStatus.FAILED
            cell.completed_at = datetime.now(UTC)
            cell.next_retry_at = None
        else:
            cell.status = MapsCensusCellStatus.PENDING
            backoff = base_backoff_seconds * (2 ** max(0, cell.attempt_count - 1))
            cell.next_retry_at = datetime.now(UTC) + timedelta(seconds=backoff)
        cell.last_error = str(error)[:2000]
        cell.claimed_by = None
        await db.commit()
        return {"terminal": terminal, "attempt_count": cell.attempt_count}


async def recover_stale_running_cells(
    session_factory,
    *,
    run_id: str,
    stale_seconds: int,
) -> int:
    """Reset ``IN_PROGRESS`` cells whose heartbeat is stale (worker died) back
    to ``PENDING`` so another claim can pick them up. Returns rows reset."""
    cutoff = datetime.now(UTC) - timedelta(seconds=max(1, stale_seconds))
    async with session_factory() as db:
        result = await db.execute(
            update(MapsCensusCell)
            .where(
                MapsCensusCell.run_id == run_id,
                MapsCensusCell.status == MapsCensusCellStatus.IN_PROGRESS,
                or_(MapsCensusCell.heartbeat_at.is_(None), MapsCensusCell.heartbeat_at < cutoff),
            )
            .values(status=MapsCensusCellStatus.PENDING, claimed_by=None, next_retry_at=None)
        )
        await db.commit()
        return int(result.rowcount or 0)


async def increment_run_cells_total(session_factory, *, run_id: str, by: int) -> int:
    """Atomically add ``by`` to ``maps_census_runs.cells_total`` via a single
    SQL ``UPDATE ... SET cells_total = cells_total + :by`` — not a Python-
    level read-modify-write.

    This matters: an ORM assignment like ``run.cells_total = some_local_sum``
    (or even ``run.cells_total += by`` on a fetched ORM instance) computes the
    new value in Python from whatever was last read and issues an absolute
    ``SET`` at flush time. Under a watchdog-triggered duplicate execution (or
    any other concurrent writer), that absolute SET can silently clobber
    ``cells_total`` back down to a stale, smaller number — this was the
    confirmed root cause of runs observed with ``cells_completed`` far
    exceeding ``cells_total`` (531 vs 58 in one case). A SQL-level relative
    update can only ever add to whatever the current value genuinely is,
    regardless of how many writers are doing this concurrently — there is no
    "stale local total" for it to overwrite with.

    Returns the new, authoritative value so callers can re-sync any local
    budget-tracking variable instead of trusting their own running sum.
    """
    async with session_factory() as db:
        if by == 0:
            current = await db.scalar(
                select(MapsCensusRun.cells_total).where(MapsCensusRun.id == run_id)
            )
            return int(current or 0)
        result = await db.execute(
            update(MapsCensusRun)
            .where(MapsCensusRun.id == run_id)
            .values(cells_total=MapsCensusRun.cells_total + by)
            .returning(MapsCensusRun.cells_total)
        )
        await db.commit()
        row = result.first()
        return int(row[0]) if row is not None else 0


async def reconcile_run_cell_counters(session_factory, *, run_id: str) -> dict[str, int]:
    """Recompute ``cells_total``/``cells_completed`` from the actual
    persisted ``maps_census_cells`` rows and write them back — the
    authoritative source of truth, not whatever an in-memory counter
    accumulated across however many (possibly concurrent, possibly crashed
    and resumed) executions touched this run.

    ``cells_total`` = every cell row ever persisted for this run.
    ``cells_completed`` = every cell row in a terminal status (COMPLETED,
    CAPPED, or FAILED) — pending/in-progress cells are never counted as
    completed, and a cell is never double-counted since each one only ever
    reaches one terminal status once.

    Both counts are ``WHERE run_id = :run_id`` — an index-scoped count on
    this run's own rows (``ix_maps_census_cells_run_id``), not a full-table
    scan — so this is cheap enough for a low-frequency checkpoint (resume,
    cancel, watchdog recovery) but is deliberately NOT called in the
    per-cell hot path, where the atomic increment above is used instead.
    """
    async with session_factory() as db:
        total = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(MapsCensusCell)
                    .where(MapsCensusCell.run_id == run_id)
                )
            )
            or 0
        )
        completed = int(
            (
                await db.scalar(
                    select(func.count())
                    .select_from(MapsCensusCell)
                    .where(
                        MapsCensusCell.run_id == run_id,
                        MapsCensusCell.status.in_(
                            [
                                MapsCensusCellStatus.COMPLETED,
                                MapsCensusCellStatus.CAPPED,
                                MapsCensusCellStatus.FAILED,
                            ]
                        ),
                    )
                )
            )
            or 0
        )
        run = await db.get(MapsCensusRun, run_id)
        if run is not None:
            run.cells_total = total
            run.cells_completed = completed
            await db.commit()
    return {"cells_total": total, "cells_completed": completed}


async def is_run_cancelled(session_factory, *, run_id: str) -> bool:
    from app.db.models import MapsCensusRun, MapsCensusStatus

    async with session_factory() as db:
        status = await db.scalar(select(MapsCensusRun.status).where(MapsCensusRun.id == run_id))
        return status == MapsCensusStatus.CANCELLED


async def is_campaign_paused(session_factory, *, run_id: str) -> bool:
    from app.db.models import MapsCensusRun

    async with session_factory() as db:
        run = await db.get(MapsCensusRun, run_id)
        if run is None:
            return False
        state = run.processing_state or {}
        return bool(state.get("campaign_paused"))
