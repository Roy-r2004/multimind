"""Integration tests for the standalone Maps census orchestration."""

from __future__ import annotations

import json
from asyncio import gather as asyncio_gather
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.db.models import MapsCensusRun, MapsCensusStatus, MapsPlace
from app.services.scraping.maps_census_service import (
    _accepted_direct_llm_website_url,
    _accepted_llm_website_url,
    _maps_website_search_queries,
    _match_official_website_by_address,
    _normalize_website_payload,
    auto_refresh_maps_census_websites,
    maps_census_service,
)
from app.services.scraping.maps_grid_planner import MapsGridCell
from app.services.scraping.maps_places_client import PlaceResult
from app.services.scraping.search_providers.base import SearchProviderResult


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
async def test_refresh_recounts_only_retained_verified_facilities(db, auth, monkeypatch):
    _use_serper_website_search(monkeypatch)
    run = await _create_run(db, auth)
    run.status = MapsCensusStatus.COMPLETED
    run.places_found = 2
    run.places_classified_relevant = 2
    await db.commit()

    db.add_all(
        [
            MapsPlace(
                run_id=run.id,
                google_place_id="verified-count",
                raw_name="Verified Rehab",
                canonical_name="Verified Rehab",
                is_relevant=True,
                official_website="https://verified.example/",
                website_source="search",
            ),
            MapsPlace(
                run_id=run.id,
                google_place_id="unverified-count",
                raw_name="Unverified Rehab",
                canonical_name="Unverified Rehab",
                is_relevant=True,
            ),
        ]
    )
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
    assert run.places_found == 2
    assert run.places_classified_relevant == 2
    assert run.places_with_website == 1


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
async def test_export_run_csv_without_addictions_when_enrichment_disabled(db, auth):
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
async def test_apply_post_classification_filters_demotes_low_confidence_and_missing_address(
    db, auth
):
    run = await _create_run(db, auth)
    low_confidence = MapsPlace(
        run_id=run.id,
        google_place_id="low",
        raw_name="Low Confidence Rehab",
        canonical_name="Low Confidence Rehab",
        is_relevant=True,
        confidence_score=0.65,
        formatted_address="123 Main St, Minsk",
    )
    missing_address = MapsPlace(
        run_id=run.id,
        google_place_id="no-address",
        raw_name="No Address Rehab",
        canonical_name="No Address Rehab",
        is_relevant=True,
        confidence_score=0.85,
    )
    export_ready = MapsPlace(
        run_id=run.id,
        google_place_id="ready",
        raw_name="Ready Rehab",
        canonical_name="Ready Rehab",
        is_relevant=True,
        confidence_score=0.92,
        formatted_address="456 Oak Ave, Minsk",
    )
    db.add_all([low_confidence, missing_address, export_ready])
    await db.commit()

    session_factory = maps_census_service._session_factory(db)
    demoted = await maps_census_service._apply_post_classification_filters(
        session_factory, run_id=run.id
    )
    assert demoted == 2

    await db.refresh(low_confidence)
    await db.refresh(missing_address)
    await db.refresh(export_ready)
    assert low_confidence.is_relevant is False
    assert missing_address.is_relevant is False
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
