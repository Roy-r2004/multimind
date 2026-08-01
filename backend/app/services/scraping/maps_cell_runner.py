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

from sqlalchemy import or_, select, update

from app.db.models import MapsCensusCell, MapsCensusCellStatus

DEFAULT_RETRY_BASE_SECONDS = 30


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


async def heartbeat_cell(session_factory, *, cell_id: str) -> None:
    async with session_factory() as db:
        await db.execute(
            update(MapsCensusCell)
            .where(MapsCensusCell.id == cell_id)
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
