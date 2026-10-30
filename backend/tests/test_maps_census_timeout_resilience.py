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
async def test_country_profile_is_not_rebuilt_on_watchdog_resume(db, auth, monkeypatch):
    """Regression for run 0afe4ca7-2dd3-47aa-a2cd-566e6b19b208: every
    watchdog-triggered resume of a stalled (Gosom breaker open) campaign
    previously re-ran build_profile_for_run unconditionally — a real paid
    sonar-pro country-profile call repeated on every ~10-minute recovery
    cycle for as long as Gosom stayed unavailable, confirmed via worker
    logs re-rendering maps_country_profile.j2 on each resume. Once the
    profile has genuinely completed, a second run_census() call for the
    same run_id must not call it again.
    """
    from app.db.models import MapsCensusRun as _MapsCensusRun
    from app.db.models import MapsCountryProfileStatus

    calls: list[str] = []

    async def fake_build_profile(_db, *, run_id: str):
        calls.append(run_id)
        run = await db.get(_MapsCensusRun, run_id)
        run.country_profile_status = MapsCountryProfileStatus.COMPLETED.value
        await db.commit()

    monkeypatch.setattr(maps_country_profile_service, "build_profile_for_run", fake_build_profile)

    run = await _create_run(db, auth)
    seed = [MapsGridCell(region_name="Finland", city_name="Helsinki", query_text="rehab Helsinki")]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )

    from app.services.scraping.maps_places_client import PlaceResult

    client = _FakePlacesClient(
        by_query={"rehab Helsinki": [PlaceResult(google_place_id="g1", raw_name="Facility", formatted_address="x")]}
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", lambda *_args, **_kwargs: client
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None
    assert calls == [run.id]  # first entry: the profile genuinely gets built

    # Simulate a watchdog-triggered resume for the same run_id (e.g. after
    # Gosom wedged and the stale-heartbeat recovery relaunched this job) —
    # no new cells, just a second call into the same resumable run_census.
    summary2 = await maps_census_service.run_census(db, run_id=run.id)
    assert summary2.get("error") is None
    assert calls == [run.id]  # unchanged — not called a second time


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
        "app.services.scraping.maps_census_service.create_places_client", lambda *_args, **_kwargs: client
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
        "app.services.scraping.maps_census_service.create_places_client", lambda *_args, **_kwargs: client
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
        "app.services.scraping.maps_census_service.create_places_client", lambda *_args, **_kwargs: client
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
async def test_breaker_open_stops_expansion_planning_not_just_cell_execution(db, auth, monkeypatch):
    """Regression for the Finland incident (run 72198d39-307e-4854-a6c5-
    101d3815bbae): _execute_cells correctly stops feeding new Gosom work
    once its breaker opens (see the sibling test above), but the OUTER
    campaign loop did not check that before deciding whether to expand —
    it only looked at campaign_cells_used/expanding_names, which still
    looked "not yet saturated" because the two cells that succeeded before
    the breaker tripped each found a new place. That let it call the paid
    grid planner again every iteration, manufacturing more PENDING cells
    that could never be executed, forever (or until the 1500-cell campaign
    cap). This must never call the planner a second time, and cells_total
    must never grow past the seed wave while the breaker is open.
    """
    from app.services.scraping.maps_places_client import PlaceResult, PlacesTimeoutError

    monkeypatch.setattr(get_settings(), "maps_discovery_provider", "gosom")
    monkeypatch.setattr(get_settings(), "maps_gosom_circuit_breaker_failure_threshold", 1)
    monkeypatch.setattr(get_settings(), "maps_census_cell_timeout_max_attempts", 1)
    monkeypatch.setattr(get_settings(), "maps_census_saturation_window", 20)
    monkeypatch.setattr(get_settings(), "maps_census_min_new_unique_for_expansion", 1)

    run = await _create_run(db, auth)
    seed = [
        MapsGridCell(region_name="Finland", city_name="Helsinki", query_text="rehab Helsinki 1"),
        MapsGridCell(region_name="Finland", city_name="Helsinki", query_text="rehab Helsinki 2"),
        MapsGridCell(region_name="Finland", city_name="Helsinki", query_text="rehab Helsinki 3"),
        MapsGridCell(region_name="Finland", city_name="Helsinki", query_text="rehab Helsinki 4"),
    ]
    # A second wave the planner would hand out IF asked again — the bug
    # under test is exactly whether it ever gets asked.
    wave2 = [
        MapsGridCell(region_name="Finland", city_name="Helsinki", query_text="rehab Helsinki expansion")
    ]
    from tests.test_maps_census_service import _CountingGridPlanner

    planner = _CountingGridPlanner([seed, wave2])
    monkeypatch.setattr("app.services.scraping.maps_census_service.maps_grid_planner", planner)

    timeout_error = PlacesTimeoutError("gosom job exceeded 480s")
    client = _FakePlacesClient(
        by_query={
            "rehab Helsinki 1": [PlaceResult(google_place_id="h1", raw_name="Facility 1", formatted_address="x")],
            "rehab Helsinki 2": [PlaceResult(google_place_id="h2", raw_name="Facility 2", formatted_address="x")],
        },
        errors_by_query={
            "rehab Helsinki 3": timeout_error,
            "rehab Helsinki 4": timeout_error,
        },
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", lambda *_args, **_kwargs: client
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    # The planner was asked for the seed wave only — never asked again for
    # an expansion wave while the breaker stayed open.
    assert len(planner.calls) == 1

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    # Exactly the 4 seed cells — no expansion-wave cell was ever persisted.
    assert len(cells) == 4
    by_query = {c.query_text: c for c in cells}
    assert by_query["rehab Helsinki 1"].status == MapsCensusCellStatus.COMPLETED
    assert by_query["rehab Helsinki 2"].status == MapsCensusCellStatus.COMPLETED
    assert by_query["rehab Helsinki 3"].status == MapsCensusCellStatus.FAILED
    # Never attempted — the breaker opened on cell 3's failure, before
    # cell 4's turn — and released back to PENDING, not left IN_PROGRESS.
    assert by_query["rehab Helsinki 4"].status == MapsCensusCellStatus.PENDING
    assert by_query["rehab Helsinki 4"].claimed_by is None

    await db.refresh(run)
    # Still genuinely pending work behind the open breaker — never falsely
    # marked COMPLETED, and cells_total must equal the real row count
    # rather than having grown from a phantom expansion wave.
    assert run.status == MapsCensusStatus.RUNNING
    assert run.cells_total == 4


@pytest.mark.asyncio
async def test_cancellation_stops_expansion_planning_not_just_cell_execution(db, auth, monkeypatch):
    """The same outer-loop blind spot, for cancellation instead of an open
    breaker: a run cancelled between _execute_cells calls must not trigger
    one more paid grid-planner call before the campaign notices and stops.
    """
    from app.db.models import MapsCensusStatus as _Status
    from app.services.scraping.maps_places_client import PlaceResult

    monkeypatch.setattr(get_settings(), "maps_census_saturation_window", 20)
    monkeypatch.setattr(get_settings(), "maps_census_min_new_unique_for_expansion", 1)

    run = await _create_run(db, auth)
    seed = [MapsGridCell(region_name="Finland", city_name="Turku", query_text="rehab Turku 1")]
    wave2 = [MapsGridCell(region_name="Finland", city_name="Turku", query_text="rehab Turku expansion")]
    from tests.test_maps_census_service import _CountingGridPlanner

    planner = _CountingGridPlanner([seed, wave2])
    monkeypatch.setattr("app.services.scraping.maps_census_service.maps_grid_planner", planner)

    place = PlaceResult(google_place_id="t1", raw_name="Facility 1", formatted_address="x")

    class _CancellingClient(_FakePlacesClient):
        """Cancels the run from inside the one seed cell's own call — the
        cell loop's own cancellation check (checked at the top of each
        iteration) only notices on its *next* pass, so this simulates
        cancellation landing exactly between _execute_cells finishing its
        batch and the outer loop deciding whether to expand."""

        async def search_text_paginated(self, **kwargs):
            outcome = await super().search_text_paginated(**kwargs)
            run.status = _Status.CANCELLED
            await db.commit()
            return outcome

    client = _CancellingClient({"rehab Turku 1": [place]})
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", lambda *_args, **_kwargs: client
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    # Cancelled right after the seed wave — the expansion wave must never
    # have been planned.
    assert len(planner.calls) == 1

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    assert len(cells) == 1


@pytest.mark.asyncio
async def test_circuit_breaker_open_mid_iteration_releases_the_claimed_cell(db, auth, monkeypatch):
    """Task 3: a cell that was already claimed (PENDING -> IN_PROGRESS) when
    CircuitBreakerOpenError fires from inside gosom_breaker.call() itself —
    the defense-in-depth path, distinct from the pre-loop check that
    normally stops a cell from ever being claimed in the first place — must
    be released back to PENDING/unclaimed, not left stuck IN_PROGRESS
    forever (nothing else will ever claim or heartbeat it again).
    """
    from app.services.scraping.maps_circuit_breaker import CircuitBreaker, CircuitBreakerOpenError

    monkeypatch.setattr(get_settings(), "maps_discovery_provider", "gosom")

    run = await _create_run(db, auth)
    seed = [MapsGridCell(region_name="Finland", city_name="Oulu", query_text="rehab Oulu")]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda *_args, **_kwargs: _FakePlacesClient({}),
    )

    async def _raise_open(self, fn):
        del fn
        raise CircuitBreakerOpenError("forced mid-iteration for this test")

    # The breaker's own state stays CLOSED throughout — this specifically
    # exercises the "opened mid-iteration" defensive except block, not the
    # ordinary pre-loop check (which only ever fires when state == OPEN and
    # would stop the cell from being claimed at all).
    monkeypatch.setattr(CircuitBreaker, "call", _raise_open)

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    assert len(cells) == 1
    assert cells[0].status == MapsCensusCellStatus.PENDING
    assert cells[0].claimed_by is None

    await db.refresh(run)
    assert run.status == MapsCensusStatus.RUNNING


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
        "app.services.scraping.maps_census_service.create_places_client", lambda *_args, **_kwargs: client
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
