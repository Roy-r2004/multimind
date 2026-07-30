"""Integration tests for the standalone Maps census orchestration."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.dependencies import AuthContext
from app.db.models import MapsCensusCellStatus, MapsCensusRun, MapsCensusStatus, MapsPlace
from app.services.scraping.maps_census_service import maps_census_service
from app.services.scraping.maps_grid_planner import MapsGridCell
from app.services.scraping.maps_places_client import PlaceResult


class _FakeGridPlanner:
    def __init__(self, cells: list[MapsGridCell]) -> None:
        self._cells = cells

    async def plan(self, **_kwargs) -> list[MapsGridCell]:
        return self._cells


class _FakePlacesClient:
    def __init__(self, by_query: dict[str, list[PlaceResult]]) -> None:
        self._by_query = by_query

    def is_configured(self) -> bool:
        return True

    async def search_text(self, *, query: str, region_code: str, max_results: int) -> list[PlaceResult]:
        return self._by_query.get(query, [])


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
                    confidence=0.9 if is_rehab else 0.8,
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
