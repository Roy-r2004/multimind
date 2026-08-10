"""Tests for multi-location brand disaggregation during detail enrichment.

Covers the pure-logic pieces (signal pre-filter, candidate filtering, dedup)
and the DB-touching pieces (candidate creation, run status flip + re-enqueue)
independently of the LLM call itself, which is exercised via a fake provider.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.dependencies import AuthContext
from app.db.models import MapsCensusRun, MapsCensusStatus, MapsPlace, MapsPlaceEnrichmentStatus
from app.services.scraping.maps_detail_enrichment_service import MapsDetailEnrichmentService
from app.services.scraping.maps_enrichment_response_parser import EnrichmentParseStats
from app.services.scraping.maps_quota_tracker import MapsQuotaTracker
from app.services.scraping.maps_sibling_location_service import (
    SIBLING_EXTRACTION_SOURCE,
    SiblingLocationCandidate,
    contains_multi_location_signal,
    create_sibling_candidates,
    extract_sibling_locations,
    reopen_run_for_new_candidates,
)
from app.services.scraping.maps_website_crawl_service import CrawledPage, WebsiteCrawlOutcome


def _run(auth, **kwargs):
    base = dict(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="GB",
        country_name="United Kingdom",
        status=MapsCensusStatus.COMPLETED,
        places_found=1,
    )
    base.update(kwargs)
    return MapsCensusRun(**base)


def _place(run_id: str, key: str, **kwargs) -> MapsPlace:
    base = dict(
        run_id=run_id,
        google_place_id=key,
        raw_name=key,
        canonical_name=key,
        city_name="London",
        is_relevant=True,
        keep_drop_decision="keep",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    base.update(kwargs)
    return MapsPlace(**base)


# --- signal pre-filter -------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Visit our Locations page to find a center near you.",
        "We have treatment centers across the country.",
        "Find a location that works for you.",
        "Our network of treatment centers spans three cities.",
        "See our branches in Manchester and Leeds.",
    ],
)
def test_contains_multi_location_signal_true_cases(text):
    assert contains_multi_location_signal(text) is True


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "We offer residential addiction treatment with a 17-person capacity.",
        "Call us today to book an assessment.",
    ],
)
def test_contains_multi_location_signal_false_cases(text):
    assert contains_multi_location_signal(text) is False


# --- candidate filtering -------------------------------------------------


def test_headquarters_and_administrative_are_not_candidates():
    hq = SiblingLocationCandidate(
        facility_name="Recovery Group HQ", city="London", location_type="headquarters"
    )
    admin = SiblingLocationCandidate(
        facility_name="Recovery Group Admin Office", city="London", location_type="administrative_office"
    )
    outpatient = SiblingLocationCandidate(
        facility_name="Recovery Group Day Clinic", city="Bristol", location_type="outpatient_only"
    )
    assert hq.is_candidate_location() is False
    assert admin.is_candidate_location() is False
    assert outpatient.is_candidate_location() is False


def test_inpatient_location_with_city_is_a_candidate():
    branch = SiblingLocationCandidate(
        facility_name="Recovery Group Manchester", city="Manchester", location_type="inpatient_facility"
    )
    assert branch.is_candidate_location() is True


def test_candidate_without_name_or_location_is_rejected():
    no_name = SiblingLocationCandidate(facility_name="", city="Manchester", location_type="inpatient_facility")
    no_location = SiblingLocationCandidate(
        facility_name="Recovery Group Somewhere", location_type="inpatient_facility"
    )
    assert no_name.is_candidate_location() is False
    assert no_location.is_candidate_location() is False


# --- LLM call + parsing ----------------------------------------------------


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeProvider:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls = 0

    async def complete(self, **_kwargs):
        self.calls += 1
        return _FakeResponse(self._text)


@pytest.mark.asyncio
async def test_extract_sibling_locations_filters_non_candidates_from_response():
    import json

    payload = json.dumps(
        {
            "results": [
                {"facility_name": "Recovery Group Manchester", "city": "Manchester", "location_type": "inpatient_facility", "confidence": 0.9},
                {"facility_name": "Recovery Group HQ", "city": "London", "location_type": "headquarters", "confidence": 0.95},
            ]
        }
    )
    provider = _FakeProvider(payload)
    result = await extract_sibling_locations(
        provider,
        model_slug="test-model",
        facility_payload={"place_id": "p1", "name": "Recovery Group London"},
        crawl_excerpt="Our Locations: Manchester, London (HQ)",
        country_code="GB",
        country_name="United Kingdom",
    )
    assert provider.calls == 1
    assert len(result) == 1
    assert result[0].facility_name == "Recovery Group Manchester"


@pytest.mark.asyncio
async def test_extract_sibling_locations_empty_results_for_single_location_org():
    import json

    provider = _FakeProvider(json.dumps({"results": []}))
    result = await extract_sibling_locations(
        provider,
        model_slug="test-model",
        facility_payload={"place_id": "p1", "name": "Solo Rehab"},
        crawl_excerpt=None,
        country_code="GB",
        country_name="United Kingdom",
    )
    assert result == []


# --- candidate creation + dedup ----------------------------------------------------


@pytest.mark.asyncio
async def test_create_sibling_candidates_inserts_in_keep_drop_ready_state(db, auth: AuthContext):
    run = _run(auth)
    db.add(run)
    await db.flush()
    parent = _place(run.id, "parent-1", canonical_name="Recovery Group London")
    db.add(parent)
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    candidates = [
        SiblingLocationCandidate(
            facility_name="Recovery Group Manchester",
            full_physical_address="1 Deansgate, Manchester",
            city="Manchester",
            location_type="inpatient_facility",
            confidence=0.9,
        )
    ]

    created = await create_sibling_candidates(
        factory, run_id=run.id, parent_place=parent, candidates=candidates
    )
    assert created == 1

    async with factory() as session:
        from sqlalchemy import select

        rows = (
            await session.execute(select(MapsPlace).where(MapsPlace.run_id == run.id))
        ).scalars().all()
    siblings = [p for p in rows if p.id != parent.id]
    assert len(siblings) == 1
    sibling = siblings[0]
    assert sibling.canonical_name == "Recovery Group Manchester"
    assert sibling.keep_drop_decision is None
    assert sibling.is_relevant is True
    assert sibling.lifecycle_status == "needs_review"
    assert sibling.client_eligibility == "review"
    assert sibling.discovery_sources == [SIBLING_EXTRACTION_SOURCE]
    assert sibling.source_record_ids == [parent.id]
    assert sibling.google_place_id.startswith("sibling:")


@pytest.mark.asyncio
async def test_create_sibling_candidates_skips_already_discovered_place(db, auth: AuthContext):
    run = _run(auth)
    db.add(run)
    await db.flush()
    parent = _place(run.id, "parent-1", canonical_name="Recovery Group London")
    already_known = _place(
        run.id, "gplace-manchester", canonical_name="Recovery Group Manchester", city_name="Manchester"
    )
    db.add_all([parent, already_known])
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    candidates = [
        SiblingLocationCandidate(
            facility_name="Recovery Group Manchester",
            city="Manchester",
            location_type="inpatient_facility",
            confidence=0.9,
        )
    ]
    created = await create_sibling_candidates(
        factory, run_id=run.id, parent_place=parent, candidates=candidates
    )
    assert created == 0


@pytest.mark.asyncio
async def test_create_sibling_candidates_is_idempotent_across_calls(db, auth: AuthContext):
    """Re-running detail enrichment (e.g. re-enrich-keeps) must not create a
    second row for the same sibling — the synthetic id is deterministic."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    parent = _place(run.id, "parent-1", canonical_name="Recovery Group London")
    db.add(parent)
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    candidates = [
        SiblingLocationCandidate(
            facility_name="Recovery Group Leeds", city="Leeds", location_type="residential_facility"
        )
    ]
    first = await create_sibling_candidates(factory, run_id=run.id, parent_place=parent, candidates=candidates)
    second = await create_sibling_candidates(factory, run_id=run.id, parent_place=parent, candidates=candidates)
    assert first == 1
    assert second == 0


# --- run status flip + re-enqueue ----------------------------------------------------


@pytest.mark.asyncio
async def test_reopen_run_for_new_candidates_flips_completed_to_running(db, auth: AuthContext, monkeypatch):
    run = _run(auth, status=MapsCensusStatus.COMPLETED, places_found=5)
    db.add(run)
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)

    enqueued = []

    async def fake_enqueue(job_name, run_id, **_kwargs):
        enqueued.append(job_name)

    from app.services.scraping.maps_census_service import maps_census_service

    monkeypatch.setattr(maps_census_service, "_enqueue_job", fake_enqueue)

    await reopen_run_for_new_candidates(factory, run_id=run.id, created_count=2)

    async with factory() as session:
        refreshed = await session.get(MapsCensusRun, run.id)
        assert refreshed.status == MapsCensusStatus.RUNNING
        assert refreshed.places_found == 7
    assert enqueued == ["run_maps_keep_drop_job"]


@pytest.mark.asyncio
async def test_reopen_run_for_new_candidates_is_a_noop_when_nothing_created(db, auth: AuthContext, monkeypatch):
    run = _run(auth, status=MapsCensusStatus.COMPLETED)
    db.add(run)
    await db.commit()
    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)

    enqueued = []
    from app.services.scraping.maps_census_service import maps_census_service

    async def fake_enqueue(job_name, run_id, **_kwargs):
        enqueued.append(job_name)

    monkeypatch.setattr(maps_census_service, "_enqueue_job", fake_enqueue)

    await reopen_run_for_new_candidates(factory, run_id=run.id, created_count=0)

    async with factory() as session:
        refreshed = await session.get(MapsCensusRun, run.id)
        assert refreshed.status == MapsCensusStatus.COMPLETED
    assert enqueued == []


# --- recursion guard, exercised through the real _enrich_batch entry point ------------


@pytest.mark.asyncio
async def test_enrich_batch_skips_sibling_extraction_for_already_sibling_discovered_places(
    db, auth: AuthContext, monkeypatch
):
    """A sibling-discovered place must never trigger its own sibling
    extraction — caps expansion to exactly one level deep."""
    run = _run(auth, country_code="AD", country_name="Andorra")
    db.add(run)
    await db.flush()
    fresh_place = _place(
        run.id, "fresh-1", canonical_name="Fresh Facility", raw_website="https://fresh.example.com"
    )
    already_sibling = _place(
        run.id,
        "sibling-1",
        canonical_name="Already A Sibling",
        raw_website="https://sibling.example.com",
        discovery_sources=[SIBLING_EXTRACTION_SOURCE],
    )
    db.add_all([fresh_place, already_sibling])
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)

    fake_outcome = WebsiteCrawlOutcome(
        normalized_domain="example.com",
        pages=[
            CrawledPage(
                url="https://example.com/locations",
                title="Our Locations",
                text_excerpt="Visit our Locations page to find a center near you.",
                http_status=200,
            )
        ],
        page_urls=["https://example.com/locations"],
        cache_hit=False,
    )

    async def fake_crawl_website(*_args, **_kwargs):
        return fake_outcome

    async def fake_fetch_detail_batch(self, payloads, **kwargs):
        return []

    extraction_calls: list[str] = []

    async def fake_extract_sibling_locations(_provider, *, facility_payload, **_kwargs):
        extraction_calls.append(facility_payload["place_id"])
        return []

    monkeypatch.setattr(
        "app.services.scraping.maps_detail_enrichment_service.maps_website_crawl_service.crawl_website",
        fake_crawl_website,
    )
    monkeypatch.setattr(
        MapsDetailEnrichmentService, "_fetch_detail_batch", fake_fetch_detail_batch
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_detail_enrichment_service.extract_sibling_locations",
        fake_extract_sibling_locations,
    )

    service = MapsDetailEnrichmentService()
    await service._enrich_batch(
        factory,
        places=[fresh_place, already_sibling],
        country_code="AD",
        country_name="Andorra",
        parse_stats=EnrichmentParseStats(),
        tracker=MapsQuotaTracker(),
    )

    assert extraction_calls == [fresh_place.id]
