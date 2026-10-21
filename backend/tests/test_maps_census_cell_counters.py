"""Regression tests for the adaptive cell-counter corruption bug observed in
the Alabama Gosom experiment (run f6824809-bba0-4aeb-98b3-92342c68ccfc):
``maps_census_runs.cells_completed`` reaching 531 while ``cells_total``
stayed at 58 — an impossible state (completed > total) that also showed up
mid-run as "71/69" and "514 completed out of ~524 rows".

Root cause (see maps_census_service.py / maps_cell_runner.py): every
adaptive-expansion-wave write to ``cells_total`` was a Python-level
``run.cells_total = campaign_cells_used`` — an absolute overwrite using a
purely local, per-execution running sum. A concurrent or resumed execution
with its own, smaller local sum could silently clobber the real total back
down, while ``cells_completed`` (correctly incremented via ``+= 1`` against
fresh reads at every cell completion) kept climbing from genuine work. The
fix replaces every such write with an atomic, SQL-level
``cells_total = cells_total + :by`` (``increment_run_cells_total``) and adds
a cheap, index-scoped reconciliation checkpoint
(``reconcile_run_cell_counters``) at resume and cancellation.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import MapsCensusCell, MapsCensusCellStatus, MapsCensusRun, MapsCensusStatus
from app.services.scraping.maps_census_service import maps_census_service
from app.services.scraping.maps_country_profile_service import maps_country_profile_service
from app.services.scraping.maps_grid_planner import MapsGridCell
from app.services.scraping.maps_places_client import PlaceResult, PlacesSearchOutcome

from tests.test_maps_census_service import _CountingGridPlanner, _FakePlacesClient, _create_run


@pytest.fixture(autouse=True)
def _stub_country_profile_stage(monkeypatch):
    async def _noop_build_profile(_db, *, run_id: str):
        del run_id
        return None

    monkeypatch.setattr(maps_country_profile_service, "build_profile_for_run", _noop_build_profile)


@pytest.fixture(autouse=True)
def _stub_enrichment_enqueue(monkeypatch):
    async def _noop_enqueue(self, run_id: str) -> None:
        del run_id

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._maybe_enqueue_enrichment",
        _noop_enqueue,
    )


def _place(google_id: str) -> PlaceResult:
    return PlaceResult(google_place_id=google_id, raw_name=f"Facility {google_id}", formatted_address="x")


@pytest.mark.asyncio
async def test_cells_total_matches_actual_rows_across_multiple_adaptive_waves(
    db, auth, monkeypatch
):
    """Seed wave + two expansion waves (the planner returns an empty batch
    on its 4th call, ending adaptive expansion naturally) must leave
    cells_total exactly equal to the number of cell rows actually
    persisted — never drifted, overwritten, or short of reality.
    """
    monkeypatch.setattr(get_settings(), "maps_census_saturation_window", 20)
    monkeypatch.setattr(get_settings(), "maps_census_min_new_unique_for_expansion", 1)

    seed = [
        MapsGridCell(region_name="Alabama", city_name=f"Seed{i}", query_text=f"rehab seed {i}")
        for i in range(3)
    ]
    wave2 = [
        MapsGridCell(region_name="Alabama", city_name=f"Wave2-{i}", query_text=f"rehab wave2 {i}")
        for i in range(4)
    ]
    wave3 = [
        MapsGridCell(region_name="Alabama", city_name=f"Wave3-{i}", query_text=f"rehab wave3 {i}")
        for i in range(2)
    ]
    planner = _CountingGridPlanner([seed, wave2, wave3])
    monkeypatch.setattr("app.services.scraping.maps_census_service.maps_grid_planner", planner)

    all_queries = [c.query_text for c in (seed + wave2 + wave3)]
    by_query = {q: [_place(q)] for q in all_queries}  # every cell finds exactly 1 new place
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient(by_query),
    )

    run = await _create_run(db, auth)
    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    await db.refresh(run)
    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()

    assert len(planner.calls) == 4  # seed + wave2 + wave3 + one empty call that ends expansion
    assert len(cells) == 9  # 3 + 4 + 2, no subdivision in this scenario
    assert run.cells_total == len(cells)
    assert run.cells_completed == len(cells)
    assert run.cells_completed <= run.cells_total


@pytest.mark.asyncio
async def test_reconciles_the_exact_reported_impossible_state_on_resume(db, auth, monkeypatch):
    """Directly reproduces the reported numbers before fixing them: a run
    whose stored counters (cells_completed=531, cells_total=58) are wildly
    inconsistent with its actual cell rows. Resuming it (the watchdog/
    recovery path) must reconcile both values from ground truth *before*
    any further processing — verified here by pausing the campaign so no
    further processing happens at all, isolating the resume-entry fix.
    """
    run = await _create_run(db, auth)
    run.cells_total = 58
    run.cells_completed = 531
    run.processing_state = {"campaign_paused": True}
    await db.commit()

    for i in range(3):
        db.add(
            MapsCensusCell(
                run_id=run.id,
                region_name="Alabama",
                city_name="Huntsville",
                query_text=f"existing cell {i}",
                status=MapsCensusCellStatus.COMPLETED if i < 2 else MapsCensusCellStatus.PENDING,
            )
        )
    await db.commit()

    def _boom_plan(**_kwargs):
        raise AssertionError("resume must never re-enter seed grid planning")

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        type("P", (), {"plan": staticmethod(_boom_plan)})(),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient({}),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    await db.refresh(run)
    # Reconciled from the 3 real rows (2 completed, 1 still pending) —
    # not the stale 531/58, and not simply clamped (531 -> 58) either.
    assert run.cells_total == 3
    assert run.cells_completed == 2
    assert run.cells_completed <= run.cells_total
    assert run.status == MapsCensusStatus.RUNNING  # one cell still genuinely pending


@pytest.mark.asyncio
async def test_phase_2_still_never_starts_while_cells_are_pending(db, auth, monkeypatch):
    """Regression guard: the counter fix must not loosen the existing,
    independent Phase 1 -> Phase 2 gate, which checks actual non-terminal
    cell rows, not cells_total/cells_completed."""
    run = await _create_run(db, auth)
    seed = [MapsGridCell(region_name="Alabama", city_name="Mobile", query_text="rehab Mobile")]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _CountingGridPlanner([seed])
    )
    monkeypatch.setattr(get_settings(), "maps_census_cell_timeout_max_attempts", 1)

    from app.services.scraping.maps_places_client import PlacesTimeoutError

    client = _FakePlacesClient(
        {}, errors_by_query={"rehab Mobile": PlacesTimeoutError("gosom job exceeded 480s")}
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", lambda: client
    )

    enqueue_calls: list[str] = []

    async def fake_enqueue(self, run_id: str) -> None:
        enqueue_calls.append(run_id)

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._maybe_enqueue_enrichment",
        fake_enqueue,
    )

    # Force the single cell to stay unresolved (neither terminal nor
    # immediately retryable) by making the backoff long relative to this
    # test's single pass — the cell lands PENDING with a future
    # next_retry_at, so it is non-terminal, and Phase 2 must not fire.
    monkeypatch.setattr(get_settings(), "maps_census_cell_timeout_retry_base_seconds", 1)
    monkeypatch.setattr(get_settings(), "maps_census_cell_timeout_max_attempts", 2)

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None
    assert enqueue_calls == []

    await db.refresh(run)
    assert run.status != MapsCensusStatus.COMPLETED
    assert run.status != MapsCensusStatus.COMPLETED_WITH_WARNINGS
