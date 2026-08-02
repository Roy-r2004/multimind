"""Atomic batch claiming for Maps enrichment small-batch jobs.

Two concurrent workers (a regular batch job racing the watchdog, or two ARQ
workers) must never process the same place twice. On Postgres this uses
``SELECT ... FOR UPDATE SKIP LOCKED`` so a concurrent claim simply skips rows
already locked by another in-flight transaction, then flips them to
``running`` before releasing the lock. SQLite (used in unit tests) has no
``SKIP LOCKED`` support, but tests only ever use a single connection, so a
plain claim is safe there.
"""

from __future__ import annotations

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MapsPlace, MapsPlaceEnrichmentStatus


async def claim_batch_place_ids(
    session_factory,
    query,
    *,
    batch_size: int,
) -> list[str]:
    """Atomically select up to ``batch_size`` places matching ``query`` and mark
    them ``running`` in the same transaction, returning their ids.

    ``query`` must already filter to selectable places (e.g. pending/failed)
    and should NOT include its own ``.limit()``.
    """
    async with session_factory() as session:
        ids = await _claim_in_session(session, query, batch_size=batch_size)
        await session.commit()
        return ids


async def _claim_in_session(
    session: AsyncSession, query: Select, *, batch_size: int
) -> list[str]:
    dialect_name = session.bind.dialect.name if session.bind is not None else ""
    stmt = query.limit(max(1, batch_size))
    if dialect_name == "postgresql":
        stmt = stmt.with_for_update(skip_locked=True)
    places = (await session.execute(stmt)).scalars().all()
    ids: list[str] = []
    for place in places:
        place.enrichment_status = MapsPlaceEnrichmentStatus.RUNNING.value
        ids.append(place.id)
    return ids


__all__ = ["claim_batch_place_ids"]
