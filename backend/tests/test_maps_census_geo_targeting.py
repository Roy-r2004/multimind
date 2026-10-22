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

from app.db.models import MapsCensusCell, MapsCensusRun, MapsCensusStatus, MapsPlace
from app.services.scraping.maps_census_service import maps_census_service
from app.services.scraping.maps_country_profile_service import maps_country_profile_service
from app.services.scraping.maps_geo_regions import resolve_geo_anchor
from app.services.scraping.maps_grid_planner import MapsGridCell
from app.services.scraping.maps_places_client import PlaceResult, PlacesSearchOutcome

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


_DEFAULT_STATE_NAMES = {"AL": "Alabama", "MI": "Michigan", "CO": "Colorado", "FL": "Florida"}


async def _create_run(
    db,
    auth,
    *,
    country_code: str,
    state_code: str | None = None,
    state_name: str | None = None,
) -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code=country_code,
        country_name="United States" if country_code == "US" else "France",
        state_code=state_code,
        state_name=state_name or _DEFAULT_STATE_NAMES.get(state_code or "", None),
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


# --- Multi-state / multi-country coverage (Task: "Alabama was only our
# initial test case" — these prove the fix generalizes, not just Alabama). ---


@pytest.mark.parametrize("state_code", ["AL", "MI", "CO", "FL"])
def test_resolve_geo_anchor_covers_every_newly_added_state_with_a_distinct_box(state_code):
    """Each of the four states this session adds coverage for must resolve
    to its own real box — and no two states must ever resolve to the same
    box (the exact class of bug this whole effort started from: a
    statewide box from one place leaking onto another)."""
    boxes = {
        code: resolve_geo_anchor(country_code="US", state_code=code, state_name=None)
        for code in ["AL", "MI", "CO", "FL"]
    }
    for code, box in boxes.items():
        assert box is not None, f"{code} should have a trusted box"
        assert box["north"] > box["south"]
        assert box["east"] > box["west"]

    distinct_boxes = {tuple(sorted(b.items())) for b in boxes.values()}
    assert len(distinct_boxes) == 4, "every state must resolve to its own distinct box"


def test_resolve_geo_anchor_still_unknown_for_unlisted_us_state():
    """A US state not yet in the table (e.g. California) must stay None —
    never fabricated, never borrowed from a neighboring state's box."""
    assert resolve_geo_anchor(country_code="US", state_code="CA", state_name=None) is None


@pytest.mark.parametrize(
    "state_code,state_name",
    [("MI", None), (None, "Michigan"), ("CO", None), (None, "Colorado"), ("FL", None), (None, "Florida")],
)
@pytest.mark.asyncio
async def test_non_alabama_state_city_named_cells_never_get_the_statewide_box(
    db, auth, monkeypatch, state_code, state_name
):
    """The Phenix City-class bug, generalized: a city-named cell in
    Michigan, Colorado, or Florida must never receive that state's
    statewide box either — resolved by state_code or by state_name alone,
    matching how the UI can send either."""
    run = await _create_run(db, auth, country_code="US", state_code=state_code, state_name=state_name)
    seed = [MapsGridCell(region_name="Some County", city_name="Some City", query_text="rehab Some City")]
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
async def test_country_only_us_search_with_no_state_selected_stays_text_only(db, auth, monkeypatch):
    """A US run with no state selected at all must never silently search
    any one state's box, and must never expand to a (nonexistent) real
    country-wide box either — the only honest outcome without a selected
    state is text-only."""
    run = await _create_run(db, auth, country_code="US", state_code=None, state_name=None)
    seed = [MapsGridCell(region_name="Nationwide", city_name=None, query_text="addiction treatment nationwide")]
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
async def test_non_us_country_with_state_selected_stays_text_only(db, auth, monkeypatch):
    """A non-US country with a province/state selected must not pick up
    any US state's box, and must not be treated as if no state were
    selected either — it is simply a country this table has no data for
    yet, so it stays text-only, honestly."""
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="CA",
        country_name="Canada",
        state_code="ON",
        state_name="Ontario",
        status=MapsCensusStatus.QUEUED,
    )
    db.add(run)
    await db.commit()
    seed = [MapsGridCell(region_name="Ontario", city_name="Toronto", query_text="rehab Toronto")]
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
async def test_run_surfaces_geo_anchor_resolution_in_processing_state(db, auth, monkeypatch):
    """Task requirement: clearly surface geo cells vs. text-only — a run
    must record whether it resolved a trusted box, not leave that implicit
    in logs only."""
    resolved_run = await _create_run(db, auth, country_code="US", state_code="CO")
    unresolved_run = await _create_run(db, auth, country_code="FR")
    for run in (resolved_run, unresolved_run):
        seed = [MapsGridCell(region_name="Region", city_name=None, query_text=f"query {run.id}")]
        monkeypatch.setattr(
            "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
        )
        monkeypatch.setattr(
            "app.services.scraping.maps_census_service.create_places_client",
            lambda: _FakePlacesClient({}),
        )
        summary = await maps_census_service.run_census(db, run_id=run.id)
        assert summary.get("error") is None

    await db.refresh(resolved_run)
    await db.refresh(unresolved_run)
    assert (resolved_run.processing_state or {}).get("geo_anchor", {}).get("resolved") is True
    assert (resolved_run.processing_state or {}).get("geo_anchor", {}).get("bounds") is not None
    assert (unresolved_run.processing_state or {}).get("geo_anchor", {}).get("resolved") is False


def test_known_regions_for_country_lists_only_real_coverage():
    from app.services.scraping.maps_geo_regions import known_regions_for_country

    assert known_regions_for_country("US") == ["AL", "CO", "FL", "MI"]
    assert known_regions_for_country("FR") == []
    assert known_regions_for_country(None) == []


# --- Dataset-backed polygon integration: proves polygon_cells() output
# actually reaches persisted MapsCensusCell rows, not just a helper
# function nobody calls. ---


def _write_adm1_cache(tmp_path, iso3: str, features: list[dict]) -> None:
    import json

    path = tmp_path / f"{iso3}_ADM1.json"
    path.write_text(
        json.dumps(
            {
                "iso3": iso3,
                "adm_level": "ADM1",
                "source": "geoBoundaries gbOpen",
                "license": "Public Domain",
                "boundary_id": f"{iso3}-ADM1-TEST",
                "download_url": "https://raw.githubusercontent.com/example/example.geojson",
                "fetched_at": "2026-01-01T00:00:00+00:00",
                "sha256": "0" * 64,
                "geojson": {"type": "FeatureCollection", "features": features},
            }
        ),
        encoding="utf-8",
    )


def _square_feature(name: str, shape_iso: str, minx, miny, maxx, maxy) -> dict:
    return {
        "type": "Feature",
        "properties": {"shapeName": name, "shapeISO": shape_iso, "shapeID": f"{shape_iso}-ID"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]
            ],
        },
    }


@pytest.mark.asyncio
async def test_region_wide_cells_use_real_polygon_sub_boxes_when_cached(
    db, auth, monkeypatch, tmp_path
):
    """When a dataset-backed boundary is cached for this country/state,
    multiple region-wide seed cells must receive DIFFERENT, polygon-
    filtered sub-boxes (not the identical full bounding rectangle each),
    proving polygon_cells() output actually reaches persisted cells."""
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "maps_geo_boundary_cache_dir", str(tmp_path))
    # An L-shaped Alabama stand-in so polygon_cells() genuinely produces
    # more than one sub-box at a 1-degree grid.
    _write_adm1_cache(
        tmp_path,
        "USA",
        [
            _square_feature("Alabama", "US-AL", -88.0, 31.0, -86.0, 35.0),
            _square_feature("Alabama", "US-AL", -88.0, 31.0, -85.0, 32.0),
        ],
    )

    run = await _create_run(db, auth, country_code="US", state_code="AL")
    seed = [
        MapsGridCell(region_name="Alabama", city_name=None, query_text="addiction treatment north"),
        MapsGridCell(region_name="Alabama", city_name=None, query_text="addiction treatment south"),
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
    bounds = [c.viewport_bounds for c in cells]
    assert all(b is not None for b in bounds)
    # The whole point of the polygon integration: NOT every cell gets the
    # identical full bounding rectangle anymore.
    assert bounds[0] != bounds[1]

    await db.refresh(run)
    geo_anchor = (run.processing_state or {}).get("geo_anchor") or {}
    assert geo_anchor.get("polygon_available") is True
    assert geo_anchor.get("source") == "geoBoundaries gbOpen"


@pytest.mark.asyncio
async def test_region_wide_cells_fall_back_to_static_box_without_cache(db, auth, monkeypatch):
    """No cached dataset boundary (the common case today) must behave
    exactly as before this task — every region-wide cell gets the same
    static-table box, not an empty/broken result."""
    run = await _create_run(db, auth, country_code="US", state_code="AL")
    seed = [
        MapsGridCell(region_name="Alabama", city_name=None, query_text="addiction treatment north"),
        MapsGridCell(region_name="Alabama", city_name=None, query_text="addiction treatment south"),
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
    bounds = [c.viewport_bounds for c in cells]
    alabama_bbox = resolve_geo_anchor(country_code="US", state_code="AL", state_name=None)
    assert bounds[0] == alabama_bbox
    assert bounds[1] == alabama_bbox


@pytest.mark.asyncio
async def test_run_surfaces_geo_ai_plan_diagnostics_when_disabled(db, auth, monkeypatch):
    """The AI planner is disabled by default — a run must still record
    that fact explicitly in processing_state, never leave it implicit."""
    run = await _create_run(db, auth, country_code="US", state_code="MI")
    seed = [MapsGridCell(region_name="Michigan", city_name=None, query_text="rehab Michigan")]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient({}),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    await db.refresh(run)
    geo_ai_plan = (run.processing_state or {}).get("geo_ai_plan") or {}
    assert geo_ai_plan.get("used_ai") is False
    assert geo_ai_plan.get("skipped_reason") == "ai_planner_disabled"


@pytest.mark.asyncio
async def test_ai_planner_additional_search_terms_actually_create_cells(db, auth, monkeypatch):
    """Proves the AI planner's suggestions are not diagnostics-only: its
    additional_search_terms must reach real persisted cells as extra query
    text, reusing a region the deterministic grid planner already chose
    (never a new region, never city_name, never coordinates). priority_areas
    (place names) must NOT be used this way — see the production code
    comment for why."""
    from app.core.config import get_settings
    from app.services.scraping import maps_geo_planner_service
    from app.services.scraping.maps_geo_planner_service import (
        MapsGeoPlanningResult,
        MapsGeoPlanningSuggestion,
    )

    settings = get_settings()
    monkeypatch.setattr(settings, "maps_geo_ai_planner_enabled", True)

    async def _fake_plan_geo_strategy(**kwargs):
        return MapsGeoPlanningResult(
            suggestion=MapsGeoPlanningSuggestion(
                priority_areas=["Some Hallucinated City"],
                additional_search_terms=["outpatient detox center", "sober living home"],
            ),
            used_ai=True,
            cached=False,
            model="test-model",
        )

    monkeypatch.setattr(maps_geo_planner_service, "plan_geo_strategy", _fake_plan_geo_strategy)

    run = await _create_run(db, auth, country_code="US", state_code="MI")
    seed = [MapsGridCell(region_name="Michigan", city_name=None, query_text="addiction treatment Michigan")]
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
    query_texts = {c.query_text for c in cells}
    assert "outpatient detox center" in query_texts
    assert "sober living home" in query_texts
    # The hallucinated place name must never become a region/city on any cell.
    assert "Some Hallucinated City" not in {c.region_name for c in cells}
    assert "Some Hallucinated City" not in {c.city_name for c in cells if c.city_name}
    assert all(c.city_name is None for c in cells if c.query_text in query_texts - {"addiction treatment Michigan"})

    await db.refresh(run)
    geo_ai_plan = (run.processing_state or {}).get("geo_ai_plan") or {}
    assert geo_ai_plan.get("used_ai") is True
    assert geo_ai_plan.get("cells_added") == 2


@pytest.mark.asyncio
async def test_geo_boundary_match_distinguishes_all_four_outcomes_end_to_end(
    db, auth, monkeypatch, tmp_path
):
    """Geographic result validation (Task 6): a result's coordinates
    falling inside a state's bounding RECTANGLE is not proof it's inside
    the real boundary. Proves all four honest outcomes are distinguished
    on real persisted MapsPlace rows: inside the verified polygon, outside
    it, no polygon available to check (no cached dataset boundary), and
    no coordinates supplied at all — never silently folded together."""
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "maps_geo_boundary_cache_dir", str(tmp_path))
    _write_adm1_cache(
        tmp_path,
        "USA",
        [_square_feature("Alabama", "US-AL", -88.5, 30.1, -84.9, 35.1)],
    )

    run = await _create_run(db, auth, country_code="US", state_code="AL")
    seed = [MapsGridCell(region_name="Alabama", city_name=None, query_text="addiction treatment Alabama")]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )
    places = {
        "addiction treatment Alabama": [
            PlaceResult(
                google_place_id="inside-1",
                raw_name="Inside Alabama Rehab",
                formatted_address=None,
                latitude=32.5,
                longitude=-86.5,
            ),
            PlaceResult(
                google_place_id="outside-1",
                raw_name="Michigan Rehab (wrong state)",
                formatted_address=None,
                latitude=42.0,
                longitude=-83.0,
            ),
            PlaceResult(
                google_place_id="nocoords-1",
                raw_name="No Coordinates Rehab",
                formatted_address=None,
                latitude=None,
                longitude=None,
            ),
        ]
    }
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient(places),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    rows = (
        await db.execute(select(MapsPlace).where(MapsPlace.run_id == run.id))
    ).scalars().all()
    by_id = {row.google_place_id: row for row in rows}
    assert by_id["inside-1"].geo_boundary_match == "inside_polygon"
    assert by_id["outside-1"].geo_boundary_match == "outside_polygon"
    assert by_id["nocoords-1"].geo_boundary_match == "no_coordinates"


@pytest.mark.asyncio
async def test_geo_boundary_match_is_no_polygon_available_without_cache(db, auth, monkeypatch):
    """Without a cached dataset boundary (the common case today), a result
    WITH coordinates must be recorded as unvalidated, never silently
    treated as confirmed just because a static-table box happened to seed
    the cell."""
    run = await _create_run(db, auth, country_code="US", state_code="AL")
    seed = [MapsGridCell(region_name="Alabama", city_name=None, query_text="addiction treatment Alabama")]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )
    places = {
        "addiction treatment Alabama": [
            PlaceResult(
                google_place_id="p1",
                raw_name="Some Rehab",
                formatted_address=None,
                latitude=32.5,
                longitude=-86.5,
            ),
        ]
    }
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient(places),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    row = (
        await db.execute(select(MapsPlace).where(MapsPlace.run_id == run.id))
    ).scalar_one()
    assert row.geo_boundary_match == "no_polygon_available"


# --- Country-only (ADM0) and non-US-state (Canada/Quebec) dataset-backed
# resolution, proving coverage isn't just US states. ---


@pytest.mark.asyncio
async def test_country_only_resolution_uses_cached_adm0_polygon(db, auth, monkeypatch, tmp_path):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "maps_geo_boundary_cache_dir", str(tmp_path))
    _write_adm1_cache(  # ADM0 cache, reusing the same writer with a different suffix
        tmp_path, "FRA", [_square_feature("France", "FR", -5.5, 41.0, 9.9, 51.3)]
    )
    import shutil

    shutil.move(str(tmp_path / "FRA_ADM1.json"), str(tmp_path / "FRA_ADM0.json"))

    run = await _create_run(db, auth, country_code="FR")
    seed = [MapsGridCell(region_name="France", city_name=None, query_text="addiction treatment France")]
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
    # With only one region-wide seed cell, it gets the first real
    # polygon-filtered sub-box (at the configured grid granularity) rather
    # than the full country rectangle — it must still lie within it.
    bounds = cells[0].viewport_bounds
    assert bounds is not None
    assert -5.5 <= bounds["west"] <= 9.9
    assert -5.5 <= bounds["east"] <= 9.9
    assert 41.0 <= bounds["south"] <= 51.3
    assert 41.0 <= bounds["north"] <= 51.3

    await db.refresh(run)
    geo_anchor = (run.processing_state or {}).get("geo_anchor") or {}
    assert geo_anchor.get("polygon_available") is True
    assert geo_anchor.get("bounds") == pytest.approx(
        {"west": -5.5, "south": 41.0, "east": 9.9, "north": 51.3}
    )


@pytest.mark.asyncio
async def test_canada_quebec_resolves_via_dataset_not_just_us_states(db, auth, monkeypatch, tmp_path):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "maps_geo_boundary_cache_dir", str(tmp_path))
    _write_adm1_cache(
        tmp_path,
        "CAN",
        [_square_feature("Quebec", "CA-QC", -79.8, 44.9, -57.1, 62.6)],
    )

    run = await _create_run(db, auth, country_code="CA", state_name="Quebec")
    seed = [MapsGridCell(region_name="Quebec", city_name=None, query_text="addiction treatment Quebec")]
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
    assert cells[0].viewport_bounds is not None

    await db.refresh(run)
    geo_anchor = (run.processing_state or {}).get("geo_anchor") or {}
    assert geo_anchor.get("polygon_available") is True
    assert geo_anchor.get("source") == "geoBoundaries gbOpen"
