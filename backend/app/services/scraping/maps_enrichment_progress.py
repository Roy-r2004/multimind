"""Enrichment progress / heartbeat persistence for Maps two-phase pipeline.

Keeps discovery campaign status separate from enrichment liveness so a worker
crash cannot look like a successful completed campaign, and so pending rows can
resume without a full Recover reset.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.db.models import MapsCensusRun, MapsPlace, MapsPlaceEnrichmentStatus

logger = logging.getLogger(__name__)

ENRICHMENT_STATUS_PENDING = "pending"
ENRICHMENT_STATUS_RUNNING = "running"
ENRICHMENT_STATUS_COMPLETED = "completed"
ENRICHMENT_STATUS_FAILED_RETRYABLE = "failed_retryable"
ENRICHMENT_STATUS_PAUSED = "paused"
ENRICHMENT_STATUS_STALE_FAILED = "stale_failed"

# Auto-resume a crashed enrichment at most this many times without Recover.
MAX_ENRICHMENT_AUTO_RESUMES = 1


async def count_places_by_enrichment_status(
    session_factory, *, run_id: str
) -> dict[str, int]:
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(MapsPlace.enrichment_status, func.count())
                .where(MapsPlace.run_id == run_id)
                .group_by(MapsPlace.enrichment_status)
            )
        ).all()
    return {
        (status if status is not None else "?"): int(count) for status, count in rows
    }


async def persist_enrichment_progress(
    session_factory,
    *,
    run_id: str,
    phase: str,
    enrichment_status: str | None = None,
    last_processed_place_id: str | None = None,
    processed_count: int | None = None,
    pending_count: int | None = None,
    running_count: int | None = None,
    classified_count: int | None = None,
    detail_enriched_count: int | None = None,
    paused: bool | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write enrichment heartbeat + counters after every batch (and on fatal exit)."""
    now = datetime.now(UTC)
    async with session_factory() as session:
        run = await session.get(MapsCensusRun, run_id)
        if run is None:
            return
        state = dict(run.processing_state or {})
        state["enrichment_heartbeat_at"] = now.isoformat()
        state["current_phase"] = phase
        if last_processed_place_id is not None:
            state["last_processed_place_id"] = last_processed_place_id
        if processed_count is not None:
            state["processed_count"] = int(processed_count)
        if pending_count is not None:
            state["pending_count"] = int(pending_count)
        if running_count is not None:
            state["running_count"] = int(running_count)
        if classified_count is not None:
            state["classification_stats"] = {
                **dict(state.get("classification_stats") or {}),
                "classified": int(classified_count),
                "paused": bool(paused) if paused is not None else bool(
                    (state.get("classification_stats") or {}).get("paused")
                ),
                "updated_at": now.isoformat(),
            }
        if detail_enriched_count is not None:
            state["detail_enrichment_stats"] = {
                **dict(state.get("detail_enrichment_stats") or {}),
                "enriched": int(detail_enriched_count),
                "paused": bool(paused) if paused is not None else bool(
                    (state.get("detail_enrichment_stats") or {}).get("paused")
                ),
                "updated_at": now.isoformat(),
            }
        if enrichment_status is not None:
            state["enrichment_status"] = enrichment_status
        if error is not None:
            state["enrichment_last_error"] = str(error)[:1000]
            state["enrichment_last_error_at"] = now.isoformat()
        if extra:
            for key, value in extra.items():
                state[key] = value
        run.processing_state = state
        run.heartbeat_at = now
        # Never flip discovery status away from completed while enrichment runs.
        await session.commit()


def enrichment_status_from_run(run: MapsCensusRun) -> str:
    """Derive enrichment status independently of discovery campaign status."""
    state = run.processing_state or {}
    explicit = state.get("enrichment_status")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    if run.enrichment_refresh_completed_at is not None:
        if state.get("enrichment_paused"):
            return ENRICHMENT_STATUS_PAUSED
        return ENRICHMENT_STATUS_COMPLETED
    if state.get("enrichment_paused") or state.get("enrichment_pipeline_paused"):
        return ENRICHMENT_STATUS_PAUSED
    if state.get("enrichment_heartbeat_at") or (run.enrichment_refresh_attempts or 0) > 0:
        return ENRICHMENT_STATUS_RUNNING
    return ENRICHMENT_STATUS_PENDING


def parse_enrichment_heartbeat(run: MapsCensusRun) -> datetime | None:
    state = run.processing_state or {}
    raw = state.get("enrichment_heartbeat_at")
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return run.heartbeat_at


async def assert_enrichment_exit_allowed(
    session_factory,
    *,
    run_id: str,
    paused: bool,
    failed: bool,
) -> dict[str, Any]:
    """Refuse silent success when selectable work remains."""
    counts = await count_places_by_enrichment_status(session_factory, run_id=run_id)
    pending = int(counts.get(MapsPlaceEnrichmentStatus.PENDING.value, 0))
    running = int(counts.get(MapsPlaceEnrichmentStatus.RUNNING.value, 0))
    ok = (pending == 0 and running == 0) or paused or failed
    return {
        "ok": ok,
        "pending": pending,
        "running": running,
        "failed": int(counts.get(MapsPlaceEnrichmentStatus.FAILED.value, 0)),
        "completed": int(counts.get(MapsPlaceEnrichmentStatus.COMPLETED.value, 0)),
        "skipped": int(counts.get(MapsPlaceEnrichmentStatus.SKIPPED.value, 0)),
        "counts": counts,
    }


__all__ = [
    "ENRICHMENT_STATUS_COMPLETED",
    "ENRICHMENT_STATUS_FAILED_RETRYABLE",
    "ENRICHMENT_STATUS_PAUSED",
    "ENRICHMENT_STATUS_PENDING",
    "ENRICHMENT_STATUS_RUNNING",
    "ENRICHMENT_STATUS_STALE_FAILED",
    "MAX_ENRICHMENT_AUTO_RESUMES",
    "assert_enrichment_exit_allowed",
    "count_places_by_enrichment_status",
    "enrichment_status_from_run",
    "parse_enrichment_heartbeat",
    "persist_enrichment_progress",
]
