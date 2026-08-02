"""Tests for Maps census finalization / state reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.db.models import (
    MapsCensusCell,
    MapsCensusCellStatus,
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
)
from app.services.scraping.maps_admin_service import _derive_current_stage
from app.services.scraping.maps_enrichment_processing_state import MapsEnrichmentPipelineState
from app.services.scraping.maps_run_finalization import (
    collect_run_stage_snapshot,
    derive_stage_statuses,
    reconcile_run_finalization,
)


def _run(auth, **kwargs):
    base = dict(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="DZ",
        country_name="Algeria",
        status=MapsCensusStatus.RUNNING,
        cells_total=62,
        cells_completed=1134,
        places_found=10,
        enrichment_refresh_completed_at=datetime.now(UTC),
        processing_state={"enrichment_status": "completed", "current_phase": "two_phase_complete"},
    )
    base.update(kwargs)
    return MapsCensusRun(**base)


@pytest.mark.asyncio
async def test_reconcile_fixes_stale_cell_denominator_and_terminalizes(db, auth):
    run = _run(auth)
    db.add(run)
    await db.flush()
    # Actual persisted cells exceed the stale seed denominator (62).
    for i in range(5):
        db.add(
            MapsCensusCell(
                run_id=run.id,
                region_name="Algiers",
                city_name="Algiers",
                query_text=f"rehab {i}",
                status=MapsCensusCellStatus.COMPLETED,
            )
        )
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="p1",
            raw_name="Centre",
            canonical_name="Centre",
            is_relevant=True,
            lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
            client_eligibility=MapsClientEligibility.REVIEW.value,
            enrichment_status=MapsPlaceEnrichmentStatus.COMPLETED.value,
            enrichment_pipeline_state=MapsEnrichmentPipelineState.DETAIL_NOT_REQUIRED.value,
            official_website="https://example.org",
        )
    )
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="p2",
            raw_name="No Site",
            canonical_name="No Site",
            is_relevant=True,
            lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
            client_eligibility=MapsClientEligibility.REVIEW.value,
            enrichment_status=MapsPlaceEnrichmentStatus.COMPLETED.value,
            # Missing website → website stage completed_with_failures
        )
    )
    await db.commit()

    result = await reconcile_run_finalization(db, run_id=run.id)
    assert result["reconciled"] is True
    assert result["cell_metrics"]["total_cells"] == 5
    # Stale seed denominator (62) is preserved as initial_cells when it is
    # smaller than the actual persisted total; this fixture has only 5 cells so
    # initial falls back to the actual total.
    assert result["cell_metrics"]["total_cells"] == 5
    assert result["cell_metrics"]["expansion_cells"] >= 0

    await db.refresh(run)
    assert run.cells_total == 5
    assert run.cells_completed == 5
    assert run.status in {
        MapsCensusStatus.COMPLETED,
        MapsCensusStatus.COMPLETED_WITH_WARNINGS,
    }
    assert run.website_refresh_completed_at is not None
    stages = (run.processing_state or {}).get("stage_statuses") or {}
    assert stages["discovery_status"] == "completed"
    assert stages["classification_status"] == "completed"
    assert stages["detail_enrichment_status"] == "completed"
    assert stages["overall_status"] in {"completed", "completed_with_warnings"}
    assert _derive_current_stage(run) == "completed"


@pytest.mark.asyncio
async def test_reconcile_clears_false_grid_planning_error(db, auth):
    run = _run(auth, error_message="Maps census grid planning failed.")
    db.add(run)
    await db.flush()
    db.add(
        MapsCensusCell(
            run_id=run.id,
            region_name="Algiers",
            query_text="rehab",
            status=MapsCensusCellStatus.COMPLETED,
        )
    )
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="p1",
            raw_name="Centre",
            canonical_name="Centre",
            is_relevant=True,
            lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
            client_eligibility=MapsClientEligibility.REVIEW.value,
            enrichment_status=MapsPlaceEnrichmentStatus.COMPLETED.value,
            official_website="https://example.org",
        )
    )
    await db.commit()

    result = await reconcile_run_finalization(db, run_id=run.id)
    assert result["reconciled"] is True
    await db.refresh(run)
    assert run.error_message is None
    assert run.status in {
        MapsCensusStatus.COMPLETED,
        MapsCensusStatus.COMPLETED_WITH_WARNINGS,
    }


@pytest.mark.asyncio
async def test_reconcile_refuses_when_discovery_cells_still_running(db, auth):
    run = _run(auth, cells_total=2, cells_completed=1)
    db.add(run)
    await db.flush()
    db.add(
        MapsCensusCell(
            run_id=run.id,
            region_name="Oran",
            query_text="rehab oran",
            status=MapsCensusCellStatus.IN_PROGRESS,
        )
    )
    await db.commit()

    result = await reconcile_run_finalization(db, run_id=run.id)
    assert result["reconciled"] is False
    assert "discovery_cells_active" in result["blockers"]
    await db.refresh(run)
    assert run.status == MapsCensusStatus.RUNNING


def test_derive_stage_post_processing_not_used_when_overall_completed():
    run = type(
        "R",
        (),
        {
            "status": MapsCensusStatus.COMPLETED_WITH_WARNINGS,
            "processing_state": {
                "stage_statuses": {"overall_status": "completed_with_warnings"},
                "cell_metrics": {"total_cells": 1134, "completed_cells": 1134},
            },
            "country_profile_status": None,
            "enrichment_refresh_completed_at": datetime.now(UTC),
            "enrichment_refresh_attempts": 1,
            "cells_completed": 1134,
            "cells_total": 1134,
        },
    )()
    assert _derive_current_stage(run) == "completed"


@pytest.mark.asyncio
async def test_snapshot_counts_actual_cells(db, auth):
    run = _run(auth, cells_total=62, cells_completed=0)
    db.add(run)
    await db.flush()
    db.add_all(
        [
            MapsCensusCell(
                run_id=run.id,
                region_name="A",
                query_text="q1",
                status=MapsCensusCellStatus.COMPLETED,
            ),
            MapsCensusCell(
                run_id=run.id,
                region_name="A",
                query_text="q2",
                status=MapsCensusCellStatus.COMPLETED,
                parent_cell_id=None,
                expansion_depth=1,
            ),
        ]
    )
    await db.commit()
    snap = await collect_run_stage_snapshot(db, run_id=run.id)
    assert snap["total_cells"] == 2
    assert snap["expansion_cells"] == 1
    assert snap["completed_cells"] == 2
    stages = derive_stage_statuses(run, snap)
    assert stages["discovery_status"] == "completed"
