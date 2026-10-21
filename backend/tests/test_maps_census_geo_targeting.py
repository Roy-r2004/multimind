"""End-to-end verification of geographic-targeting behavior after the
Phenix City fix:

- A city-named cell (the normal case — the grid planner always names a
  city) NEVER receives the state-wide trusted bounding box, in Alabama or
  anywhere else — a statewide box is not a valid bounding area for any one
  city, and attaching it produced exactly the production bug this fixes
  (a Phenix City query subdivided into quadrants covering the opposite
  corner of the state). Without a verified per-city coordinate source
  (which this codebase does not have), a city-named cell always falls back
  to the pre-existing text-only search.
- A genuinely region/state-wide cell with no city_name still gets the
  trusted bounding box, and viewport_subdivision still works for it end to
  end (the mechanism itself remains correct — it just must never be fed a
  box that doesn't describe the thing being searched for).
- A run with no known anchor at all (e.g. a country/state not in the
  static table) falls back to the pre-existing text-only search, with no
  viewport_bounds fabricated.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import MapsCensusCell, MapsCensusRun, MapsCensusStatus
from app.services.scraping.maps_census_service import maps_census_service
from app.services.scraping.maps_country_profile_service import maps_country_profile_service
from app.services.scraping.maps_geo_regions import resolve_geo_anchor
from app.services.scraping.maps_grid_planner import MapsGridCell
from app.services.scraping.maps_places_client import PlacesSearchOutcome

from tests.test_maps_census_service import _FakeGridPlanner, _FakePlacesClient


@pytest.fixture(autouse=True)
def _stub_country_profile_stage(monkeypatch):
    """Every run_census test must never reach the real country-profile LLM
    stage (live Sonar) — run_census calls build_profile_for_run
    unconditionally before grid planning."""

    async def _noop_build_profile(_db, *, run_id: str):
        del run_id
        return None

    monkeypatch.setattr(maps_country_profile_service, "build_profile_for_run", _noop_build_profile)


@pytest.fixture(autouse=True)
def _stub_enrichment_enqueue(monkeypatch):
    """These tests only exercise discovery/subdivision — never let
    run_census's end-of-run auto-enrichment enqueue a real (inline,
    fire-and-forget) background job that outlives the test's own db engine.
    """

    async def _noop_enqueue(self, run_id: str) -> None:
        del run_id

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._maybe_enqueue_enrichment",
        _noop_enqueue,
    )


async def _create_run(db, auth, *, country_code: str, state_code: str | None = None) -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code=country_code,
        country_name="United States" if country_code == "US" else "France",
        state_code=state_code,
        state_name="Alabama" if state_code == "AL" else None,
        status=MapsCensusStatus.QUEUED,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run


def test_resolve_geo_anchor_known_state_vs_unknown():
    alabama = resolve_geo_anchor(country_code="US", state_code="AL", state_name=None)
    assert alabama is not None
    assert alabama["north"] > alabama["south"]
    assert alabama["east"] > alabama["west"]

    assert resolve_geo_anchor(country_code="US", state_code="CA", state_name=None) is None
    assert resolve_geo_anchor(country_code="FR", state_code=None, state_name=None) is None
    assert resolve_geo_anchor(country_code=None, state_code=None, state_name=None) is None


@pytest.mark.asyncio
async def test_city_named_cells_never_receive_the_statewide_box(db, auth, monkeypatch):
    """The exact production bug: a Phenix City query (and any other
    city-named cell) must never be seeded with the Alabama-wide box — that
    box describes the whole state, not Phenix City, and quadrant-splitting
    it produces areas (e.g. the state's northwest corner) nowhere near the
    city actually named in the query text."""
    run = await _create_run(db, auth, country_code="US", state_code="AL")
    seed = [
        MapsGridCell(
            region_name="Russell County",
            city_name="Phenix City",
            query_text="community recovery organization Phenix City, Alabama",
        ),
        MapsGridCell(region_name="Jefferson County", city_name="Birmingham", query_text="rehab Birmingham"),
    ]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient({}),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    assert len(cells) == 2
    for cell in cells:
        assert cell.city_name is not None
        assert cell.viewport_bounds is None


@pytest.mark.asyncio
async def test_region_wide_cell_with_no_city_still_gets_trusted_box(db, auth, monkeypatch):
    """A genuinely region/state-wide cell (no city_name at all) is exactly
    the case a state-wide box is valid for — it must still get it."""
    run = await _create_run(db, auth, country_code="US", state_code="AL")
    seed = [MapsGridCell(region_name="Alabama", city_name=None, query_text="addiction treatment Alabama")]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient({}),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    assert len(cells) == 1
    alabama_bbox = resolve_geo_anchor(country_code="US", state_code="AL", state_name=None)
    assert cells[0].viewport_bounds == alabama_bbox


@pytest.mark.asyncio
async def test_run_with_unknown_anchor_falls_back_to_text_only_search(db, auth, monkeypatch):
    """No fabricated coordinates for a state/country not in the trusted
    table — cells must carry no viewport_bounds, same as before this fix,
    not an invented box."""
    run = await _create_run(db, auth, country_code="FR")
    seed = [MapsGridCell(region_name="Ile-de-France", city_name="Paris", query_text="rehab Paris")]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient({}),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    assert len(cells) == 1
    assert cells[0].viewport_bounds is None


@pytest.mark.asyncio
async def test_capped_region_wide_cell_subdivides_into_distinct_smaller_boxes_end_to_end(
    db, auth, monkeypatch
):
    """The full chain for the one case a state box is legitimately usable
    (no city_name): seeded state bbox -> capped cell -> viewport quadrant
    children with distinct, smaller boxes -> those exact boxes reach the
    places client on the next processing pass (not silently dropped along
    the way)."""
    run = await _create_run(db, auth, country_code="US", state_code="AL")
    seed = [MapsGridCell(region_name="Alabama", city_name=None, query_text="addiction treatment Alabama")]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )

    places = {
        "addiction treatment Alabama": PlacesSearchOutcome(
            places=[],
            pages_fetched=1,
            raw_results_found=20,
            unique_results_found=20,
            duplicates_found=0,
            next_page_available=True,
            result_cap_reached=True,  # force subdivision
            resume_page_token=None,
        ),
    }

    class _CappedOnFirstQuery(_FakePlacesClient):
        async def search_text_paginated(self, *, query, **kwargs):
            outcome = places.get(query)
            if outcome is not None:
                self.paginated_calls.append(
                    {"query": query, "viewport_bounds": kwargs.get("viewport_bounds")}
                )
                return outcome
            return await super().search_text_paginated(query=query, **kwargs)

    client = _CappedOnFirstQuery({})
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", lambda: client
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    parent = next(c for c in cells if c.parent_cell_id is None)
    children = [c for c in cells if c.parent_cell_id == parent.id]
    quadrant_children = [c for c in children if c.expansion_reason == "viewport_subdivision"]

    # The reorder fix (from the prior session): viewport_subdivision must
    # actually run, not be crowded out by the text-only strategies filling
    # every slot first.
    assert len(quadrant_children) == 4
    boxes = [tuple(sorted(c.viewport_bounds.items())) for c in quadrant_children]
    assert len(set(boxes)) == 4  # four genuinely distinct boxes
    for child in quadrant_children:
        # Every quadrant must be strictly smaller than the parent state box.
        assert child.viewport_bounds["north"] <= parent.viewport_bounds["north"]
        assert child.viewport_bounds["south"] >= parent.viewport_bounds["south"]

    # And those exact (smaller) boxes were what the places client actually
    # received when the children were processed — not lost in the pipeline.
    calls_by_query = [c for c in client.paginated_calls if c["query"] == "addiction treatment Alabama"]
    distinct_bounds_seen = {
        tuple(sorted(c["viewport_bounds"].items())) for c in calls_by_query if c["viewport_bounds"]
    }
    assert set(boxes).issubset(distinct_bounds_seen)
    parent_box = tuple(sorted(parent.viewport_bounds.items()))
    assert parent_box in distinct_bounds_seen
