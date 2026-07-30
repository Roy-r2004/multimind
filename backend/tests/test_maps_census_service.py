"""Integration tests for the standalone Maps census orchestration."""

from __future__ import annotations

import json
from asyncio import gather as asyncio_gather
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.dependencies import AuthContext
from app.db.models import MapsCensusRun, MapsCensusStatus, MapsPlace
from app.services.scraping.maps_census_service import (
    _maps_website_search_queries,
    _match_official_website_by_address,
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
async def test_run_census_falls_back_to_search_when_places_has_no_website(db, auth, monkeypatch):
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

    class _FakeSearchProvider:
        name = "fake"

        def __init__(self) -> None:
            self.requests: list[str] = []

        async def search(self, request) -> list[SearchProviderResult]:
            self.requests.append(request.query)
            return [
                SearchProviderResult(
                    rank=1,
                    url="https://centre-gamma.by/",
                    title="Centre Gamma — official site",
                    snippet="Official rehabilitation center in Minsk, Belarus",
                )
            ]

    fake_search_provider = _FakeSearchProvider()
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_search_provider",
        lambda: fake_search_provider,
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    await db.refresh(run)
    assert run.status == MapsCensusStatus.COMPLETED
    assert run.places_with_website == 1
    assert len(fake_search_provider.requests) == 1

    place = (
        await db.execute(select(MapsPlace).where(MapsPlace.google_place_id == "place-no-site"))
    ).scalar_one()
    assert place.is_relevant is True
    assert place.official_website == "https://centre-gamma.by/"
    assert place.website_source == "search"


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

    class _FakeSearchProvider:
        name = "fake"

        async def search(self, request) -> list[SearchProviderResult]:
            return [
                SearchProviderResult(
                    rank=1,
                    url="https://centre-delta.by/",
                    title="Centre Delta — official site",
                    snippet="Official rehabilitation center in Minsk, Belarus",
                )
            ]

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_search_provider",
        lambda: _FakeSearchProvider(),
    )

    summary = await maps_census_service.run_website_refresh(db, run_id=run.id)
    assert summary["places_with_website"] == 1

    await db.refresh(run)
    assert run.status == MapsCensusStatus.COMPLETED
    assert run.places_with_website == 1

    await db.refresh(place)
    assert place.official_website == "https://centre-delta.by/"
    assert place.website_source == "search"


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
