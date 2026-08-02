"""Safe campaign finalization / state reconciliation for Maps census runs.

Reconciles dashboard counters and stage statuses from persisted rows only.
Never requeues discovery, website search, crawl, classification, or detail
enrichment. Never resets places, cells, websites, crawl cache, or evidence.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    MapsCensusCell,
    MapsCensusCellStatus,
    MapsCensusRun,
    MapsCensusStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
)
from app.services.scraping.maps_enrichment_processing_state import (
    MapsEnrichmentPipelineState,
)
from app.services.scraping.maps_enrichment_progress import (
    batch_lock_is_active,
    enrichment_status_from_run,
)

logger = logging.getLogger(__name__)

STAGE_NOT_STARTED = "not_started"
STAGE_QUEUED = "queued"
STAGE_RUNNING = "running"
STAGE_COMPLETED = "completed"
STAGE_COMPLETED_WITH_FAILURES = "completed_with_failures"
STAGE_FAILED = "failed"
STAGE_PAUSED = "paused"
STAGE_CANCELLED = "cancelled"

TERMINAL_STAGES = frozenset(
    {
        STAGE_COMPLETED,
        STAGE_COMPLETED_WITH_FAILURES,
        STAGE_FAILED,
        STAGE_CANCELLED,
        STAGE_NOT_STARTED,
    }
)


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value or "")


async def collect_run_stage_snapshot(session: AsyncSession, *, run_id: str) -> dict[str, Any]:
    """Read-only inventory of cells/places used for reconciliation decisions."""
    cell_rows = (
        await session.execute(
            select(MapsCensusCell.status, func.count())
            .where(MapsCensusCell.run_id == run_id)
            .group_by(MapsCensusCell.status)
        )
    ).all()
    cell_counts = {_status_value(status): int(count) for status, count in cell_rows}
    total_cells = sum(cell_counts.values())
    expansion_cells = int(
        (
            await session.execute(
                select(func.count()).select_from(MapsCensusCell).where(
                    MapsCensusCell.run_id == run_id,
                    or_(
                        MapsCensusCell.parent_cell_id.is_not(None),
                        MapsCensusCell.expansion_depth > 0,
                    ),
                )
            )
        ).scalar_one()
        or 0
    )

    place_status_rows = (
        await session.execute(
            select(MapsPlace.enrichment_status, func.count())
            .where(MapsPlace.run_id == run_id)
            .group_by(MapsPlace.enrichment_status)
        )
    ).all()
    place_status = {_status_value(status) or "?": int(count) for status, count in place_status_rows}

    pipeline_rows = (
        await session.execute(
            select(MapsPlace.enrichment_pipeline_state, func.count())
            .where(MapsPlace.run_id == run_id)
            .group_by(MapsPlace.enrichment_pipeline_state)
        )
    ).all()
    pipeline = {(state or "?"): int(count) for state, count in pipeline_rows}

    relevant_total = int(
        (
            await session.execute(
                select(func.count()).select_from(MapsPlace).where(
                    MapsPlace.run_id == run_id, MapsPlace.is_relevant.is_(True)
                )
            )
        ).scalar_one()
        or 0
    )
    relevant_with_website = int(
        (
            await session.execute(
                select(func.count()).select_from(MapsPlace).where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.is_relevant.is_(True),
                    or_(
                        MapsPlace.official_website.is_not(None)
                        & (MapsPlace.official_website != ""),
                        MapsPlace.raw_website.is_not(None) & (MapsPlace.raw_website != ""),
                    ),
                )
            )
        ).scalar_one()
        or 0
    )
    last_place_activity = (
        await session.execute(
            select(func.max(MapsPlace.updated_at)).where(MapsPlace.run_id == run_id)
        )
    ).scalar_one()
    last_cell_activity = (
        await session.execute(
            select(func.max(MapsCensusCell.updated_at)).where(MapsCensusCell.run_id == run_id)
        )
    ).scalar_one()

    pending_cells = cell_counts.get(MapsCensusCellStatus.PENDING.value, 0)
    running_cells = cell_counts.get(MapsCensusCellStatus.IN_PROGRESS.value, 0)
    failed_cells = cell_counts.get(MapsCensusCellStatus.FAILED.value, 0)
    completed_cells = cell_counts.get(MapsCensusCellStatus.COMPLETED.value, 0) + cell_counts.get(
        MapsCensusCellStatus.CAPPED.value, 0
    )

    classify_active = (
        place_status.get(MapsPlaceEnrichmentStatus.PENDING.value, 0)
        + place_status.get(MapsPlaceEnrichmentStatus.RUNNING.value, 0)
        + place_status.get(MapsPlaceEnrichmentStatus.FAILED.value, 0)
    )
    # Detail-only active rows: pending/failed with classification_completed /
    # detail_enrichment_* pipeline states. Classification-active rows are the
    # remaining pending/failed relevant places without a finished classification.
    detail_pipeline_active = sum(
        count
        for state, count in pipeline.items()
        if state
        in {
            MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value,
            MapsEnrichmentPipelineState.DETAIL_ENRICHMENT_PENDING.value,
            MapsEnrichmentPipelineState.DETAIL_ENRICHMENT_FAILED.value,
        }
    )

    return {
        "cell_counts": cell_counts,
        "total_cells": total_cells,
        "expansion_cells": expansion_cells,
        "initial_cells": max(0, total_cells - expansion_cells),
        "pending_cells": pending_cells,
        "running_cells": running_cells,
        "failed_cells": failed_cells,
        "completed_cells": completed_cells,
        "capped_cells": cell_counts.get(MapsCensusCellStatus.CAPPED.value, 0),
        "place_enrichment_status": place_status,
        "pipeline_counts": pipeline,
        "relevant_total": relevant_total,
        "relevant_with_website": relevant_with_website,
        "relevant_without_website": max(0, relevant_total - relevant_with_website),
        "enrichment_pending": place_status.get(MapsPlaceEnrichmentStatus.PENDING.value, 0),
        "enrichment_running": place_status.get(MapsPlaceEnrichmentStatus.RUNNING.value, 0),
        "enrichment_failed": place_status.get(MapsPlaceEnrichmentStatus.FAILED.value, 0),
        "enrichment_completed": place_status.get(MapsPlaceEnrichmentStatus.COMPLETED.value, 0),
        "enrichment_skipped": place_status.get(MapsPlaceEnrichmentStatus.SKIPPED.value, 0),
        "classify_or_detail_active": classify_active,
        "detail_pipeline_markers": detail_pipeline_active,
        "last_place_activity": last_place_activity,
        "last_cell_activity": last_cell_activity,
        "discovery_has_active_work": bool(pending_cells or running_cells),
        "enrichment_has_active_work": bool(
            place_status.get(MapsPlaceEnrichmentStatus.PENDING.value, 0)
            or place_status.get(MapsPlaceEnrichmentStatus.RUNNING.value, 0)
        ),
    }


def _stage_from_counts(
    *,
    active: bool,
    failed: int,
    completed_like: int,
    ever_started: bool,
) -> str:
    if active:
        return STAGE_RUNNING
    if not ever_started and completed_like == 0 and failed == 0:
        return STAGE_NOT_STARTED
    if failed > 0 and completed_like > 0:
        return STAGE_COMPLETED_WITH_FAILURES
    if failed > 0 and completed_like == 0:
        return STAGE_FAILED
    return STAGE_COMPLETED


def derive_stage_statuses(run: MapsCensusRun, snap: dict[str, Any]) -> dict[str, str]:
    state = run.processing_state or {}
    enrichment = enrichment_status_from_run(run)

    discovery = _stage_from_counts(
        active=bool(snap["discovery_has_active_work"]),
        failed=int(snap["failed_cells"]),
        completed_like=int(snap["completed_cells"]),
        ever_started=int(snap["total_cells"]) > 0,
    )

    website_missing = int(snap["relevant_without_website"])
    website_attempts = int(run.website_refresh_attempts or 0)
    website_done_ts = run.website_refresh_completed_at is not None
    if state.get("website_search_paused"):
        website = STAGE_PAUSED
    elif website_attempts == 0 and not website_done_ts and snap["relevant_with_website"] == 0:
        website = STAGE_NOT_STARTED
    elif website_missing > 0 and website_attempts > 0:
        website = STAGE_COMPLETED_WITH_FAILURES
    elif website_missing > 0 and website_attempts == 0 and snap["relevant_with_website"] > 0:
        # Places already have some websites from discovery; refresh never stamped.
        website = STAGE_COMPLETED_WITH_FAILURES
    else:
        website = STAGE_COMPLETED

    crawl_skip = list((state.get("crawl_skip_domains") or []))
    if enrichment in {"running", "paused"}:
        crawl = STAGE_RUNNING if enrichment == "running" else STAGE_PAUSED
    elif crawl_skip:
        crawl = STAGE_COMPLETED_WITH_FAILURES
    elif enrichment in {"completed", "failed_retryable", "stale_failed"}:
        crawl = STAGE_COMPLETED
    else:
        crawl = STAGE_NOT_STARTED

    if snap["enrichment_running"] > 0 or snap["enrichment_pending"] > 0:
        # Only queued/running rows keep enrichment stages non-terminal.
        # Failed leftovers are terminal (completed_with_failures) — they do not
        # imply the campaign is still processing.
        classification = STAGE_RUNNING
        detail = STAGE_RUNNING
    elif enrichment == "paused" or state.get("enrichment_pipeline_paused"):
        classification = STAGE_PAUSED
        detail = STAGE_PAUSED
    elif enrichment == "completed" or (
        snap["enrichment_pending"] == 0
        and snap["enrichment_running"] == 0
        and (snap["enrichment_completed"] + snap["enrichment_skipped"] + snap["enrichment_failed"])
        > 0
    ):
        if snap["enrichment_failed"] > 0:
            classification = STAGE_COMPLETED_WITH_FAILURES
            detail = STAGE_COMPLETED_WITH_FAILURES
        else:
            classification = STAGE_COMPLETED
            detail = STAGE_COMPLETED
    else:
        classification = STAGE_NOT_STARTED
        detail = STAGE_NOT_STARTED

    warnings = {
        discovery,
        website,
        crawl,
        classification,
        detail,
    } & {STAGE_COMPLETED_WITH_FAILURES, STAGE_FAILED}
    active = {
        discovery,
        website,
        crawl,
        classification,
        detail,
    } & {STAGE_RUNNING, STAGE_QUEUED, STAGE_PAUSED}

    if active:
        overall = STAGE_RUNNING
    elif STAGE_FAILED in warnings and STAGE_COMPLETED not in {
        discovery,
        classification,
        detail,
    }:
        overall = STAGE_FAILED
    elif warnings:
        overall = "completed_with_warnings"
    else:
        overall = STAGE_COMPLETED

    return {
        "discovery_status": discovery,
        "website_discovery_status": website,
        "crawl_status": crawl,
        "classification_status": classification,
        "detail_enrichment_status": detail,
        "overall_status": overall,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _latest_activity(*values: datetime | None) -> datetime | None:
    present = [_as_utc(value) for value in values if value is not None]
    if not present:
        return None
    return max(present)


async def reconcile_run_finalization(
    session: AsyncSession,
    *,
    run_id: str,
    force: bool = False,
) -> dict[str, Any]:
    """Reconcile counters/statuses for one run without reprocessing work.

    Safety gates refuse terminalization when discovery/enrichment rows are still
    queued or running, or when a batch lock is still active — unless ``force``
    is explicitly set by an admin after inspection.
    """
    run = await session.get(MapsCensusRun, run_id)
    if run is None:
        raise ValueError(f"Maps census run not found: {run_id}")

    snap = await collect_run_stage_snapshot(session, run_id=run_id)
    stages = derive_stage_statuses(run, snap)
    lock_active = batch_lock_is_active(run)

    blockers: list[str] = []
    if snap["discovery_has_active_work"]:
        blockers.append("discovery_cells_active")
    if snap["enrichment_has_active_work"]:
        blockers.append("enrichment_places_active")
    if lock_active:
        blockers.append("active_batch_lock")

    if blockers and not force:
        return {
            "run_id": run_id,
            "reconciled": False,
            "blockers": blockers,
            "snapshot": snap,
            "derived_stages": stages,
            "message": "Refusing finalization while active work or locks remain",
        }

    # Preserve original seed denominator once, then align counters to reality.
    state = dict(run.processing_state or {})
    if "initial_cells" not in state and run.cells_total:
        # If the persisted cells_total is the stale seed count (smaller than
        # actual rows), keep it as initial_cells for dashboard reporting.
        if run.cells_total < snap["total_cells"]:
            state["initial_cells"] = int(run.cells_total)
        else:
            state["initial_cells"] = int(snap["initial_cells"])
    initial_cells = int(state.get("initial_cells") or snap["initial_cells"] or snap["total_cells"])
    expansion_cells = max(0, int(snap["total_cells"]) - initial_cells)

    cell_metrics = {
        "initial_cells": initial_cells,
        "expansion_cells": expansion_cells,
        "total_cells": int(snap["total_cells"]),
        "completed_cells": int(snap["completed_cells"]),
        "pending_cells": int(snap["pending_cells"]),
        "running_cells": int(snap["running_cells"]),
        "failed_cells": int(snap["failed_cells"]),
        "capped_cells": int(snap["capped_cells"]),
    }

    last_activity = _latest_activity(
        snap["last_place_activity"],
        snap["last_cell_activity"],
        run.enrichment_refresh_completed_at,
        run.completed_at,
        run.updated_at,
    )

    # Website timestamp: only when website stage is terminal.
    if stages["website_discovery_status"] in {
        STAGE_COMPLETED,
        STAGE_COMPLETED_WITH_FAILURES,
    } and run.website_refresh_completed_at is None:
        run.website_refresh_completed_at = last_activity or datetime.now(UTC)

    if stages["discovery_status"] in {STAGE_COMPLETED, STAGE_COMPLETED_WITH_FAILURES}:
        # Align denominator with actual persisted cells (fixes 1134/62).
        run.cells_total = int(snap["total_cells"])
        run.cells_completed = int(snap["completed_cells"])

    if stages["detail_enrichment_status"] in {
        STAGE_COMPLETED,
        STAGE_COMPLETED_WITH_FAILURES,
    } and run.enrichment_refresh_completed_at is None:
        run.enrichment_refresh_completed_at = last_activity or datetime.now(UTC)

    overall = stages["overall_status"]
    if overall == "completed_with_warnings":
        run.status = MapsCensusStatus.COMPLETED_WITH_WARNINGS
    elif overall == STAGE_COMPLETED:
        run.status = MapsCensusStatus.COMPLETED
    elif overall == STAGE_FAILED:
        run.status = MapsCensusStatus.FAILED
    # Do not force RUNNING — reconciliation only terminalizes.

    if run.status in {
        MapsCensusStatus.COMPLETED,
        MapsCensusStatus.COMPLETED_WITH_WARNINGS,
    }:
        if run.completed_at is None:
            run.completed_at = last_activity or datetime.now(UTC)
        # Completed campaigns do not need a live heartbeat.
        run.heartbeat_at = None

    state["cell_metrics"] = cell_metrics
    state["stage_statuses"] = stages
    state["overall_status"] = overall
    state["last_activity_at"] = (
        last_activity.isoformat() if last_activity is not None else None
    )
    state["finalization"] = {
        "reconciled_at": datetime.now(UTC).isoformat(),
        "mode": "state_reconciliation_only",
        "reran_discovery": False,
        "reran_enrichment": False,
        "blockers_ignored": blockers if force else [],
    }
    # Clear stale enrichment anomaly markers once the campaign is terminal.
    if overall in {STAGE_COMPLETED, "completed_with_warnings"}:
        state.pop("enrichment_exit_anomaly", None)
        if state.get("enrichment_status") != "completed":
            state["enrichment_status"] = "completed"
        state["current_phase"] = "completed"
    run.processing_state = state

    await session.commit()
    logger.info(
        "maps_run_reconciled run_id=%s overall=%s total_cells=%s",
        run_id,
        overall,
        snap["total_cells"],
    )
    return {
        "run_id": run_id,
        "reconciled": True,
        "blockers": [],
        "snapshot": snap,
        "cell_metrics": cell_metrics,
        "stage_statuses": stages,
        "overall_status": overall,
        "website_refresh_completed_at": (
            run.website_refresh_completed_at.isoformat()
            if run.website_refresh_completed_at
            else None
        ),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "run_status": _status_value(run.status),
        "message": "Reconciled from persisted rows; no processing stages were rerun",
    }


__all__ = [
    "STAGE_COMPLETED",
    "STAGE_COMPLETED_WITH_FAILURES",
    "STAGE_FAILED",
    "STAGE_NOT_STARTED",
    "STAGE_PAUSED",
    "STAGE_QUEUED",
    "STAGE_RUNNING",
    "collect_run_stage_snapshot",
    "derive_stage_statuses",
    "reconcile_run_finalization",
]
