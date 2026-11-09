"""Regression tests: a permanent classification/technical failure in Phase 2
must never, on its own, produce a third "review" eligibility outcome.

Root cause fixed: both `_finalize_place_failure` (unexpected/timeout error,
retries exhausted) and the Sonar-fallback-exhausted branch inside
`_classify_place` directly stamped `client_eligibility = REVIEW` for any
place that wasn't already kept by Phase 1 — bypassing
`compute_client_eligibility` entirely. Per Phase 2's strictly-binary
KEEP/DROP policy, a technical failure is missing evidence, never a
disqualification, so it must resolve to ELIGIBLE unless the place's
already-stored evidence (or a manual exclusion) reliably confirms a
disqualifying condition — exactly what `compute_client_eligibility` already
decides from whatever fields survived up to the failure.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.db.models import (
    MapsClientEligibility,
    MapsFacilityType,
    MapsLifecycleStatus,
    MapsOrganizationScope,
    MapsOwnershipStatus,
    MapsPlace,
)
from app.services.scraping.maps_classification_service import MapsClassificationService
from app.services.scraping.maps_census_service import maps_census_service
from app.services.scraping.maps_country_profile_service import maps_country_profile_service
from app.services.scraping.maps_grid_planner import MapsGridCell
from app.services.scraping.maps_primary_extraction import PrimaryExtractionEvidence
from app.services.scraping.maps_structured_classification import (
    MapsStructuredClassificationResult,
    StructuredClassificationProviderResult,
)

from tests.test_maps_census_service import _create_run, _FakeGridPlanner, _FakePlacesClient


def _factory(db):
    return async_sessionmaker(bind=db.bind, expire_on_commit=False)


# --- _finalize_place_failure: a permanent error on a non-kept place must
# recompute the real eligibility from whatever evidence already exists,
# never blindly stamp REVIEW. ---


@pytest.mark.asyncio
async def test_finalize_place_failure_insufficient_evidence_becomes_eligible(db, auth):
    run = await _create_run(db, auth)
    settings = get_settings()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="insufficient-1",
        raw_name="Unresolved Facility",
        canonical_name="Unresolved Facility",
        is_relevant=True,
        keep_drop_decision="review",
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        enrichment_attempts=max(1, settings.maps_place_max_attempts),
    )
    db.add(place)
    await db.commit()

    service = MapsClassificationService()
    await service._finalize_place_failure(
        _factory(db), place_id=place.id, error="simulated permanent failure"
    )

    await db.refresh(place)
    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
    assert place.lifecycle_status == MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value


@pytest.mark.asyncio
async def test_finalize_place_failure_never_overrides_confirmed_disqualification(db, auth):
    """A technical failure must not launder a reliably disqualified place
    (already-stored evidence confirms government ownership) into eligible —
    compute_client_eligibility's own hard-exclude rules still apply."""
    run = await _create_run(db, auth)
    settings = get_settings()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="confirmed-govt-1",
        raw_name="State Narcology Dispensary",
        canonical_name="State Narcology Dispensary",
        is_relevant=True,
        keep_drop_decision="review",
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        ownership_status=MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value,
        enrichment_attempts=max(1, settings.maps_place_max_attempts),
    )
    db.add(place)
    await db.commit()

    service = MapsClassificationService()
    await service._finalize_place_failure(
        _factory(db), place_id=place.id, error="simulated permanent failure"
    )

    await db.refresh(place)
    assert place.client_eligibility == MapsClientEligibility.EXCLUDED.value


@pytest.mark.asyncio
async def test_finalize_place_failure_never_overrides_manual_exclusion(db, auth):
    """A technical failure must never override an existing manual Reject —
    manually_excluded_at is checked first by compute_client_eligibility."""
    run = await _create_run(db, auth)
    settings = get_settings()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="manual-reject-1",
        raw_name="Manually Rejected Facility",
        canonical_name="Manually Rejected Facility",
        is_relevant=False,
        keep_drop_decision="review",
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        manually_excluded_at=datetime.now(UTC),
        # Would otherwise look eligible if the manual exclusion were ignored.
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        addiction_focus_confirmed=True,
        enrichment_attempts=max(1, settings.maps_place_max_attempts),
    )
    db.add(place)
    await db.commit()

    service = MapsClassificationService()
    await service._finalize_place_failure(
        _factory(db), place_id=place.id, error="simulated permanent failure"
    )

    await db.refresh(place)
    assert place.client_eligibility == MapsClientEligibility.EXCLUDED.value


# --- The Sonar-fallback-exhausted branch inside _classify_place: same
# requirement, exercised end to end through the real run_census()
# orchestration (discovery -> keep/drop -> structured classification ->
# Sonar fallback failing permanently). ---


def _evidence(field: str, value: str) -> PrimaryExtractionEvidence:
    return PrimaryExtractionEvidence(
        field=field,
        value=value,
        quote=f"Evidence confirming {field}={value}",
        source_url="https://example.org/about",
        confidence=0.9,
    )


class _FakeStructuredClassificationProvider:
    def __init__(self, results_by_name: dict[str, MapsStructuredClassificationResult]) -> None:
        self._results_by_name = results_by_name

    async def classify_one(self, *, facility_payload, **_kwargs):
        name = facility_payload.get("name") or ""
        output = self._results_by_name.get(name) or MapsStructuredClassificationResult()
        return StructuredClassificationProviderResult(output=output)


@pytest.mark.asyncio
async def test_sonar_fallback_permanent_failure_on_non_kept_place_becomes_eligible(
    db, auth, monkeypatch
):
    """A place genuinely left with unresolved residential evidence (so
    Sonar fires) whose Sonar call then fails permanently, on a place Phase 1
    never confidently kept (``review``), must resolve to ELIGIBLE — not the
    old hardcoded REVIEW — once retries are exhausted."""
    monkeypatch.setattr(get_settings(), "maps_census_website_search_enabled", False)
    monkeypatch.setattr(get_settings(), "maps_place_max_attempts", 1)

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

    async def _noop_build_profile(_db, *, run_id: str):
        del run_id
        return None

    monkeypatch.setattr(maps_country_profile_service, "build_profile_for_run", _noop_build_profile)

    async def _noop_enqueue(self, run_id: str) -> None:
        del run_id

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._maybe_enqueue_enrichment",
        _noop_enqueue,
    )

    place_name = "Unresolved Residential Candidate"
    run = await _create_run(db, auth)
    seed = [MapsGridCell(region_name="Finland", city_name="Helsinki", query_text=place_name)]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )

    from app.services.scraping.maps_places_client import PlaceResult

    client = _FakePlacesClient(
        by_query={
            place_name: [
                PlaceResult(google_place_id="g1", raw_name=place_name, formatted_address="x")
            ]
        }
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda *_args, **_kwargs: client,
    )

    async def fake_classify_keep_drop(_session, place, *, country_code, country_name):
        from app.services.scraping.maps_keep_drop_service import KeepDropDecision as _KDD

        return (
            _KDD.model_validate(
                json.loads(
                    '{"decision": "review", "reason": "genuine signal, residential unconfirmed",'
                    ' "confidence": 0.55, "evidence": []}'
                )
            ),
            "nano",
        )

    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.classify_place_keep_drop",
        fake_classify_keep_drop,
    )

    provider = _FakeStructuredClassificationProvider(
        {
            place_name: MapsStructuredClassificationResult(
                facility_type="general_mental_health_clinic",
                operator_type="nonprofit",
                ownership_status="confirmed_non_government",
                organization_scope="facility",
                addiction_treatment_mission=True,
                residential_accommodation=None,
                care_setting=[],
                evidence=[
                    _evidence("operator_type", "nonprofit"),
                    _evidence("ownership_status", "confirmed_non_government"),
                    _evidence("facility_type", "general_mental_health_clinic"),
                    _evidence("organization_scope", "facility"),
                ],
            )
        }
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_classification_service.create_structured_classification_provider",
        lambda: provider,
    )

    async def fake_fetch_sonar_classify_one(**_kwargs):
        raise RuntimeError("simulated permanent Sonar provider failure")

    monkeypatch.setattr(
        "app.services.scraping.maps_classification_service.fetch_sonar_classify_one",
        fake_fetch_sonar_classify_one,
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    from sqlalchemy import select

    place = (
        await db.execute(
            select(MapsPlace).where(MapsPlace.run_id == run.id, MapsPlace.canonical_name == place_name)
        )
    ).scalar_one()
    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
