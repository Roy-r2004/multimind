"""Integration tests for the standalone Maps census orchestration."""

from __future__ import annotations

import json
from asyncio import gather as asyncio_gather
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.db.models import (
    MapsCensusCell,
    MapsCensusCellStatus,
    MapsCensusRegion,
    MapsCensusRun,
    MapsCensusStatus,
    MapsContactStatus,
    MapsLifecycleStatus,
    MapsPlace,
)
from app.services.scraping.maps_census_service import (
    MapsRelevanceDecision,
    MapsWebsitePlan,
    _accepted_direct_llm_website_url,
    _accepted_llm_website_url,
    _contact_status_for_place,
    _lifecycle_from_classification,
    _maps_website_search_queries,
    _match_official_website_by_address,
    _normalize_website_payload,
    _is_facebook_url,
    auto_refresh_maps_census_websites,
    has_street_address,
    is_generic_facility_name,
    has_contact_channel,
    maps_census_service,
)
from app.services.scraping.maps_country_profile_service import maps_country_profile_service
from app.services.scraping.maps_grid_planner import MapsGridCell
from app.services.scraping.maps_places_client import PlaceResult, PlacesSearchOutcome
from app.services.scraping.search_providers.base import SearchProviderResult


@pytest.fixture(autouse=True)
def _stub_country_profile_stage(monkeypatch):
    """Autouse: every ``run_census`` test in this module must never reach the
    real country-profile LLM stage (live Sonar) — ``run_census`` calls
    ``maps_country_profile_service.build_profile_for_run`` unconditionally
    before grid planning. Dedicated tests that exercise the real profile
    service with mocked providers live in ``test_maps_country_profile.py``.
    """

    async def _noop_build_profile(_db, *, run_id: str):
        del run_id
        return None

    monkeypatch.setattr(maps_country_profile_service, "build_profile_for_run", _noop_build_profile)


class _FakeGridPlanner:
    def __init__(self, cells: list[MapsGridCell]) -> None:
        self._cells = cells

    async def plan(self, **_kwargs) -> list[MapsGridCell]:
        return self._cells


class _CountingGridPlanner:
    """Returns one batch per call (from ``batches``, by call index); records every
    call's kwargs so tests can assert how many times/with what focus the adaptive
    loop asked the planner for more cells. Calls beyond ``len(batches)`` get an
    empty batch, mirroring a planner that has nothing left to add.
    """

    def __init__(self, batches: list[list[MapsGridCell]]) -> None:
        self._batches = batches
        self.calls: list[dict] = []

    async def plan(self, **kwargs) -> list[MapsGridCell]:
        idx = len(self.calls)
        self.calls.append(kwargs)
        if idx < len(self._batches):
            return self._batches[idx]
        return []


class _FakePlacesClient:
    """Fake Google Places client for orchestration tests: single page, no
    pagination cap, no error — every ``search_text_paginated`` call returns
    every configured result for that query in one page.
    """

    def __init__(
        self,
        by_query: dict[str, list[PlaceResult]],
        *,
        errors_by_query: dict[str, Exception] | None = None,
    ) -> None:
        self._by_query = by_query
        self._errors_by_query = errors_by_query or {}
        self.paginated_calls: list[dict] = []

    def is_configured(self) -> bool:
        return True

    async def search_text(self, *, query: str, region_code: str, max_results: int) -> list[PlaceResult]:
        return self._by_query.get(query, [])

    async def search_text_paginated(
        self,
        *,
        query: str,
        region_code: str,
        page_size: int | None = None,
        max_pages: int | None = None,
        resume_page_token: str | None = None,
        cancel_check=None,
    ) -> PlacesSearchOutcome:
        del region_code, page_size, max_pages, resume_page_token, cancel_check
        self.paginated_calls.append({"query": query})
        error = self._errors_by_query.get(query)
        if error is not None:
            raise error
        places = self._by_query.get(query, [])
        return PlacesSearchOutcome(
            places=list(places),
            pages_fetched=1 if places else 0,
            raw_results_found=len(places),
            unique_results_found=len(places),
            duplicates_found=0,
            next_page_available=False,
            result_cap_reached=False,
            pagination_error=None,
            resume_page_token=None,
        )


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeProvider:
    def __init__(self, decisions_json: str) -> None:
        self._decisions_json = decisions_json

    async def complete(self, **_kwargs) -> _FakeResponse:
        return _FakeResponse(self._decisions_json)


class _FakeProviderRegistry:
    def __init__(self, provider: _FakeProvider) -> None:
        self._provider = provider

    def get_provider(self, _name: str) -> _FakeProvider:
        return self._provider


def _use_serper_website_search(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "maps_census_website_search_mode", "serper")


def _patch_direct_llm_website_finder(monkeypatch, *, url: str, confidence: float = 0.95) -> None:
    async def fake_find_batch(self, *, provider, model_slug, country_code, country_name, batch):
        from app.services.scraping.maps_census_service import MapsWebsiteDecision

        return [
            MapsWebsiteDecision(
                place_id=item["id"],
                url=url,
                reason="known facility",
                confidence=confidence,
            )
            for item in batch
        ]

    async def verify_always(_url: str) -> bool:
        return True

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._find_websites_llm_batch",
        fake_find_batch,
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service._verify_website_reachable",
        verify_always,
    )


async def _create_run(db, auth: AuthContext) -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="BY",
        country_name="Belarus",
        status=MapsCensusStatus.QUEUED,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run


@pytest.mark.asyncio
async def test_run_census_dedupes_places_and_applies_website_validation(db, auth, monkeypatch):
    run = await _create_run(db, auth)

    cells = [
        MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="rehab Minsk"),
        MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="наркология Минск"),
    ]
    # Same google_place_id returned from two different cell queries — must dedupe to one row.
    shared_place = PlaceResult(
        google_place_id="place-shared",
        raw_name="Centre Alpha Rehab",
        formatted_address="1 Main St, Minsk, Belarus",
        place_types=["health"],
        latitude=53.9,
        longitude=27.5667,
        international_phone_number="+375171234567",
        website="https://centre-alpha.by/",
    )
    bad_website_place = PlaceResult(
        google_place_id="place-bad-site",
        raw_name="Regional Health Directory Listing",
        formatted_address="Minsk, Belarus",
        place_types=["health"],
        website="https://directory.example/clinics/regional",
    )
    by_query = {
        "rehab Minsk": [shared_place, bad_website_place],
        "наркология Минск": [shared_place],
    }
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _FakeGridPlanner(cells),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient(by_query),
    )

    decisions_payload = json.dumps(
        {
            "decisions": [
                {"place_id": "will-be-overridden", "is_relevant": True, "reason": "rehab", "confidence": 0.9},
            ]
        }
    )

    async def fake_classify_batch(self, *, provider, model_slug, country_code, country_name, payloads):
        from app.services.scraping.maps_census_service import MapsRelevanceDecision

        decisions = []
        for item in payloads:
            is_rehab = "Directory" not in item["name"]
            decisions.append(
                MapsRelevanceDecision(
                    place_id=item["place_id"],
                    is_relevant=is_rehab,
                    reason="rehab facility" if is_rehab else "directory listing, not a facility",
                        confidence=0.9 if is_rehab else 0.2,
                )
            )
        return decisions

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._classify_batch",
        fake_classify_batch,
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="openai/gpt-4.1"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_provider_registry",
        lambda: _FakeProviderRegistry(_FakeProvider(decisions_payload)),
    )

    from app.services.scraping.maps_keep_drop_service import KeepDropDecision

    async def fake_keep_drop(_session, place, *, country_code, country_name):
        if "Directory" in (place.canonical_name or ""):
            return KeepDropDecision(
                decision="drop", reason="directory listing, not a facility", confidence=0.9
            ), "nano"
        return KeepDropDecision(
            decision="keep", reason="private rehab facility", confidence=0.95
        ), "nano"

    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.classify_place_keep_drop",
        fake_keep_drop,
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    # run_census mutates the row through short-lived sessions of its own; the
    # long-lived test session must be told its cached copy is stale.
    await db.refresh(run)
    refreshed = run
    assert refreshed.status == MapsCensusStatus.COMPLETED
    assert refreshed.cells_total == 2
    assert refreshed.cells_completed == 2
    assert refreshed.places_found == 2  # deduped: shared_place counted once

    places = (
        await db.execute(select(MapsPlace).where(MapsPlace.run_id == run.id))
    ).scalars().all()
    assert len(places) == 2
    by_google_id = {p.google_place_id: p for p in places}

    rehab = by_google_id["place-shared"]
    assert rehab.is_relevant is True
    assert rehab.official_website == "https://centre-alpha.by/"

    directory = by_google_id["place-bad-site"]
    assert directory.is_relevant is False
    # Rejected as a directory listing by the shared strict validator — never trusted as official.
    assert directory.official_website is None

    assert refreshed.places_classified_relevant == 1
    assert refreshed.places_with_website == 1


@pytest.mark.asyncio
async def test_run_census_falls_back_to_llm_when_places_has_no_website(db, auth, monkeypatch):
    run = await _create_run(db, auth)

    cells = [MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="rehab Minsk")]
    # Google Places found the facility but returned no website for it at all.
    no_website_place = PlaceResult(
        google_place_id="place-no-site",
        raw_name="Centre Gamma Rehab",
        formatted_address="5 Hope St, Minsk, Belarus",
        place_types=["health"],
        latitude=53.9,
        longitude=27.5667,
        website=None,
    )
    by_query = {"rehab Minsk": [no_website_place]}
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _FakeGridPlanner(cells),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient(by_query),
    )

    async def fake_classify_batch(self, *, provider, model_slug, country_code, country_name, payloads):
        from app.services.scraping.maps_census_service import MapsRelevanceDecision

        return [
            MapsRelevanceDecision(
                place_id=item["place_id"], is_relevant=True, reason="rehab facility", confidence=0.9
            )
            for item in payloads
        ]

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._classify_batch",
        fake_classify_batch,
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="openai/gpt-4.1"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_provider_registry",
        lambda: _FakeProviderRegistry(_FakeProvider(json.dumps({"decisions": []}))),
    )

    from app.services.scraping.maps_keep_drop_service import KeepDropDecision

    async def fake_keep_drop(_session, place, *, country_code, country_name):
        return KeepDropDecision(
            decision="keep", reason="private rehab facility", confidence=0.95
        ), "nano"

    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.classify_place_keep_drop",
        fake_keep_drop,
    )

    _patch_direct_llm_website_finder(monkeypatch, url="https://centre-gamma.by/")
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="anthropic/claude-sonnet-4"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_provider_registry",
        lambda: _FakeProviderRegistry(_FakeProvider(json.dumps({"decisions": []}))),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    await db.refresh(run)
    assert run.status == MapsCensusStatus.COMPLETED
    assert run.places_with_website == 1

    place = (
        await db.execute(select(MapsPlace).where(MapsPlace.google_place_id == "place-no-site"))
    ).scalar_one()
    assert place.is_relevant is True
    assert place.official_website == "https://centre-gamma.by/"
    assert place.website_source == "llm"


@pytest.mark.asyncio
async def test_run_census_keeps_failed_classification_discovered_and_skips_website_promotion(
    db, auth, monkeypatch
):
    run = await _create_run(db, auth)

    cells = [MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="rehab Minsk")]
    place_with_raw_site = PlaceResult(
        google_place_id="place-classification-failed",
        raw_name="Centre Unknown",
        formatted_address="9 Hope St, Minsk, Belarus",
        place_types=["health"],
        latitude=53.9,
        longitude=27.5667,
        website="https://centre-unknown.by/",
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _FakeGridPlanner(cells),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient({"rehab Minsk": [place_with_raw_site]}),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="openai/gpt-4.1"),
    )

    class _ExplodingProvider:
        async def complete(self, **_kwargs):
            raise RuntimeError("classifier offline")

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_provider_registry",
        lambda: _FakeProviderRegistry(_ExplodingProvider()),
    )
    # Keep/drop gate: both nano and Sonar offline → uncertain defaults to drop.
    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.get_provider_registry",
        lambda: _FakeProviderRegistry(_ExplodingProvider()),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    await db.refresh(run)
    assert run.status == MapsCensusStatus.COMPLETED
    assert run.places_classified_relevant == 0
    assert run.places_with_website == 0

    place = (
        await db.execute(
            select(MapsPlace).where(MapsPlace.google_place_id == "place-classification-failed")
        )
    ).scalar_one()
    assert place.is_relevant is False
    assert place.keep_drop_decision == "drop"
    assert place.keep_drop_reason == "classifier_unavailable"
    assert place.lifecycle_status == MapsLifecycleStatus.UNRELATED.value
    assert place.classification_confidence == 0.0
    assert place.official_website is None


@pytest.mark.asyncio
async def test_run_census_passes_stored_country_profile_into_grid_planner(db, auth, monkeypatch):
    """Assertion #4: run_census must pass the profile stored on the run (by the
    Task 1 profile stage) into the grid planner's ``country_profile`` kwarg.
    """
    run = await _create_run(db, auth)
    captured_kwargs: dict = {}
    stored_profile = {
        "query_families": ["generic"],
        "provider_terms": {"generic": ["rehab"]},
    }

    class _CapturingGridPlanner:
        async def plan(self, **kwargs):
            captured_kwargs.update(kwargs)
            return [MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="rehab Minsk")]

    async def fake_build_profile(session, *, run_id: str):
        run_row = await session.get(MapsCensusRun, run_id)
        run_row.country_profile = stored_profile
        await session.commit()
        return run_row.country_profile

    monkeypatch.setattr(maps_country_profile_service, "build_profile_for_run", fake_build_profile)
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _CapturingGridPlanner(),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient({}),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None
    assert captured_kwargs.get("country_profile") == stored_profile


@pytest.mark.asyncio
async def test_run_census_persists_query_family_and_language_on_cells(db, auth, monkeypatch):
    run = await _create_run(db, auth)
    cells = [
        MapsGridCell(
            region_name="Minsk Region",
            city_name="Minsk",
            query_text="zxq-outpatient-clinic Minsk",
            query_family="outpatient",
            query_language="en",
        )
    ]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _FakeGridPlanner(cells),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient({}),
    )

    await maps_census_service.run_census(db, run_id=run.id)

    from app.db.models import MapsCensusCell

    stored_cell = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalar_one()
    assert stored_cell.query_family == "outpatient"
    assert stored_cell.query_language == "en"


@pytest.mark.asyncio
async def test_run_census_fails_when_places_api_key_missing(db, auth, monkeypatch):
    run = await _create_run(db, auth)
    cells = [MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="rehab Minsk")]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _FakeGridPlanner(cells),
    )

    class _UnconfiguredClient:
        def is_configured(self) -> bool:
            return False

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _UnconfiguredClient(),
    )

    await maps_census_service.run_census(db, run_id=run.id)

    await db.refresh(run)
    refreshed = run
    assert refreshed.status == MapsCensusStatus.FAILED
    assert "API key" in (refreshed.error_message or "")


@pytest.mark.asyncio
async def test_run_census_fails_gracefully_when_grid_planning_returns_nothing(db, auth, monkeypatch):
    run = await _create_run(db, auth)
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _FakeGridPlanner([]),
    )

    await maps_census_service.run_census(db, run_id=run.id)

    await db.refresh(run)
    refreshed = run
    assert refreshed.status == MapsCensusStatus.FAILED
    assert refreshed.error_message


@pytest.mark.asyncio
async def test_run_census_skips_replan_when_discovery_cells_already_complete(db, auth, monkeypatch):
    """Stale watchdog must not re-enter seed grid planning on a finished run."""
    run = await _create_run(db, auth)
    run.status = MapsCensusStatus.RUNNING
    run.heartbeat_at = None
    db.add(
        MapsCensusCell(
            run_id=run.id,
            region_name="Minsk Region",
            city_name="Minsk",
            query_text="rehab Minsk",
            status=MapsCensusCellStatus.COMPLETED,
        )
    )
    await db.commit()

    planned = {"called": False}

    class _BoomPlanner:
        async def plan(self, **_kwargs):
            planned["called"] = True
            raise AssertionError("seed planner must not run")

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _BoomPlanner(),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("skipped") == 1
    assert planned["called"] is False
    await db.refresh(run)
    assert run.status == MapsCensusStatus.RUNNING
    assert run.error_message is None


async def _fake_classify_all_relevant(self, *, provider, model_slug, country_code, country_name, payloads):
    return [
        MapsRelevanceDecision(
            place_id=item["place_id"], is_relevant=True, reason="rehab facility", confidence=0.9
        )
        for item in payloads
    ]


def _stub_classification(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._classify_batch",
        _fake_classify_all_relevant,
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="openai/gpt-4.1"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_provider_registry",
        lambda: _FakeProviderRegistry(_FakeProvider(json.dumps({"decisions": []}))),
    )


# ---------------------------------------------------------------------------
# Phase 2 Task 4: adaptive run_census loop + region metrics + funnel snapshot
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_census_creates_regions_linked_to_cells(db, auth, monkeypatch):
    run = await _create_run(db, auth)
    seed = [
        MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="rehab Minsk"),
        MapsGridCell(region_name="Brest Region", city_name="Brest", query_text="rehab Brest"),
    ]
    planner = _CountingGridPlanner([seed])
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", planner
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient({}),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    regions = (
        await db.execute(select(MapsCensusRegion).where(MapsCensusRegion.run_id == run.id))
    ).scalars().all()
    assert {r.region_name for r in regions} == {"Minsk Region", "Brest Region"}

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    assert len(cells) == 2
    region_ids_by_name = {r.region_name: r.id for r in regions}
    for cell in cells:
        assert cell.region_id is not None
        assert cell.region_id == region_ids_by_name[cell.region_name]


@pytest.mark.asyncio
async def test_run_census_stops_expanding_unproductive_region(db, auth, monkeypatch):
    """A region whose seed cells fill a full saturation window with zero new
    places must be marked saturated and must not trigger any expansion call."""
    monkeypatch.setattr(get_settings(), "maps_census_saturation_window", 3)
    run = await _create_run(db, auth)
    seed = [
        MapsGridCell(region_name="Dead Region", city_name="Nowhere", query_text=f"empty query {i}")
        for i in range(3)
    ]
    planner = _CountingGridPlanner([seed])
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", planner
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient({}),  # every query returns zero places
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None
    assert len(planner.calls) == 1  # never asked for more "Dead Region" cells

    region = (
        await db.execute(
            select(MapsCensusRegion).where(MapsCensusRegion.run_id == run.id)
        )
    ).scalar_one()
    assert region.saturation_status == "saturated"

    await db.refresh(run)
    # The only region in the run just went terminal (saturated), so the loop
    # stops on the "all regions terminal" branch without ever calling the
    # planner again for more cells.
    assert run.saturation_summary["stopped_reason"] == "all_regions_terminal"
    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    assert len(cells) == 3  # no extra cells beyond the seed batch


@pytest.mark.asyncio
async def test_run_census_saturates_region_with_many_unrelated_uniques_but_no_plausible(
    db, auth, monkeypatch
):
    """Phase 2 gap #5: ``new_plausible_places`` must reflect real classified
    plausibility, not raw discovery. A region that keeps finding brand-new
    (high ``new_unique_places``) but consistently *unrelated* places — e.g.
    random businesses that share search terms with rehab facilities — must
    still be recognized as saturated because its *plausible* candidate count
    never grows, even though its configured unique-place threshold is deliberately
    set above the raw discovery count for this scenario.
    """
    monkeypatch.setattr(get_settings(), "maps_census_saturation_window", 3)
    monkeypatch.setattr(get_settings(), "maps_census_min_new_unique_for_expansion", 1000)
    monkeypatch.setattr(get_settings(), "maps_census_min_new_plausible_for_expansion", 1)

    run = await _create_run(db, auth)
    seed = [
        MapsGridCell(region_name="Ghost Region", city_name="Nowhere", query_text=f"ghost query {i}")
        for i in range(3)
    ]
    planner = _CountingGridPlanner([seed])
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", planner
    )

    # Each of the 3 seed cells discovers 5 brand-new, distinct places (15
    # unique total across the window) — none of them rehab-related.
    by_query: dict[str, list[PlaceResult]] = {}
    for i, cell in enumerate(seed):
        by_query[cell.query_text] = [
            PlaceResult(
                google_place_id=f"ghost-{i}-{j}",
                raw_name=f"Random Business {i}-{j}",
                formatted_address="1 Nowhere St, Nowhere",
            )
            for j in range(5)
        ]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient(by_query),
    )

    async def fake_classify_batch(self, *, provider, model_slug, country_code, country_name, payloads):
        from app.services.scraping.maps_census_service import MapsRelevanceDecision

        return [
            MapsRelevanceDecision(
                place_id=item["place_id"],
                is_relevant=False,
                reason="unrelated business, not a rehab facility",
                confidence=0.1,
            )
            for item in payloads
        ]

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._classify_batch",
        fake_classify_batch,
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="openai/gpt-4.1"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_provider_registry",
        lambda: _FakeProviderRegistry(_FakeProvider(json.dumps({"decisions": []}))),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None
    # Never asked the planner for more "Ghost Region" cells once saturated.
    assert len(planner.calls) == 1

    region = (
        await db.execute(
            select(MapsCensusRegion).where(MapsCensusRegion.run_id == run.id)
        )
    ).scalar_one()

    # High raw discovery, zero real plausibility — proves the two metrics are
    # decoupled (new_plausible_places is NOT just a copy of new_unique_places).
    assert region.unique_places_found == 15
    assert region.plausible_providers_found == 0
    assert region.unrelated_found == 15
    assert region.eligible_candidates_found == 0
    assert region.review_candidates_found == 0
    assert region.individuals_found == 0
    assert region.confirmed_public_found == 0

    # Despite the high absolute unique-place count, the region still
    # saturates because plausible discovery never grows.
    assert region.saturation_status == "saturated"

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    assert sum(c.new_unique_places for c in cells) == 15
    assert sum(c.new_plausible_places for c in cells) == 0


@pytest.mark.asyncio
async def test_run_census_respects_campaign_cell_ceiling(db, auth, monkeypatch):
    """A low campaign ceiling must cap total persisted cells even when the
    planner is willing to return more cells than it was asked for."""
    monkeypatch.setattr(get_settings(), "maps_census_max_cells_per_campaign", 5)
    run = await _create_run(db, auth)
    generous_seed = [
        MapsGridCell(region_name="Big Region", city_name="City", query_text=f"query {i}")
        for i in range(8)  # more than the ceiling, ignoring the max_cells hint
    ]
    planner = _CountingGridPlanner([generous_seed])
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", planner
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
    assert len(cells) <= 5

    await db.refresh(run)
    assert run.saturation_summary["stopped_reason"] == "campaign_capped"
    assert run.saturation_summary["campaign_cells_used"] <= 5
    assert run.saturation_summary["max_cells_per_campaign"] == 5


@pytest.mark.asyncio
async def test_run_census_persists_funnel_and_saturation_snapshots(db, auth, monkeypatch):
    run = await _create_run(db, auth)
    place = PlaceResult(
        google_place_id="place-snapshot",
        raw_name="Centre Snapshot Rehab",
        formatted_address="1 Main St, Minsk, Belarus",
        place_types=["health"],
        website="https://centre-snapshot.by/",
    )
    seed = [MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="rehab Minsk")]
    planner = _CountingGridPlanner([seed])
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", planner
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient({"rehab Minsk": [place]}),
    )
    _stub_classification(monkeypatch)

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    await db.refresh(run)
    assert run.status == MapsCensusStatus.COMPLETED
    assert run.funnel_metrics is not None
    for key in (
        "cells_planned",
        "cells_completed",
        "cell_failures",
        "places_found",
        "places_classified_relevant",
        "country_profile_status",
    ):
        assert key in run.funnel_metrics

    assert run.saturation_summary is not None
    for key in ("campaign_cells_used", "max_cells_per_campaign", "stopped_reason", "regions"):
        assert key in run.saturation_summary
    assert len(run.saturation_summary["regions"]) == 1
    region_snapshot = run.saturation_summary["regions"][0]
    assert region_snapshot["region_name"] == "Minsk Region"
    for key in (
        "saturation_status",
        "cells_completed",
        "unique_places_found",
        "new_unique_places_last_window",
        "new_plausible_providers_last_window",
    ):
        assert key in region_snapshot


@pytest.mark.asyncio
async def test_create_run_queues_and_list_get_places_scoped_to_org(db, auth, monkeypatch):
    captured: dict[str, str] = {}

    async def fake_enqueue(self, run_id: str) -> None:
        captured["run_id"] = run_id

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._enqueue", fake_enqueue
    )

    detail = await maps_census_service.create_run(db, auth, "by")
    assert detail.country_code == "BY"
    assert detail.country_name == "Belarus"
    assert detail.status == "queued"
    assert captured["run_id"] == detail.id

    runs = await maps_census_service.list_runs(db, auth)
    assert any(r.id == detail.id for r in runs)

    # Seed a couple of places directly to exercise list_places filters.
    db.add(
        MapsPlace(
            run_id=detail.id,
            google_place_id="p1",
            raw_name="Relevant Clinic",
            canonical_name="Relevant Clinic",
            is_relevant=True,
            official_website="https://relevant.example/",
        )
    )
    db.add(
        MapsPlace(
            run_id=detail.id,
            google_place_id="p2",
            raw_name="Irrelevant Hotel",
            canonical_name="Irrelevant Hotel",
            is_relevant=False,
        )
    )
    await db.commit()

    all_places = await maps_census_service.list_places(db, auth, detail.id)
    assert len(all_places) == 2

    relevant_only = await maps_census_service.list_places(db, auth, detail.id, relevant_only=True)
    assert len(relevant_only) == 1
    assert relevant_only[0].google_place_id == "p1"

    with_website = await maps_census_service.list_places(
        db, auth, detail.id, with_website_only=True
    )
    assert len(with_website) == 1
    assert with_website[0].google_place_id == "p1"


@pytest.mark.asyncio
async def test_create_run_fetches_hero_image_in_background(db, auth, monkeypatch):
    async def fake_enqueue(self, run_id: str) -> None:
        return None

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._enqueue", fake_enqueue
    )

    class _FakePexelsClient:
        async def search_landscape(self, query: str) -> str:
            assert query == "Belarus landscape"
            return "https://images.pexels.com/belarus-hero.jpg"

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_pexels_client",
        lambda: _FakePexelsClient(),
    )
    bind = db.bind or db.get_bind()
    session_factory = async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", session_factory)
    captured_tasks = _tracking_create_task(monkeypatch, marker="_fetch_hero_image")

    detail = await maps_census_service.create_run(db, auth, "by")
    assert detail.hero_image_url is None  # not resolved yet — background task still pending
    await asyncio_gather(*captured_tasks)

    run = await db.get(MapsCensusRun, detail.id)
    await db.refresh(run)
    assert run.hero_image_url == "https://images.pexels.com/belarus-hero.jpg"


@pytest.mark.asyncio
async def test_create_run_survives_hero_image_fetch_failure(db, auth, monkeypatch):
    async def fake_enqueue(self, run_id: str) -> None:
        return None

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._enqueue", fake_enqueue
    )

    class _FailingPexelsClient:
        async def search_landscape(self, query: str) -> str:
            raise RuntimeError("pexels is down")

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_pexels_client",
        lambda: _FailingPexelsClient(),
    )
    captured_tasks = _tracking_create_task(monkeypatch, marker="_fetch_hero_image")

    detail = await maps_census_service.create_run(db, auth, "by")
    assert detail.status == "queued"
    # The failing background task must not raise into the test/event loop.
    await asyncio_gather(*captured_tasks, return_exceptions=True)

    run = await db.get(MapsCensusRun, detail.id)
    await db.refresh(run)
    assert run.hero_image_url is None


@pytest.mark.asyncio
async def test_run_census_persists_photo_reference(db, auth, monkeypatch):
    run = await _create_run(db, auth)
    cells = [MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="rehab Minsk")]
    place_with_photo = PlaceResult(
        google_place_id="place-with-photo",
        raw_name="Centre Photo Rehab",
        formatted_address="1 Main St, Minsk, Belarus",
        place_types=["health"],
        website="https://centre-photo.by/",
        photo_reference="places/place-with-photo/photos/photo-1",
    )
    by_query = {"rehab Minsk": [place_with_photo]}
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _FakeGridPlanner(cells),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient(by_query),
    )

    async def fake_classify_batch(self, *, provider, model_slug, country_code, country_name, payloads):
        from app.services.scraping.maps_census_service import MapsRelevanceDecision

        return [
            MapsRelevanceDecision(
                place_id=item["place_id"], is_relevant=True, reason="rehab facility", confidence=0.9
            )
            for item in payloads
        ]

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._classify_batch",
        fake_classify_batch,
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="openai/gpt-4.1"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_provider_registry",
        lambda: _FakeProviderRegistry(_FakeProvider(json.dumps({"decisions": []}))),
    )

    await maps_census_service.run_census(db, run_id=run.id)

    place = (
        await db.execute(select(MapsPlace).where(MapsPlace.google_place_id == "place-with-photo"))
    ).scalar_one()
    assert place.photo_reference == "places/place-with-photo/photos/photo-1"


@pytest.mark.asyncio
async def test_delete_run_removes_run_and_places(db, auth):
    run = await _create_run(db, auth)
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="p1",
            raw_name="Some Clinic",
            canonical_name="Some Clinic",
            is_relevant=True,
        )
    )
    await db.commit()

    await maps_census_service.delete_run(db, auth, run.id)

    assert await db.get(MapsCensusRun, run.id) is None
    remaining_places = (
        await db.execute(select(MapsPlace).where(MapsPlace.run_id == run.id))
    ).scalars().all()
    assert remaining_places == []


@pytest.mark.asyncio
async def test_delete_run_raises_not_found_for_other_org(db, auth):
    from app.core.exceptions import NotFoundError

    run = await _create_run(db, auth)
    other_auth = AuthContext(
        user=auth.user, org_id="00000000-0000-0000-0000-000000000000", role=auth.role
    )
    with pytest.raises(NotFoundError):
        await maps_census_service.delete_run(db, other_auth, run.id)


@pytest.mark.asyncio
async def test_request_website_refresh_rejects_non_completed_run(db, auth):
    from app.core.exceptions import ValidationError

    run = await _create_run(db, auth)  # left in QUEUED status
    with pytest.raises(ValidationError):
        await maps_census_service.request_website_refresh(db, auth, run.id)


@pytest.mark.asyncio
async def test_request_website_refresh_enqueues_and_marks_running(db, auth, monkeypatch):
    run = await _create_run(db, auth)
    run.status = MapsCensusStatus.COMPLETED
    await db.commit()

    captured: dict[str, str] = {}

    async def fake_enqueue_refresh(self, run_id: str) -> None:
        captured["run_id"] = run_id

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._enqueue_refresh",
        fake_enqueue_refresh,
    )

    detail = await maps_census_service.request_website_refresh(db, auth, run.id)
    assert detail.status == "running"
    assert captured["run_id"] == run.id


@pytest.mark.asyncio
async def test_run_website_refresh_processes_more_than_250_places_without_truncation(db, auth, monkeypatch):
    """Phase 2 gap #4: the legacy 250-place cap must no longer silently drop
    places from website backfill — the resumable batch loop should process
    every relevant place missing a website in one call."""
    run = await _create_run(db, auth)
    run.status = MapsCensusStatus.RUNNING
    await db.commit()

    total_places = 300
    for i in range(total_places):
        db.add(
            MapsPlace(
                run_id=run.id,
                google_place_id=f"p-missing-{i:04d}",
                raw_name=f"Rehab Center {i}",
                canonical_name=f"Rehab Center {i}",
                city_name="Minsk",
                is_relevant=True,
            )
        )
    await db.commit()

    _patch_direct_llm_website_finder(monkeypatch, url="https://example-rehab.by/")
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="anthropic/claude-sonnet-4"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_provider_registry",
        lambda: _FakeProviderRegistry(_FakeProvider("{}")),
    )

    summary = await maps_census_service.run_website_refresh(db, run_id=run.id)
    assert summary["places_with_website"] == total_places

    remaining = (
        await db.execute(
            select(MapsPlace).where(
                MapsPlace.run_id == run.id,
                MapsPlace.official_website.is_(None),
            )
        )
    ).scalars().all()
    assert remaining == []

    await db.refresh(run)
    assert run.places_with_website == total_places
    assert run.processing_state is not None
    assert run.processing_state.get("website_search_paused") is False
    assert run.quota_metrics is not None
    assert run.quota_metrics.get("website_lookup_calls", 0) > 0


@pytest.mark.asyncio
async def test_run_website_refresh_backfills_missing_website_and_completes(db, auth, monkeypatch):
    run = await _create_run(db, auth)
    run.status = MapsCensusStatus.RUNNING
    run.cells_total = 1
    run.cells_completed = 1
    run.places_found = 1
    run.places_classified_relevant = 1
    await db.commit()

    place = MapsPlace(
        run_id=run.id,
        google_place_id="p-missing-site",
        raw_name="Centre Delta Rehab",
        canonical_name="Centre Delta Rehab",
        city_name="Minsk",
        is_relevant=True,
    )
    db.add(place)
    await db.commit()

    _patch_direct_llm_website_finder(monkeypatch, url="https://centre-delta.by/")
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="anthropic/claude-sonnet-4"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_provider_registry",
        lambda: _FakeProviderRegistry(_FakeProvider("{}")),
    )

    summary = await maps_census_service.run_website_refresh(db, run_id=run.id)
    assert summary["places_with_website"] == 1

    await db.refresh(run)
    assert run.status == MapsCensusStatus.COMPLETED
    assert run.places_with_website == 1

    await db.refresh(place)
    assert place.official_website == "https://centre-delta.by/"
    assert place.website_source == "llm"


def _tracking_create_task(monkeypatch, *, marker: str):
    """Delegates to the real asyncio.create_task for everything (so SQLAlchemy's own
    internal task scheduling on session close keeps working) but records tasks whose
    coroutine was produced by a function named ``marker``.
    """
    import asyncio

    real_create_task = asyncio.create_task
    captured: list[asyncio.Task] = []

    def wrapper(coro, *args, **kwargs):
        task = real_create_task(coro, *args, **kwargs)
        code = getattr(coro, "cr_code", None)
        if code is not None and code.co_name == marker:
            captured.append(task)
        return task

    monkeypatch.setattr("asyncio.create_task", wrapper)
    return captured


def test_accepted_llm_website_url_requires_candidate_host_and_homepage():
    candidates = [
        SearchProviderResult(
            rank=1,
            url="https://www.gknd.by/kontakty",
            title="Contacts",
            snippet="Minsk",
        ),
        SearchProviderResult(
            rank=2,
            url="https://facebook.com/gknd",
            title="Social",
            snippet="",
        ),
    ]
    assert (
        _accepted_llm_website_url("https://www.gknd.by/kontakty", candidates=candidates)
        == "https://www.gknd.by/"
    )
    assert _accepted_llm_website_url("https://evil.example/", candidates=candidates) is None
    assert _accepted_llm_website_url("https://facebook.com/gknd", candidates=candidates) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://facebook.com/ALT.Association/",
        "https://www.facebook.com/profile.php?id=123",
        "https://web.facebook.com/clinic/",
        "https://m.facebook.com/clinic/",
    ],
)
def test_places_facebook_urls_are_accepted_as_social_fallbacks(url):
    assert _is_facebook_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        None,
        "not-a-url",
        "https://facebook.example/clinic",
        "https://evil.example/?next=facebook.com/clinic",
        "https://instagram.com/clinic",
        "https://directory.example/clinic",
        "https://www.facebook.com/groups/algeria-health",
        "https://www.facebook.com/login/",
        "https://www.facebook.com/",
    ],
)
def test_only_real_facebook_hosts_are_places_social_fallbacks(url):
    assert _is_facebook_url(url) is False


@pytest.mark.asyncio
async def test_llm_facebook_page_accepted_when_page_name_identifies_facility():
    accepted = await _accepted_direct_llm_website_url(
        "https://www.facebook.com/EhsFernaneOuedAissi/",
        confidence=0.9,
        page_name="Ehs fernane oued aissi",
        facility_name="Psychiatric Hospital Fernane Hanafi",
    )
    assert accepted == "https://www.facebook.com/EhsFernaneOuedAissi/"


@pytest.mark.asyncio
async def test_llm_facebook_page_accepted_via_vanity_handle_without_page_name():
    accepted = await _accepted_direct_llm_website_url(
        "https://www.facebook.com/CliniqueLilasAlger/",
        confidence=0.9,
        page_name="",
        facility_name="Clinique Psychiatrique Lilas",
    )
    assert accepted == "https://www.facebook.com/CliniqueLilasAlger/"


@pytest.mark.asyncio
async def test_llm_facebook_page_rejected_when_it_belongs_to_another_clinic():
    accepted = await _accepted_direct_llm_website_url(
        "https://www.facebook.com/cgsahydra/",
        confidence=0.95,
        page_name="Clinique CGSA Hydra",
        facility_name="مركز معالجة الادمان - يسر.polyclinic",
    )
    assert accepted is None


@pytest.mark.asyncio
async def test_llm_opaque_facebook_profile_rejected_without_identity_proof():
    accepted = await _accepted_direct_llm_website_url(
        "https://www.facebook.com/profile.php?id=61559475466892",
        confidence=0.95,
        page_name="",
        facility_name="Dr. Fekar psychiatre et addictologue",
    )
    assert accepted is None


@pytest.mark.asyncio
async def test_places_facebook_page_is_used_only_when_official_site_is_missing(db, auth):
    run = await _create_run(db, auth)
    fallback = MapsPlace(
        run_id=run.id,
        google_place_id="place-facebook-only",
        raw_name="ALT Association",
        canonical_name="ALT Association",
        formatted_address="60 Hai Essabah, Oran",
        city_name="Oran",
        is_relevant=True,
        raw_website="https://web.facebook.com/ALT.Association/",
    )
    official = MapsPlace(
        run_id=run.id,
        google_place_id="place-with-domain",
        raw_name="Clinic With Domain",
        canonical_name="Clinic With Domain",
        formatted_address="1 Main Street, Oran",
        city_name="Oran",
        is_relevant=True,
        raw_website="https://facebook.com/clinic/",
        official_website="https://clinic.dz/",
        website_source="llm",
    )
    db.add_all([fallback, official])
    await db.commit()

    applied = await maps_census_service._apply_places_social_fallbacks(
        maps_census_service._session_factory(db), run_id=run.id
    )

    assert applied == 1
    await db.refresh(fallback)
    await db.refresh(official)
    assert fallback.official_website == "https://web.facebook.com/ALT.Association/"
    assert fallback.website_source == "places_social"
    assert official.official_website == "https://clinic.dz/"
    assert official.website_source == "llm"


@pytest.mark.asyncio
async def test_accepted_direct_llm_website_url_requires_confidence_and_rejects_social(
    monkeypatch,
):
    async def verify_ok(_url: str) -> bool:
        return True

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service._verify_website_reachable",
        verify_ok,
    )
    assert (
        await _accepted_direct_llm_website_url(
            "https://rehab-center-alger.com/",
            confidence=0.95,
        )
        == "https://rehab-center-alger.com/"
    )
    assert (
        await _accepted_direct_llm_website_url(
            "https://facebook.com/clinic",
            confidence=0.95,
        )
        is None
    )
    assert (
        await _accepted_direct_llm_website_url(
            "https://rehab-center-alger.com/",
            confidence=0.5,
        )
        is None
    )


def test_normalize_website_payload_accepts_url_aliases():
    payload = _normalize_website_payload(
        [{"id": "p1", "website": "https://gknd.by/", "confidence": 0.8}]
    )
    assert payload["decisions"][0]["place_id"] == "p1"
    assert payload["decisions"][0]["url"] == "https://gknd.by/"


def test_normalize_website_payload_truncates_long_sonar_reasons():
    long_reason = "x" * 500
    payload = _normalize_website_payload(
        [{"place_id": "p1", "url": None, "reason": long_reason, "confidence": 0.5}]
    )
    assert len(payload["decisions"][0]["reason"]) <= 2000
    MapsWebsitePlan.model_validate(payload)

    payload = _normalize_website_payload(
        [{"place_id": "p1", "url": None, "reason": "y" * 2500, "confidence": 0.5}]
    )
    assert len(payload["decisions"][0]["reason"]) == 2000
    MapsWebsitePlan.model_validate(payload)


@pytest.mark.asyncio
async def test_drop_untrusted_websites_clears_blocklisted_directory_urls(db, auth):
    run = await _create_run(db, auth)
    run.status = MapsCensusStatus.COMPLETED
    await db.commit()

    directory = MapsPlace(
        run_id=run.id,
        google_place_id="p-directory",
        raw_name="Mogilovskaya Psikhiatricheskaya Bol'nitsa",
        canonical_name="Mogilovskaya Psikhiatricheskaya Bol'nitsa",
        city_name="Mogilev",
        is_relevant=True,
        official_website="https://www.yoys.by/",
        website_source="search",
    )
    genuine = MapsPlace(
        run_id=run.id,
        google_place_id="p-genuine",
        raw_name="Centre Alpha Rehab",
        canonical_name="Centre Alpha Rehab",
        city_name="Minsk",
        is_relevant=True,
        official_website="https://centre-alpha.by/",
        website_source="search",
    )
    db.add_all([directory, genuine])
    await db.commit()

    session_factory = maps_census_service._session_factory(db)
    dropped = await maps_census_service._drop_untrusted_websites(session_factory, run_id=run.id)
    assert dropped == 1

    await db.refresh(directory)
    await db.refresh(genuine)
    assert directory.official_website is None
    assert directory.website_source is None
    assert genuine.official_website == "https://centre-alpha.by/"


@pytest.mark.asyncio
async def test_propagate_shared_website_across_same_name_locations(db, auth):
    run = await _create_run(db, auth)
    run.status = MapsCensusStatus.COMPLETED
    await db.commit()

    with_site = MapsPlace(
        run_id=run.id,
        google_place_id="p-branch-1",
        raw_name="Centre Alpha Rehab",
        canonical_name="Centre Alpha Rehab",
        city_name="Minsk",
        is_relevant=True,
        official_website="https://centre-alpha.by/",
        website_source="search",
    )
    without_site = MapsPlace(
        run_id=run.id,
        google_place_id="p-branch-2",
        raw_name="Centre Alpha Rehab",
        canonical_name="Centre Alpha Rehab",
        city_name="Brest",
        is_relevant=True,
    )
    different_name = MapsPlace(
        run_id=run.id,
        google_place_id="p-other",
        raw_name="Other Clinic",
        canonical_name="Other Clinic",
        city_name="Minsk",
        is_relevant=True,
    )
    db.add_all([with_site, without_site, different_name])
    await db.commit()

    session_factory = maps_census_service._session_factory(db)
    await maps_census_service._propagate_shared_websites(session_factory, run_id=run.id)

    await db.refresh(without_site)
    await db.refresh(different_name)
    assert without_site.official_website == "https://centre-alpha.by/"
    assert without_site.website_source == "search"
    assert different_name.official_website is None


@pytest.mark.asyncio
async def test_propagate_does_not_overwrite_conflicting_websites(db, auth):
    run = await _create_run(db, auth)
    run.status = MapsCensusStatus.COMPLETED
    await db.commit()

    a = MapsPlace(
        run_id=run.id,
        google_place_id="p-a",
        raw_name="Dispanser Psihonevrologicheskii",
        canonical_name="Dispanser Psihonevrologicheskii",
        city_name="Lida",
        is_relevant=True,
        official_website="https://lida.example/",
        website_source="search",
    )
    b = MapsPlace(
        run_id=run.id,
        google_place_id="p-b",
        raw_name="Dispanser Psihonevrologicheskii",
        canonical_name="Dispanser Psihonevrologicheskii",
        city_name="Mozyr",
        is_relevant=True,
        official_website="https://mozyr.example/",
        website_source="search",
    )
    c = MapsPlace(
        run_id=run.id,
        google_place_id="p-c",
        raw_name="Dispanser Psihonevrologicheskii",
        canonical_name="Dispanser Psihonevrologicheskii",
        city_name="Polotsk",
        is_relevant=True,
    )
    db.add_all([a, b, c])
    await db.commit()

    session_factory = maps_census_service._session_factory(db)
    await maps_census_service._propagate_shared_websites(session_factory, run_id=run.id)

    await db.refresh(c)
    assert c.official_website is None


@pytest.mark.asyncio
async def test_run_website_refresh_uses_llm_when_rule_matchers_fail(db, auth, monkeypatch):
    _use_serper_website_search(monkeypatch)
    run = await _create_run(db, auth)
    run.status = MapsCensusStatus.COMPLETED
    run.cells_total = 1
    run.cells_completed = 1
    run.places_found = 1
    run.places_classified_relevant = 1
    await db.commit()

    place = MapsPlace(
        run_id=run.id,
        google_place_id="p-llm",
        raw_name="Dispanser Narkologicheskii Klinicheskii Gorodskoi",
        canonical_name="Dispanser Narkologicheskii Klinicheskii Gorodskoi",
        city_name="Minsk",
        formatted_address="улица Гастелло16, Minsk, Minskaja voblasć 220035",
        is_relevant=True,
    )
    db.add(place)
    await db.commit()

    class _FakeSearchProvider:
        name = "fake"

        async def search(self, request) -> list[SearchProviderResult]:
            # Snippet deliberately omits the street so address matching fails —
            # same real-world Serper behaviour we observed for gknd.by.
            return [
                SearchProviderResult(
                    rank=1,
                    url="https://www.gknd.by/",
                    title="Учреждение здравоохранения «Минский городской клинический наркологический центр»",
                    snippet="Городской клинический наркологический диспансер г. Минска",
                )
            ]

    class _FakeLLMProvider:
        async def complete(self, **_kwargs):
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "decisions": [
                            {
                                "place_id": place.id,
                                "url": "https://www.gknd.by/",
                                "reason": "official narcological centre domain",
                                "confidence": 0.95,
                            }
                        ]
                    }
                )
            )

    class _FakeRegistry:
        def get_provider(self, _name):
            return _FakeLLMProvider()

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_search_provider",
        lambda: _FakeSearchProvider(),
    )
    requested_models: list[str] = []

    def fake_get_model(name: str):
        requested_models.append(name)
        return SimpleNamespace(provider="openrouter", provider_model="anthropic/claude-sonnet-4")

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_model",
        fake_get_model,
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_provider_registry",
        lambda: _FakeRegistry(),
    )

    summary = await maps_census_service.run_website_refresh(db, run_id=run.id)
    assert summary["places_with_website"] == 1
    await db.refresh(place)
    assert place.official_website == "https://www.gknd.by/"
    assert place.website_source == "search"
    assert requested_models == ["sonar-pro"]


@pytest.mark.asyncio
async def test_delete_unverified_places_removes_only_relevant_rows_without_websites(db, auth):
    run = await _create_run(db, auth)
    verified = MapsPlace(
        run_id=run.id,
        google_place_id="verified",
        raw_name="Verified Rehab",
        canonical_name="Verified Rehab",
        is_relevant=True,
        official_website="https://verified.example/",
        website_source="search",
    )
    unverified = MapsPlace(
        run_id=run.id,
        google_place_id="unverified",
        raw_name="Unverified Rehab",
        canonical_name="Unverified Rehab",
        is_relevant=True,
    )
    irrelevant = MapsPlace(
        run_id=run.id,
        google_place_id="irrelevant",
        raw_name="Unrelated Hotel",
        canonical_name="Unrelated Hotel",
        is_relevant=False,
    )
    db.add_all([verified, unverified, irrelevant])
    await db.commit()

    session_factory = maps_census_service._session_factory(db)
    deleted = await maps_census_service._delete_unverified_places(session_factory, run_id=run.id)
    assert deleted == 1

    remaining = (
        await db.execute(select(MapsPlace).where(MapsPlace.run_id == run.id))
    ).scalars().all()
    assert {place.google_place_id for place in remaining} == {"verified", "irrelevant"}


@pytest.mark.asyncio
async def test_refresh_recounts_after_contact_status_updates(db, auth, monkeypatch):
    """Website refresh preserves uncertain places and refreshes contact completeness."""
    _use_serper_website_search(monkeypatch)
    run = await _create_run(db, auth)
    run.status = MapsCensusStatus.COMPLETED
    run.places_found = 3
    run.places_classified_relevant = 3
    await db.commit()

    verified = MapsPlace(
        run_id=run.id,
        google_place_id="verified-count",
        raw_name="Verified Rehab",
        canonical_name="Verified Rehab",
        is_relevant=True,
        official_website="https://verified.example/",
        website_source="search",
    )
    phone_only = MapsPlace(
        run_id=run.id,
        google_place_id="phone-count",
        raw_name="Phone Only Rehab",
        canonical_name="Phone Only Rehab",
        is_relevant=True,
        international_phone_number="+213 555 00 00",
    )
    no_contact = MapsPlace(
        run_id=run.id,
        google_place_id="unverified-count",
        raw_name="Unverified Rehab",
        canonical_name="Unverified Rehab",
        is_relevant=True,
    )
    db.add_all([verified, phone_only, no_contact])
    await db.commit()

    class _EmptySearchProvider:
        async def search(self, _request):
            return []

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_search_provider",
        lambda: _EmptySearchProvider(),
    )

    await maps_census_service.run_website_refresh(db, run_id=run.id)
    await db.refresh(run)
    await db.refresh(phone_only)
    await db.refresh(no_contact)
    assert run.places_found == 3
    assert run.places_classified_relevant == 3
    assert run.places_with_website == 1
    assert phone_only.contact_status == MapsContactStatus.PHONE_ONLY.value
    assert no_contact.is_relevant is True
    assert no_contact.contact_status == MapsContactStatus.MISSING.value

def test_maps_website_search_queries_fall_back_from_quoted_to_address():
    queries = _maps_website_search_queries(
        name="Dispanser Narkologicheskii Klinicheskii Gorodskoi",
        city="Minsk",
        country_name="Belarus",
        address="улица Гастелло16, Minsk, Minskaja voblasć 220035",
    )
    assert queries[0].startswith('"Dispanser Narkologicheskii Klinicheskii Gorodskoi"')
    assert queries[1].startswith("Dispanser Narkologicheskii Klinicheskii Gorodskoi")
    assert '"' not in queries[1] or queries[1].count('"') == 0
    assert "улица Гастелло 16" in queries[2]
    assert "Minsk" in queries[2]


@pytest.mark.asyncio
async def test_run_website_refresh_retries_unquoted_when_quoted_query_empty(
    db, auth, monkeypatch
):
    """Live Belarus gap: quoting the Latin transliteration returns 0 Serper hits;
    the unquoted retry surfaces the real homepage which address-matching accepts.
    """
    _use_serper_website_search(monkeypatch)
    run = await _create_run(db, auth)
    run.status = MapsCensusStatus.COMPLETED
    run.cells_total = 1
    run.cells_completed = 1
    run.places_found = 1
    run.places_classified_relevant = 1
    await db.commit()

    place = MapsPlace(
        run_id=run.id,
        google_place_id="p-transliterated-empty-quoted",
        raw_name="Dispanser Narkologicheskii Klinicheskii Gorodskoi",
        canonical_name="Dispanser Narkologicheskii Klinicheskii Gorodskoi",
        city_name="Minsk",
        formatted_address="улица Гастелло16, Minsk, Minskaja voblasć 220035",
        is_relevant=True,
    )
    db.add(place)
    await db.commit()

    class _FakeSearchProvider:
        name = "fake"

        async def search(self, request) -> list[SearchProviderResult]:
            if request.query.startswith('"'):
                return []
            return [
                SearchProviderResult(
                    rank=1,
                    url="https://www.gknd.by/",
                    title="Учреждение здравоохранения «Минский городской клинический наркологический центр»",
                    snippet="г. Минск, ул. Гастелло, 16",
                )
            ]

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_search_provider",
        lambda: _FakeSearchProvider(),
    )

    summary = await maps_census_service.run_website_refresh(db, run_id=run.id)
    assert summary["places_with_website"] == 1
    await db.refresh(place)
    assert place.official_website == "https://www.gknd.by/"
    assert place.website_source == "search"


def test_address_fallback_rescues_transliterated_name_mismatch():
    """Regression test for the Belarus gap: Google Places gives a garbled Latin
    transliteration of the name ("Dispanser Narkologicheskii Klinicheskii
    Gorodskoi"), so name-token matching against the real Cyrillic site content
    always fails — but the address ("ulica Gastello 16, Minsk") is partially
    left in Cyrillic by Google and does overlap with the real site's content.
    """
    results = [
        SearchProviderResult(
            rank=1,
            url="https://gknd.by/",
            title="Услуги — Минский городской клинический наркологический центр",
            snippet="Учреждение здравоохранения, г. Минск, ул. Гастелло, 16",
        ),
        SearchProviderResult(
            rank=2,
            url="https://narco-dispanser.relax.by/rubric/addiction/",
            title="Наркологический центр в Минске — цены, отзывы",
            snippet="Каталог клиник Минска, юридический адрес ул. Гастелло, 16",
        ),
    ]
    selected = _match_official_website_by_address(
        address="улица Гастелло 16, Minsk, Minskaja voblasć 220035",
        city="Minsk",
        country_name="Belarus",
        results=results,
    )
    assert selected is not None
    assert selected.url == "https://gknd.by/"


def test_address_fallback_splits_house_number_glued_to_street_name():
    """Regression test: Google's formatted_address for Belarus glues the house
    number directly onto the street name with no separator (e.g.
    "Гастелло16"), which must still split into two tokens so it can match a
    real site's "ул. Гастелло, 16" (this is the exact real-world address for
    the gknd.by facility that the naive tokenizer originally missed).
    """
    results = [
        SearchProviderResult(
            rank=1,
            url="https://gknd.by/",
            title="Услуги — Минский городской клинический наркологический центр",
            snippet="Учреждение здравоохранения, г. Минск, ул. Гастелло, 16",
        )
    ]
    selected = _match_official_website_by_address(
        address="улица Гастелло16, Minsk, Minskaja voblasć 220035",
        city="Minsk",
        country_name="Belarus",
        results=results,
    )
    assert selected is not None
    assert selected.url == "https://gknd.by/"


def test_address_fallback_requires_two_overlapping_tokens():
    """A bare city-name mention in an unrelated result must not count as a match."""
    results = [
        SearchProviderResult(
            rank=1,
            url="https://unrelated-directory.example/clinics",
            title="Minsk clinics directory",
            snippet="A list of clinics in Minsk, Belarus",
        )
    ]
    selected = _match_official_website_by_address(
        address="улица Гастелло 16, Minsk, Minskaja voblasć 220035",
        city="Minsk",
        country_name="Belarus",
        results=results,
    )
    assert selected is None


def test_address_fallback_returns_none_for_short_address():
    selected = _match_official_website_by_address(
        address="Minsk", city="Minsk", country_name="Belarus", results=[]
    )
    assert selected is None


@pytest.mark.parametrize(
    "address",
    [
        "QW2G+35C, Chéraga",
        "8J6J+7RF, Constantine",
        "G4XH+7F9, Batna",
        "C44M+R28, Khenchela",
        "M746+5JJ, Djelfa",
    ],
)
def test_plus_code_only_address_is_not_a_street_address(address):
    assert has_street_address(address) is False


@pytest.mark.parametrize(
    "address",
    [
        "7VQ8+8J9, Rue DES FRÈRES BEN FISSA, Aïn Témouchent",
        "QW2G+35C, N41, Chéraga",
        "12 Rue Ahmed Ouaked, Hydra, Alger",
        "Route de Dar El Beida, Alger",
    ],
)
def test_real_street_survives_alongside_plus_code(address):
    assert has_street_address(address) is True


def test_missing_address_is_not_a_street_address():
    assert has_street_address(None) is False
    assert has_street_address("   ") is False


@pytest.mark.parametrize(
    "name",
    [
        "désintoxication",
        "Centre De Désintoxication",
        "Clinique",
        "Addiction Treatment Center",
        "مركز معالجة الادمان",
        "",
    ],
)
def test_generic_category_names_are_rejected(name):
    assert is_generic_facility_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "Abidat centre anti drogues",
        "Traitement des Addictions (CERTA-TO)",
        "Clinique Psychiatrique Lilas - Hygiène mentale",
        "Clinique CGSA",
        "مركز بوشاوي للادمان",
    ],
)
def test_named_facilities_survive_generic_name_guard(name):
    assert is_generic_facility_name(name) is False


def test_contact_channel_requires_phone_or_website():
    phone_only = SimpleNamespace(
        international_phone_number="+213 555 00 00",
        official_website=None,
        raw_website=None,
    )
    website_only = SimpleNamespace(
        international_phone_number=None,
        official_website="https://clinic.dz/",
        raw_website=None,
    )
    facebook_raw = SimpleNamespace(
        international_phone_number=None,
        official_website=None,
        raw_website="https://web.facebook.com/ALT.Association/",
    )
    neither = SimpleNamespace(
        international_phone_number=None,
        official_website=None,
        raw_website=None,
    )
    blank = SimpleNamespace(
        international_phone_number="  ",
        official_website="",
        raw_website=None,
    )
    assert has_contact_channel(phone_only) is True
    assert has_contact_channel(website_only) is True
    assert has_contact_channel(facebook_raw) is True
    assert has_contact_channel(neither) is False
    assert has_contact_channel(blank) is False


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        (MapsRelevanceDecision(place_id="p1", decision="plausible", confidence=0.82), "plausible"),
        (MapsRelevanceDecision(place_id="p2", decision="unrelated", confidence=0.61), "needs_review"),
        (
            MapsRelevanceDecision(
                place_id="p3",
                is_relevant=False,
                reason="hotel listing, not a facility",
                confidence=0.20,
            ),
            "unrelated",
        ),
        (
            MapsRelevanceDecision(
                place_id="p4",
                is_relevant=False,
                reason="ownership unclear and metadata weak",
                confidence=0.20,
            ),
            "needs_review",
        ),
        (
            MapsRelevanceDecision(
                place_id="p5",
                is_relevant=False,
                reason="classification_failed",
                confidence=0.0,
            ),
            "discovered",
        ),
        (
            MapsRelevanceDecision(
                place_id="p6",
                is_relevant=False,
                reason="missing_decision",
                confidence=0.0,
            ),
            "discovered",
        ),
        (
            MapsRelevanceDecision(
                place_id="p7",
                is_relevant=False,
                reason="university gym educational rehab waste management program",
                confidence=0.20,
            ),
            "unrelated",
        ),
    ],
)
def test_lifecycle_from_classification_uses_confidence_bands(decision, expected):
    assert _lifecycle_from_classification(decision).value == expected


def test_classifier_prompt_keeps_uncertain_outpatient_and_unknown_ownership_candidates():
    prompt_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "prompts"
        / "scraping"
        / "maps_relevance_classifier.j2"
    )
    prompt = prompt_path.read_text(encoding="utf-8")
    assert "outpatient addiction centers can stay in scope" in prompt
    assert "ownership is unknown" in prompt
    assert "association-operated" in prompt
    assert "prefer `needs_review` over `unrelated`" in prompt


def test_contact_status_for_place_distinguishes_complete_partial_and_missing():
    complete = SimpleNamespace(
        international_phone_number="+213 555 00 00",
        official_website="https://clinic.dz/",
        raw_website=None,
    )
    phone_only = SimpleNamespace(
        international_phone_number="+213 555 00 00",
        official_website=None,
        raw_website=None,
    )
    website_only = SimpleNamespace(
        international_phone_number=None,
        official_website="https://clinic.dz/",
        raw_website=None,
    )
    missing = SimpleNamespace(
        international_phone_number=None,
        official_website=None,
        raw_website=None,
    )
    assert _contact_status_for_place(complete) == MapsContactStatus.COMPLETE.value
    assert _contact_status_for_place(phone_only) == MapsContactStatus.PHONE_ONLY.value
    assert _contact_status_for_place(website_only) == MapsContactStatus.WEBSITE_ONLY.value
    assert _contact_status_for_place(missing) == MapsContactStatus.MISSING.value


@pytest.mark.asyncio
async def test_missing_contact_filter_sets_contact_status_without_demoting_places(db, auth):
    run = await _create_run(db, auth)
    keep_both = MapsPlace(
        run_id=run.id,
        google_place_id="both-contact",
        raw_name="Both Contact Rehab",
        canonical_name="Both Contact Rehab",
        is_relevant=True,
        confidence_score=0.9,
        formatted_address="0 Street, Algiers",
        international_phone_number="+213 21 00 00 01",
        official_website="https://both.example/",
    )
    keep_phone = MapsPlace(
        run_id=run.id,
        google_place_id="phone-only",
        raw_name="Phone Only Rehab",
        canonical_name="Phone Only Rehab",
        is_relevant=True,
        confidence_score=0.9,
        formatted_address="1 Street, Algiers",
        international_phone_number="+213 21 00 00 00",
    )
    keep_site = MapsPlace(
        run_id=run.id,
        google_place_id="site-only",
        raw_name="Site Only Rehab",
        canonical_name="Site Only Rehab",
        is_relevant=True,
        confidence_score=0.9,
        formatted_address="2 Street, Algiers",
        official_website="https://site.example/",
    )
    drop = MapsPlace(
        run_id=run.id,
        google_place_id="no-contact",
        raw_name="No Contact Rehab",
        canonical_name="No Contact Rehab",
        is_relevant=True,
        confidence_score=0.9,
        formatted_address="3 Street, Algiers",
    )
    db.add_all([keep_both, keep_phone, keep_site, drop])
    await db.commit()

    demoted = await maps_census_service._apply_missing_contact_filter(
        maps_census_service._session_factory(db), run_id=run.id
    )
    assert demoted == 4
    await db.refresh(keep_both)
    await db.refresh(keep_phone)
    await db.refresh(keep_site)
    await db.refresh(drop)
    assert keep_both.is_relevant is True
    assert keep_phone.is_relevant is True
    assert keep_site.is_relevant is True
    assert drop.is_relevant is True
    assert keep_both.contact_status == MapsContactStatus.COMPLETE.value
    assert keep_phone.contact_status == MapsContactStatus.PHONE_ONLY.value
    assert keep_site.contact_status == MapsContactStatus.WEBSITE_ONLY.value
    assert drop.contact_status == MapsContactStatus.MISSING.value


@pytest.mark.asyncio
async def test_run_website_refresh_uses_address_fallback_when_name_match_fails(
    db, auth, monkeypatch
):
    _use_serper_website_search(monkeypatch)
    run = await _create_run(db, auth)
    run.status = MapsCensusStatus.COMPLETED
    run.cells_total = 1
    run.cells_completed = 1
    run.places_found = 1
    run.places_classified_relevant = 1
    await db.commit()

    place = MapsPlace(
        run_id=run.id,
        google_place_id="p-transliterated",
        raw_name="Dispanser Narkologicheskii Klinicheskii Gorodskoi",
        canonical_name="Dispanser Narkologicheskii Klinicheskii Gorodskoi",
        city_name="Minsk",
        formatted_address="улица Гастелло16, Minsk, Minskaja voblasć 220035",
        is_relevant=True,
    )
    db.add(place)
    await db.commit()

    class _FakeSearchProvider:
        name = "fake"

        async def search(self, request) -> list[SearchProviderResult]:
            return [
                SearchProviderResult(
                    rank=1,
                    url="https://gknd.by/",
                    title="Услуги — Минский городской клинический наркологический центр",
                    snippet="Учреждение здравоохранения, г. Минск, ул. Гастелло, 16",
                )
            ]

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_search_provider",
        lambda: _FakeSearchProvider(),
    )

    summary = await maps_census_service.run_website_refresh(db, run_id=run.id)
    assert summary["places_with_website"] == 1

    await db.refresh(place)
    assert place.official_website == "https://gknd.by/"
    assert place.website_source == "search"


@pytest.mark.asyncio
async def test_auto_refresh_selects_only_eligible_completed_runs(db, auth, monkeypatch):
    """Cron-driven backfill: due runs flip to RUNNING and get a refresh job kicked
    off; runs within cooldown, at the attempt cap, or with no gap are left alone.
    """
    bind = db.bind or db.get_bind()
    session_factory = async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", session_factory)

    now = datetime.now(UTC)

    def make_run(**overrides):
        defaults = dict(
            organization_id=auth.org_id,
            created_by=auth.user.id,
            country_code="BY",
            country_name="Belarus",
            status=MapsCensusStatus.COMPLETED,
            places_classified_relevant=10,
            places_with_website=5,
            completed_at=now - timedelta(hours=10),
        )
        defaults.update(overrides)
        run = MapsCensusRun(**defaults)
        db.add(run)
        return run

    eligible = make_run()
    within_cooldown = make_run(website_refresh_completed_at=now - timedelta(hours=1))
    max_attempts_hit = make_run(website_refresh_attempts=3)
    no_gap = make_run(places_with_website=10)
    await db.commit()

    triggered_run_ids: list[str] = []

    async def fake_refresh_job(ctx, run_id):
        triggered_run_ids.append(run_id)

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.refresh_maps_census_websites_job",
        fake_refresh_job,
    )
    captured_tasks = _tracking_create_task(monkeypatch, marker="fake_refresh_job")

    await auto_refresh_maps_census_websites({})
    await asyncio_gather(*captured_tasks)

    assert triggered_run_ids == [eligible.id]

    await db.refresh(eligible)
    await db.refresh(within_cooldown)
    await db.refresh(max_attempts_hit)
    await db.refresh(no_gap)
    assert eligible.status == MapsCensusStatus.RUNNING
    assert within_cooldown.status == MapsCensusStatus.COMPLETED
    assert max_attempts_hit.status == MapsCensusStatus.COMPLETED
    assert no_gap.status == MapsCensusStatus.COMPLETED


@pytest.mark.asyncio
async def test_auto_refresh_noop_when_disabled(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_auto_website_refresh_enabled", False)

    bind = db.bind or db.get_bind()
    session_factory = async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", session_factory)

    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="BY",
        country_name="Belarus",
        status=MapsCensusStatus.COMPLETED,
        places_classified_relevant=10,
        places_with_website=0,
        completed_at=datetime.now(UTC) - timedelta(hours=10),
    )
    db.add(run)
    await db.commit()

    async def fake_refresh_job(ctx, run_id):
        raise AssertionError("should not be called when auto-refresh is disabled")

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.refresh_maps_census_websites_job",
        fake_refresh_job,
    )
    captured_tasks = _tracking_create_task(monkeypatch, marker="fake_refresh_job")

    await auto_refresh_maps_census_websites({})
    assert captured_tasks == []

    await db.refresh(run)
    assert run.status == MapsCensusStatus.COMPLETED


@pytest.mark.asyncio
async def test_export_run_csv_without_addictions_when_enrichment_disabled(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", False)
    run = await _create_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="no-enrich",
        raw_name="Basic Rehab",
        canonical_name="Basic Rehab",
        is_relevant=True,
        confidence_score=0.92,
        formatted_address="1 Street, Minsk",
    )
    db.add(place)
    await db.commit()

    _, csv_body = await maps_census_service.export_run_csv(db, auth, run.id, tier="all")
    assert "Basic Rehab" in csv_body
    assert "Not Specified" in csv_body


@pytest.mark.asyncio
async def test_apply_post_classification_filters_downgrades_plus_code_and_rejects_generic_name(
    db, auth
):
    run = await _create_run(db, auth)
    low_confidence = MapsPlace(
        run_id=run.id,
        google_place_id="low",
        raw_name="Low Confidence Rehab",
        canonical_name="Low Confidence Rehab",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.PLAUSIBLE.value,
        confidence_score=0.65,
        formatted_address="123 Main St, Minsk",
    )
    plus_code_only = MapsPlace(
        run_id=run.id,
        google_place_id="plus-code",
        raw_name="Plus Code Rehab",
        canonical_name="Plus Code Rehab",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.PLAUSIBLE.value,
        confidence_score=0.85,
        formatted_address="QW2G+35C, Chéraga",
    )
    generic_name = MapsPlace(
        run_id=run.id,
        google_place_id="generic",
        raw_name="Addiction Treatment Center",
        canonical_name="Addiction Treatment Center",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.PLAUSIBLE.value,
        confidence_score=0.91,
        formatted_address="22 Clinic Way, Minsk",
    )
    export_ready = MapsPlace(
        run_id=run.id,
        google_place_id="ready",
        raw_name="Ready Rehab",
        canonical_name="Ready Rehab",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.PLAUSIBLE.value,
        confidence_score=0.92,
        formatted_address="456 Oak Ave, Minsk",
    )
    db.add_all([low_confidence, plus_code_only, generic_name, export_ready])
    await db.commit()

    session_factory = maps_census_service._session_factory(db)
    demoted = await maps_census_service._apply_post_classification_filters(
        session_factory, run_id=run.id
    )
    assert demoted == 2

    await db.refresh(low_confidence)
    await db.refresh(plus_code_only)
    await db.refresh(generic_name)
    await db.refresh(export_ready)
    assert low_confidence.is_relevant is True
    assert low_confidence.lifecycle_status == MapsLifecycleStatus.PLAUSIBLE.value
    assert plus_code_only.is_relevant is True
    assert plus_code_only.lifecycle_status == MapsLifecycleStatus.NEEDS_REVIEW.value
    assert generic_name.is_relevant is False
    assert generic_name.lifecycle_status == MapsLifecycleStatus.UNRELATED.value
    assert export_ready.is_relevant is True


@pytest.mark.asyncio
async def test_export_run_csv_includes_eligible_rows_with_placeholders(db, auth):
    run = await _create_run(db, auth)
    verified = MapsPlace(
        run_id=run.id,
        google_place_id="verified",
        raw_name="Verified Rehab",
        canonical_name="Verified Rehab",
        is_relevant=True,
        confidence_score=0.95,
        formatted_address="1 Rehab Lane, Minsk",
        official_website="https://verified.example/",
        international_phone_number="+375 17 123 4567",
        addictions_treated=["Alcohol", "Gambling"],
        languages_spoken=["English"],
        treatment_price="Contact for pricing",
        enrichment_status="completed",
    )
    flagged = MapsPlace(
        run_id=run.id,
        google_place_id="flagged",
        raw_name="Flagged Rehab",
        canonical_name="Flagged Rehab",
        is_relevant=True,
        confidence_score=0.75,
        formatted_address="2 Review St, Minsk",
        addictions_treated=["Heroin"],
        enrichment_status="completed",
    )
    excluded = MapsPlace(
        run_id=run.id,
        google_place_id="excluded",
        raw_name="Excluded Rehab",
        canonical_name="Excluded Rehab",
        is_relevant=False,
        confidence_score=0.95,
        formatted_address="3 Skip Rd, Minsk",
    )
    db.add_all([verified, flagged, excluded])
    await db.commit()

    filename, csv_body = await maps_census_service.export_run_csv(
        db, auth, run.id, tier="all"
    )
    assert filename == "by-maps-census-export.csv"
    lines = csv_body.strip().splitlines()
    assert lines[0] == (
        "Facility Name,Addictions Treated,Location,Languages Spoken,Website,Phone Number,Treatment Price"
    )
    assert len(lines) == 3
    assert any("Verified Rehab" in line for line in lines[1:])
    assert any("Flagged Rehab" in line for line in lines[1:])
    assert any("Alcohol, Gambling" in line for line in lines[1:])
    assert any("https://verified.example/" in line for line in lines[1:])

    _, verified_only = await maps_census_service.export_run_csv(
        db, auth, run.id, tier="verified"
    )
    assert "Verified Rehab" in verified_only
    assert "Flagged Rehab" not in verified_only

    _, flagged_only = await maps_census_service.export_run_csv(
        db, auth, run.id, tier="flagged"
    )
    assert "Flagged Rehab" in flagged_only
    assert "Verified Rehab" not in flagged_only
