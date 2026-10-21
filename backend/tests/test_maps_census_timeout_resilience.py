"""Verifies the fix for the production incident where several unrelated
Gosom queries timed out back to back and stalled the campaign: a tighter,
dedicated retry policy for provider timeouts (fewer attempts, much longer
backoff than a generic transient error), and confirmation that a timing-out
cell never blocks the rest of the campaign from making progress or reaching
a terminal state.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import MapsCensusCell, MapsCensusCellStatus, MapsCensusStatus
from app.services.scraping.maps_census_service import maps_census_service
from app.services.scraping.maps_country_profile_service import maps_country_profile_service
from app.services.scraping.maps_grid_planner import MapsGridCell
from app.services.scraping.maps_places_client import PlacesTimeoutError

from tests.test_maps_census_service import _FakeGridPlanner, _FakePlacesClient, _create_run


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


@pytest.mark.asyncio
async def test_timeout_terminates_after_configured_attempts_without_blocking_other_cells(
    db, auth, monkeypatch
):
    """Two cells: one whose provider call always raises a timeout, one that
    succeeds normally. Even with the timeout-tightened policy set to a
    single attempt, the healthy cell must still complete — a failing
    neighbor must never block it — and the campaign must reach a terminal
    status rather than get stuck.
    """
    monkeypatch.setattr(get_settings(), "maps_census_cell_timeout_max_attempts", 1)
    monkeypatch.setattr(get_settings(), "maps_census_cell_timeout_retry_base_seconds", 1)

    run = await _create_run(db, auth)
    seed = [
        MapsGridCell(region_name="Alabama", city_name="Decatur", query_text="addiction treatment Decatur"),
        MapsGridCell(region_name="Alabama", city_name="Decatur", query_text="residential treatment Decatur"),
    ]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )

    from app.services.scraping.maps_places_client import PlaceResult

    healthy_place = PlaceResult(
        google_place_id="decatur-1",
        raw_name="Decatur Recovery Center",
        formatted_address="1 Main St, Decatur, AL",
    )
    client = _FakePlacesClient(
        by_query={"residential treatment Decatur": [healthy_place]},
        errors_by_query={"addiction treatment Decatur": PlacesTimeoutError("gosom job exceeded 900s")},
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", lambda: client
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    # The timed-out query was only attempted once (the tightened cap), not
    # retried forever.
    timeout_attempts = [c for c in client.paginated_calls if c["query"] == "addiction treatment Decatur"]
    assert len(timeout_attempts) == 1

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    by_query = {c.query_text: c for c in cells}
    assert by_query["addiction treatment Decatur"].status == MapsCensusCellStatus.FAILED
    assert by_query["residential treatment Decatur"].status == MapsCensusCellStatus.COMPLETED

    await db.refresh(run)
    # The campaign still reaches a terminal state — a failing neighbor cell
    # does not prevent the overall run from completing — but honestly
    # reflects that one cell permanently failed, rather than reading as
    # full, clean coverage.
    assert run.status == MapsCensusStatus.COMPLETED_WITH_WARNINGS
    assert run.places_found == 1


@pytest.mark.asyncio
async def test_responsive_but_non_dispatching_gosom_stops_feeding_new_work(db, auth, monkeypatch):
    """The github.com/gosom/google-maps-scraper#328 scenario: gosom's REST
    API keeps answering (every request we make succeeds in posting/polling
    a job), but the jobs themselves never make real progress — every one
    times out. After enough consecutive timeouts, the gosom circuit
    breaker must open and stop submitting further jobs rather than burning
    through the whole remaining queue one 8-minute timeout at a time; cells
    behind the opened breaker stay PENDING (untouched), not FAILED.
    """
    monkeypatch.setattr(get_settings(), "maps_discovery_provider", "gosom")
    monkeypatch.setattr(get_settings(), "maps_gosom_circuit_breaker_failure_threshold", 2)
    monkeypatch.setattr(get_settings(), "maps_census_cell_timeout_max_attempts", 1)

    run = await _create_run(db, auth)
    seed = [
        MapsGridCell(region_name="Alabama", city_name="Anniston", query_text="addiction treatment Anniston"),
        MapsGridCell(region_name="Alabama", city_name="Anniston", query_text="residential treatment Anniston"),
        MapsGridCell(region_name="Alabama", city_name="Anniston", query_text="drug detox Anniston"),
    ]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )

    timeout_error = PlacesTimeoutError("gosom job exceeded 480s")
    client = _FakePlacesClient(
        by_query={},
        errors_by_query={
            "addiction treatment Anniston": timeout_error,
            "residential treatment Anniston": timeout_error,
            "drug detox Anniston": timeout_error,
        },
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", lambda: client
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    # Breaker threshold is 2 — only the first two cells were ever actually
    # submitted to the (simulated) saturated provider.
    assert len(client.paginated_calls) == 2

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    by_query = {c.query_text: c for c in cells}
    assert by_query["addiction treatment Anniston"].status == MapsCensusCellStatus.FAILED
    assert by_query["residential treatment Anniston"].status == MapsCensusCellStatus.FAILED
    # Never even attempted — the breaker opened before this cell's turn.
    assert by_query["drug detox Anniston"].status == MapsCensusCellStatus.PENDING

    await db.refresh(run)
    # Not falsely marked COMPLETED while a cell is still genuinely pending
    # behind the open breaker.
    assert run.status == MapsCensusStatus.RUNNING


@pytest.mark.asyncio
async def test_absolute_deadline_fires_independently_of_gosom_and_heartbeat(db, auth, monkeypatch):
    """The hard, independent backstop: even if the provider never raises its
    own timeout at all (e.g. gosom's dispatcher is simply wedged and never
    responds, rather than cleanly reporting 'exceeded 480s'), and even
    though the heartbeat keeps firing throughout (proving it can't be what
    resets the deadline), MultiMind's own per-cell budget still cuts the
    call off — marking the cell terminally FAILED in exactly one attempt,
    never silently hanging forever.
    """
    # The setting is floored to >=1 whole second internally (max(1, int(...))),
    # so the fake client below must sleep well past 1s regardless of the
    # literal value configured here.
    monkeypatch.setattr(get_settings(), "maps_census_cell_absolute_deadline_seconds", 1)

    import asyncio

    run = await _create_run(db, auth)
    seed = [MapsGridCell(region_name="Alabama", city_name="Huntsville", query_text="MAT Huntsville")]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )

    heartbeats: list[float] = []

    class _WedgedClient(_FakePlacesClient):
        async def search_text_paginated(self, *, heartbeat_cb=None, **kwargs):
            # Keeps calling the heartbeat (proving it stays "alive" by that
            # signal) while never actually returning — simulating a gosom
            # dispatcher that accepted the job but never progresses it.
            for _ in range(30):
                if heartbeat_cb is not None:
                    await heartbeat_cb()
                heartbeats.append(asyncio.get_running_loop().time())
                await asyncio.sleep(0.1)
            raise AssertionError("should have been cut off by the absolute deadline first")

    client = _WedgedClient({})
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", lambda: client
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    assert len(cells) == 1
    cell = cells[0]
    assert cell.status == MapsCensusCellStatus.FAILED
    assert cell.first_attempted_at is not None
    assert "deadline" in (cell.last_error or "")

    await db.refresh(run)
    # Reaches a terminal state (no other cells pending behind it) but
    # honestly as COMPLETED_WITH_WARNINGS, not a clean COMPLETED — one
    # cell never produced results.
    assert run.status == MapsCensusStatus.COMPLETED_WITH_WARNINGS


@pytest.mark.asyncio
async def test_timeout_retry_is_not_attempted_before_its_backoff_is_due(db, auth, monkeypatch):
    """With more than one allowed attempt, a retry must not fire
    immediately when the local work queue cycles back to it — it must wait
    out the (long, for timeouts) backoff. Verified at the orchestration
    level: within a single run_census call, the retry is correctly deferred
    rather than hammering the same saturated backend again right away.
    """
    monkeypatch.setattr(get_settings(), "maps_census_cell_timeout_max_attempts", 2)
    monkeypatch.setattr(get_settings(), "maps_census_cell_timeout_retry_base_seconds", 300)

    run = await _create_run(db, auth)
    seed = [MapsGridCell(region_name="Alabama", city_name="Decatur", query_text="addiction treatment Decatur")]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )

    client = _FakePlacesClient(
        by_query={},
        errors_by_query={"addiction treatment Decatur": PlacesTimeoutError("gosom job exceeded 900s")},
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", lambda: client
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    # Only the first attempt happened — the retry (5-minute backoff) was
    # correctly deferred rather than fired immediately within this call.
    assert len(client.paginated_calls) == 1

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    assert len(cells) == 1
    assert cells[0].status == MapsCensusCellStatus.PENDING
    assert cells[0].next_retry_at is not None

    await db.refresh(run)
    # The run must not be falsely marked COMPLETED while a retry is still
    # genuinely pending — it stays RUNNING for a later resume to pick up.
    assert run.status == MapsCensusStatus.RUNNING
