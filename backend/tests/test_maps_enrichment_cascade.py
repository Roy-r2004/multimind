"""Targeted tests for cascaded Maps enrichment pipeline."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def enable_cascade_enrichment(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_census_cascade_enrichment_enabled", True)


from app.db.models import (
    MapsCensusRun,
    MapsClientEligibility,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
)
from app.services.scraping.maps_enrichment_processing_state import (
    MapsEnrichmentPipelineState,
    default_pipeline_state,
)
from app.services.scraping.maps_enrichment_cascade_service import maps_enrichment_cascade_service
from app.services.scraping.maps_enrichment_selection import (
    build_selection_report,
    should_select_for_expensive_pipeline,
    skip_reason_for_place,
)
from app.services.scraping.maps_place_website_resolution import (
    classify_website_relationship,
    resolve_from_places_website,
)
from app.services.scraping.maps_primary_extraction import map_primary_to_enrichment_fields
from app.services.scraping.maps_primary_extraction import MapsPrimaryExtractionResult
from app.services.scraping.maps_sonar_fallback import SonarBudget, sonar_fallback_reason


def _place(**kwargs):
    base = dict(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
        canonical_name="Centre de Traitement",
        raw_name="Centre de Traitement",
        city_name="Algiers",
        country_name="Algeria",
    )
    base.update(kwargs)
    return type("Place", (), base)()


def test_unrelated_records_skip_expensive_pipeline():
    place = _place(
        is_relevant=False,
        lifecycle_status=MapsLifecycleStatus.UNRELATED.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
    )
    assert should_select_for_expensive_pipeline(place) is False
    assert skip_reason_for_place(place) == "not_relevant"


def test_public_and_individual_skip_expensive_pipeline():
    public = _place(lifecycle_status=MapsLifecycleStatus.CONFIRMED_PUBLIC.value)
    assert should_select_for_expensive_pipeline(public) is False
    individual = _place(
        lifecycle_status=MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value
    )
    assert should_select_for_expensive_pipeline(individual) is False


def test_google_places_website_reused():
    place = type(
        "Place",
        (),
        {
            "raw_website": "https://centre-traitement.dz",
            "official_website": None,
            "canonical_name": "Centre Traitement DZ",
            "raw_name": "Centre Traitement DZ",
            "city_name": "Algiers",
            "website_source": None,
            "website_resolution_source": None,
            "classification_evidence": None,
        },
    )()
    outcome = resolve_from_places_website(place, country_name="Algeria")
    assert outcome is not None
    assert outcome.official_website == "https://centre-traitement.dz"
    assert outcome.website_resolution_source == "google_places"


def test_deterministic_website_matching_classifies_directory():
    relationship, confidence, _ = classify_website_relationship(
        url="https://www.yelp.com/biz/example",
        facility_name="Example Rehab",
        city="Algiers",
        country_name="Algeria",
    )
    assert relationship == "directory"
    assert confidence >= 0.7


def test_primary_extraction_maps_to_eligibility_inputs():
    result = MapsPrimaryExtractionResult(
        facility_type="outpatient_addiction_center",
        operator_type="private_company",
        ownership_status="confirmed_non_government",
        organization_scope="facility",
        addiction_treatment_mission=True,
        substances_or_addictions_treated=["Alcohol"],
        languages=["Arabic"],
        evidence=[],
    )
    mapped = map_primary_to_enrichment_fields(result)
    assert mapped["facility_type"] == "outpatient_addiction_center"
    assert mapped["addiction_focus_confirmed"] is True


def test_sonar_only_for_unresolved_and_budget_enforced():
    reason = sonar_fallback_reason(
        has_website=False,
        primary_confidence=0.4,
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        facility_type="unknown",
        ownership_status="ownership_unknown",
        addiction_focus_confirmed=None,
    )
    assert reason == "no_official_website"
    budget = SonarBudget(
        enabled=True,
        max_percent=20,
        max_per_campaign=200,
        selected_candidates=717,
        calls_used=143,
    )
    assert budget.max_calls == 143
    assert budget.can_call() is False


def test_malformed_sonar_remains_retryable():
    from app.services.scraping.maps_enrichment_fetch import EnrichmentFetchError

    exc = EnrichmentFetchError("parse failed", raw_excerpt='{"broken":')
    assert "parse failed" in str(exc)
    assert exc.raw_excerpt.startswith('{"broken"')


@pytest.mark.asyncio
async def test_recovery_preserves_discovery_data(db, auth):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="DZ",
        country_name="Algeria",
        status="completed",
        places_found=2,
    )
    db.add(run)
    await db.flush()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="abc",
        raw_name="Test",
        canonical_name="Test",
        place_types=["health"],
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        enrichment_status=MapsPlaceEnrichmentStatus.FAILED.value,
        enrichment_error_message="parse failed",
    )
    db.add(place)
    await db.commit()

    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(
        bind=db.bind,
        expire_on_commit=False,
    )
    reset = await maps_enrichment_cascade_service.reset_for_recovery(factory, run_id=run.id)
    assert reset["reset_places"] == 1

    await db.refresh(place)
    refreshed = place
    assert refreshed.enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value
    assert refreshed.google_place_id == "abc"
    assert refreshed.raw_name == "Test"


@pytest.mark.asyncio
async def test_recovery_finalizes_stuck_running_place(db, auth):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="DZ",
        country_name="Algeria",
        status="completed",
        places_found=1,
    )
    db.add(run)
    await db.flush()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="running1",
        raw_name="Stuck",
        canonical_name="Stuck",
        place_types=["health"],
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        enrichment_status=MapsPlaceEnrichmentStatus.RUNNING.value,
        enrichment_pipeline_state=MapsEnrichmentPipelineState.CRAWL_PENDING.value,
        official_website="http://example.com",
    )
    db.add(place)
    await db.commit()

    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(
        bind=db.bind,
        expire_on_commit=False,
    )
    reset = await maps_enrichment_cascade_service.reset_for_recovery(factory, run_id=run.id)
    assert reset["reset_places"] == 1
    assert reset["reset_stuck_running"] == 1

    await db.refresh(place)
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
    assert place.enrichment_pipeline_state == MapsEnrichmentPipelineState.NEEDS_REVIEW.value


@pytest.mark.asyncio
async def test_stale_running_place_finalized_at_enrich_start(db, auth):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="DZ",
        country_name="Algeria",
        status="completed",
    )
    db.add(run)
    await db.flush()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="stale-running",
        raw_name="Stuck",
        canonical_name="Stuck",
        place_types=["health"],
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        enrichment_status=MapsPlaceEnrichmentStatus.RUNNING.value,
    )
    db.add(place)
    await db.commit()

    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    finalized = await maps_enrichment_cascade_service._finalize_stale_running_places(
        factory, run_id=run.id
    )
    assert finalized == 1
    await db.refresh(place)
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
    assert place.enrichment_pipeline_state == MapsEnrichmentPipelineState.NEEDS_REVIEW.value


@pytest.mark.asyncio
async def test_cheap_skip_finalizes_not_relevant_pending(db, auth):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="DZ",
        country_name="Algeria",
        status="completed",
    )
    db.add(run)
    await db.flush()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="u-pending",
        raw_name="Store",
        canonical_name="Store",
        place_types=["store"],
        is_relevant=False,
        lifecycle_status=MapsLifecycleStatus.UNRELATED.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    await maps_enrichment_cascade_service._finalize_skipped_place(
        db,
        place,
        skip_reason_for_place(place),
    )
    await db.commit()
    await db.refresh(place)
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.SKIPPED.value
    assert place.enrichment_extraction_source == "deterministic_skip"


@pytest.mark.asyncio
async def test_selection_report_counts(db, auth):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="DZ",
        country_name="Algeria",
        status="completed",
    )
    db.add(run)
    await db.flush()
    db.add_all(
        [
            MapsPlace(
                run_id=run.id,
                google_place_id="r1",
                raw_name="Review",
                canonical_name="Review",
                place_types=["health"],
                is_relevant=True,
                lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
                client_eligibility=MapsClientEligibility.REVIEW.value,
            ),
            MapsPlace(
                run_id=run.id,
                google_place_id="u1",
                raw_name="Unrelated",
                canonical_name="Unrelated",
                place_types=["store"],
                is_relevant=True,
                lifecycle_status=MapsLifecycleStatus.UNRELATED.value,
                client_eligibility=MapsClientEligibility.EXCLUDED.value,
            ),
        ]
    )
    await db.commit()
    report = await build_selection_report(db, run_id=run.id)
    assert report.selected_count == 1
    assert report.skipped_count == 1
