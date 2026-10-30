"""Regression tests for automatic Phase 2 eligibility verification.

Root cause fixed: ``_keep_drop_gate_passed`` in maps_enrichment_selection.py
excluded REVIEW-decision places from the expensive classification pipeline,
so ``_finalize_cheap_skips`` permanently stamped every REVIEW candidate
"skipped" before ``classify_run`` ever got a chance to extract the
ownership/facility-type/residential evidence ``compute_client_eligibility``
needs. On top of that, the classification step that extracts that mandatory
evidence only ever ran as part of the same gated cascade as the OPTIONAL
"Enrich with AI" contact/detail enrichment (addictions detail, languages,
price, email, phone, bed count) — so eligible facilities never appeared
unless a user manually clicked that button, even though keep/drop and
mandatory eligibility verification are supposed to be fully automatic.

These tests exercise the real run_census() orchestration end to end
(discovery seed -> keep/drop -> automatic mandatory eligibility
classification) with mocked AI providers — no live network, no paid calls —
and assert an eligible facility with missing optional fields (no phone, no
website, no bed count) appears in the accepted Phase 2 list and Excel
export WITHOUT ever calling the optional enrichment path.
"""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import (
    MapsCensusCell,
    MapsClientEligibility,
    MapsPlace,
)
from app.services.scraping.maps_census_service import maps_census_service
from app.services.scraping.maps_country_profile_service import maps_country_profile_service
from app.services.scraping.maps_export_service import ELIGIBLE_CENTERS_SHEET, maps_export_service
from app.services.scraping.maps_grid_planner import MapsGridCell
from app.services.scraping.maps_keep_drop_service import KeepDropDecision
from app.services.scraping.maps_structured_classification import (
    MapsStructuredClassificationResult,
    StructuredClassificationProviderResult,
)
from app.services.scraping.maps_primary_extraction import PrimaryExtractionEvidence

from tests.test_maps_census_service import _FakeGridPlanner, _FakePlacesClient, _create_run

KEEP_JSON = (
    '{"decision": "keep", "reason": "Private NGO residential addiction center",'
    ' "confidence": 0.95, "evidence": []}'
)
REVIEW_JSON = (
    '{"decision": "review", "reason": "Genuine addiction signal, residential evidence unconfirmed",'
    ' "confidence": 0.55, "evidence": []}'
)


@pytest.fixture(autouse=True)
def _stub_country_profile_stage(monkeypatch):
    async def _noop_build_profile(_db, *, run_id: str):
        del run_id
        return None

    monkeypatch.setattr(maps_country_profile_service, "build_profile_for_run", _noop_build_profile)


@pytest.fixture(autouse=True)
def _stub_optional_enrichment_enqueue(monkeypatch):
    """The OPTIONAL "Enrich with AI" auto-trigger must stay fully separate —
    stubbing it out proves mandatory eligibility classification does not
    depend on it ever firing."""

    async def _noop_enqueue(self, run_id: str) -> None:
        del run_id

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._maybe_enqueue_enrichment",
        _noop_enqueue,
    )


def _evidence(field: str, value: str) -> PrimaryExtractionEvidence:
    return PrimaryExtractionEvidence(
        field=field,
        value=value,
        quote=f"Evidence confirming {field}={value}",
        source_url="https://example.org/about",
        confidence=0.9,
    )


class _FakeStructuredClassificationProvider:
    """Returns a per-place-name-keyed structured classification result,
    with the evidence _enforce_field_evidence requires for any non-unknown
    field. ``calls`` records every place_payload passed in, so tests can
    assert the AI was called exactly once per place (idempotency)."""

    def __init__(self, results_by_name: dict[str, MapsStructuredClassificationResult]) -> None:
        self._results_by_name = results_by_name
        self.calls: list[str] = []

    async def classify_one(self, *, facility_payload, **_kwargs):
        name = facility_payload.get("name") or ""
        self.calls.append(name)
        output = self._results_by_name.get(name)
        if output is None:
            output = MapsStructuredClassificationResult()
        return StructuredClassificationProviderResult(output=output)


def _private_residential_addiction_result(**overrides) -> MapsStructuredClassificationResult:
    fields = dict(
        facility_type="residential_addiction_rehab",
        operator_type="nonprofit",
        ownership_status="confirmed_non_government",
        organization_scope="facility",
        addiction_treatment_mission=True,
        residential_accommodation=True,
        care_setting=["residential"],
        evidence=[
            _evidence("operator_type", "nonprofit"),
            _evidence("ownership_status", "confirmed_non_government"),
            _evidence("facility_type", "residential_addiction_rehab"),
            _evidence("organization_scope", "facility"),
        ],
    )
    fields.update(overrides)
    return MapsStructuredClassificationResult(**fields)


async def _run_with_keep_review(
    db,
    auth,
    monkeypatch,
    *,
    keep_name: str,
    review_name: str,
    results_by_name: dict,
    website_search_enabled: bool = False,
    search_provider_factory=None,
):
    # Skip real relevance classification and website search entirely — not
    # the focus of these tests, and leaving websites unset is exactly the
    # "missing optional field" scenario these tests verify.
    monkeypatch.setattr(
        get_settings(), "maps_census_website_search_enabled", website_search_enabled
    )
    if search_provider_factory is not None:
        monkeypatch.setattr(
            "app.services.scraping.maps_place_website_resolution.create_search_provider",
            search_provider_factory,
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

    run = await _create_run(db, auth)
    seed = [
        MapsGridCell(region_name="Finland", city_name="Helsinki", query_text=keep_name),
        MapsGridCell(region_name="Finland", city_name="Helsinki", query_text=review_name),
    ]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )

    from app.services.scraping.maps_places_client import PlaceResult

    client = _FakePlacesClient(
        by_query={
            keep_name: [
                PlaceResult(google_place_id="g1", raw_name=keep_name, formatted_address="x")
            ],
            review_name: [
                PlaceResult(google_place_id="g2", raw_name=review_name, formatted_address="x")
            ],
        }
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", lambda: client
    )

    async def fake_classify_keep_drop(_session, place, *, country_code, country_name):
        import json

        from app.services.scraping.maps_keep_drop_service import KeepDropDecision as _KDD

        if place.canonical_name == keep_name:
            return _KDD.model_validate(json.loads(KEEP_JSON)), "nano"
        return _KDD.model_validate(json.loads(REVIEW_JSON)), "nano"

    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.classify_place_keep_drop",
        fake_classify_keep_drop,
    )

    provider = _FakeStructuredClassificationProvider(results_by_name)
    monkeypatch.setattr(
        "app.services.scraping.maps_classification_service.create_structured_classification_provider",
        lambda: provider,
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None
    return run, provider


@pytest.mark.asyncio
async def test_eligible_facility_with_no_phone_or_website_appears_without_enrich_click(
    db, auth, monkeypatch
):
    """The critical regression: a private residential addiction center with
    no phone, no website, no bed count must automatically become ELIGIBLE
    and appear in the accepted list + Excel export — with the optional
    "Enrich with AI" auto-trigger stubbed out entirely (see fixture above),
    proving eligibility never depended on it."""
    keep_name = "Quiet Residential Rehab"
    review_name = "Uncertain Addiction Center"
    run, provider = await _run_with_keep_review(
        db,
        auth,
        monkeypatch,
        keep_name=keep_name,
        review_name=review_name,
        results_by_name={
            keep_name: _private_residential_addiction_result(),
            review_name: MapsStructuredClassificationResult(
                facility_type="unknown",
                organization_scope="unknown",
                ownership_status="ownership_unknown",
                addiction_treatment_mission=True,
                residential_accommodation=None,
            ),
        },
    )

    await db.refresh(run)
    detail = await maps_census_service.get_run(db, auth, run.id)
    listed = await maps_census_service.list_places(db, auth, run.id, accepted_only=True)
    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    exported_names = {
        row[0].value
        for row in load_workbook(BytesIO(content))[ELIGIBLE_CENTERS_SHEET].iter_rows(min_row=2)
        if row[0].value
    }

    await db.commit()
    place = (
        await db.execute(
            select(MapsPlace).where(MapsPlace.run_id == run.id, MapsPlace.canonical_name == keep_name)
        )
    ).scalar_one()
    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
    assert place.international_phone_number is None
    assert place.official_website is None
    assert place.bed_count is None

    assert detail.accepted_facilities_count == 1
    assert listed.meta.total == 1
    assert exported_names == {keep_name}
    assert detail.phase2_status == "completed"


@pytest.mark.asyncio
async def test_unconfigured_website_search_provider_does_not_block_mandatory_classification(
    db, auth, monkeypatch
):
    """Root cause fixed: with website search ENABLED (the real production
    setting) but the search provider unconfigured/failing (SERPER_API_KEY
    unset — the exact state of the Finland run 0afe4ca7-2dd3-47aa-a2cd-
    566e6b19b208's environment), classification must still run to
    completion using Google Maps metadata alone, instead of the provider's
    exception propagating out of _classify_place and permanently parking
    every website-less candidate at needs_review with zero classification
    fields after exhausting its retry attempts."""
    from app.services.scraping.search_providers.base import SearchProviderConfigurationError

    def _raise_unconfigured():
        raise SearchProviderConfigurationError("SERPER_API_KEY is not configured")

    keep_name = "No Search Provider Rehab"
    review_name = "No Search Provider Review Center"
    run, provider = await _run_with_keep_review(
        db,
        auth,
        monkeypatch,
        keep_name=keep_name,
        review_name=review_name,
        results_by_name={
            keep_name: _private_residential_addiction_result(),
            review_name: MapsStructuredClassificationResult(
                facility_type="unknown",
                organization_scope="unknown",
                ownership_status="ownership_unknown",
            ),
        },
        website_search_enabled=True,
        search_provider_factory=_raise_unconfigured,
    )

    await db.commit()
    place = (
        await db.execute(
            select(MapsPlace).where(MapsPlace.run_id == run.id, MapsPlace.canonical_name == keep_name)
        )
    ).scalar_one()
    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
    assert place.enrichment_pipeline_state != "needs_review"
    assert place.facility_type == "residential_addiction_rehab"
    assert place.enrichment_error_message is None


@pytest.mark.asyncio
async def test_review_candidate_subsequently_verified_eligible(db, auth, monkeypatch):
    """A REVIEW keep/drop decision whose mandatory evidence, once extracted,
    confirms ownership/residential/addiction criteria must itself reach
    ELIGIBLE — not be permanently capped at REVIEW just because keep/drop
    could not confirm residential evidence on its own."""
    keep_name = "Private Clinic A"
    review_name = "Nordic Recovery House"
    run, provider = await _run_with_keep_review(
        db,
        auth,
        monkeypatch,
        keep_name=keep_name,
        review_name=review_name,
        results_by_name={
            keep_name: _private_residential_addiction_result(),
            review_name: _private_residential_addiction_result(),
        },
    )

    await db.commit()
    place = (
        await db.execute(
            select(MapsPlace).where(
                MapsPlace.run_id == run.id, MapsPlace.canonical_name == review_name
            )
        )
    ).scalar_one()
    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
    assert place.keep_drop_decision == "review"


@pytest.mark.asyncio
async def test_candidate_still_lacking_residential_evidence_stays_review(db, auth, monkeypatch):
    """Unknown/unconfirmed residential evidence must result in REVIEW, never
    fabricated eligibility or silent exclusion."""
    keep_name = "Private Clinic B"
    review_name = "Addiction Support Center"
    run, provider = await _run_with_keep_review(
        db,
        auth,
        monkeypatch,
        keep_name=keep_name,
        review_name=review_name,
        results_by_name={
            keep_name: _private_residential_addiction_result(),
            # general_mental_health_clinic only reaches ELIGIBLE_FACILITY_TYPES
            # via confirmed residential evidence (see compute_client_eligibility) —
            # unlike residential_addiction_rehab, which is unconditionally in
            # that set. Leaving residential_accommodation/care_setting unknown
            # here genuinely tests "lacking residential evidence", rather than
            # a facility_type that implies residential regardless.
            review_name: _private_residential_addiction_result(
                facility_type="general_mental_health_clinic",
                residential_accommodation=None,
                care_setting=[],
                evidence=[
                    _evidence("operator_type", "nonprofit"),
                    _evidence("ownership_status", "confirmed_non_government"),
                    _evidence("facility_type", "general_mental_health_clinic"),
                    _evidence("organization_scope", "facility"),
                ],
            ),
        },
    )

    await db.commit()
    place = (
        await db.execute(
            select(MapsPlace).where(
                MapsPlace.run_id == run.id, MapsPlace.canonical_name == review_name
            )
        )
    ).scalar_one()
    assert place.client_eligibility in {
        MapsClientEligibility.REVIEW.value,
        MapsClientEligibility.EXCLUDED.value,
    }
    assert place.client_eligibility != MapsClientEligibility.ELIGIBLE.value


@pytest.mark.asyncio
async def test_government_inpatient_hospital_excluded_despite_residential_evidence(
    db, auth, monkeypatch
):
    keep_name = "City Rehab"
    review_name = "Regional Public Hospital"
    run, provider = await _run_with_keep_review(
        db,
        auth,
        monkeypatch,
        keep_name=keep_name,
        review_name=review_name,
        results_by_name={
            keep_name: _private_residential_addiction_result(),
            review_name: _private_residential_addiction_result(
                ownership_status="confirmed_government",
                evidence=[
                    _evidence("operator_type", "government_agency"),
                    _evidence("ownership_status", "confirmed_government"),
                    _evidence("facility_type", "residential_addiction_rehab"),
                    _evidence("organization_scope", "facility"),
                ],
            ),
        },
    )

    await db.commit()
    place = (
        await db.execute(
            select(MapsPlace).where(
                MapsPlace.run_id == run.id, MapsPlace.canonical_name == review_name
            )
        )
    ).scalar_one()
    assert place.client_eligibility == MapsClientEligibility.EXCLUDED.value


@pytest.mark.asyncio
async def test_mandatory_classification_is_idempotent_on_retry(db, auth, monkeypatch):
    """A second call for the same run_id (retry, concurrent job, or a later
    manual 'Enrich with AI' click that still calls classify_run internally)
    must find zero remaining candidates and make zero additional AI calls —
    never re-judge an already-classified place."""
    keep_name = "Solo Private Clinic"
    review_name = "Solo Review Clinic"
    run, provider = await _run_with_keep_review(
        db,
        auth,
        monkeypatch,
        keep_name=keep_name,
        review_name=review_name,
        results_by_name={
            keep_name: _private_residential_addiction_result(),
            review_name: _private_residential_addiction_result(),
        },
    )
    assert len(provider.calls) == 2  # exactly one AI call per candidate

    from app.services.scraping.maps_census_service import maps_census_service as _svc

    session_factory = _svc._session_factory(db)
    second_result = await _svc._run_mandatory_eligibility_classification(
        session_factory,
        run_id=run.id,
        country_code="FI",
        country_name="Finland",
        tracker=SimpleNamespace(
            add_places_request=lambda **_k: None,
            add_classifier_call=lambda **_k: None,
            add_primary_extraction_call=lambda **_k: None,
            add_enrichment_call=lambda **_k: None,
            add_crawl_request=lambda **_k: None,
            add_cost=lambda *_a, **_k: None,
            add_website_lookup_call=lambda **_k: None,
            add_sibling_location_call=lambda **_k: None,
            add_tokens=lambda *_a, **_k: None,
        ),
    )
    assert second_result.get("classified", 0) == 0
    assert len(provider.calls) == 2  # unchanged — no duplicate AI calls


@pytest.mark.asyncio
async def test_manual_exclusion_preserved_through_automatic_classification(db, auth, monkeypatch):
    """A facility an admin already marked manually excluded must stay
    excluded — automatic eligibility verification must never override a
    manual decision."""
    keep_name = "Manually Excluded Facility"
    review_name = "Unrelated Review Facility"

    monkeypatch.setattr(get_settings(), "maps_census_website_search_enabled", False)

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

    run = await _create_run(db, auth)
    seed = [
        MapsGridCell(region_name="Finland", city_name="Turku", query_text=keep_name),
        MapsGridCell(region_name="Finland", city_name="Turku", query_text=review_name),
    ]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed)
    )

    from app.services.scraping.maps_places_client import PlaceResult

    client = _FakePlacesClient(
        by_query={
            keep_name: [PlaceResult(google_place_id="g1", raw_name=keep_name, formatted_address="x")],
            review_name: [
                PlaceResult(google_place_id="g2", raw_name=review_name, formatted_address="x")
            ],
        }
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", lambda: client
    )

    async def fake_classify_keep_drop(_session, place, *, country_code, country_name):
        import json

        from app.services.scraping.maps_keep_drop_service import KeepDropDecision as _KDD

        return _KDD.model_validate(json.loads(KEEP_JSON)), "nano"

    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.classify_place_keep_drop",
        fake_classify_keep_drop,
    )

    provider = _FakeStructuredClassificationProvider(
        {
            keep_name: _private_residential_addiction_result(),
            review_name: _private_residential_addiction_result(),
        }
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_classification_service.create_structured_classification_provider",
        lambda: provider,
    )

    # Simulate an admin's manual exclusion landing between discovery and
    # keep/drop — the same unconditional write mark_excluded performs.
    import app.services.scraping.maps_keep_drop_service as _kds_module

    real_run_keep_drop_pass = _kds_module.run_keep_drop_pass

    async def fake_run_keep_drop_pass(session_factory, **kwargs):
        result = await real_run_keep_drop_pass(session_factory, **kwargs)
        async with session_factory() as session:
            excluded = (
                await session.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == kwargs["run_id"],
                        MapsPlace.canonical_name == keep_name,
                    )
                )
            ).scalar_one()
            excluded.client_eligibility = MapsClientEligibility.EXCLUDED.value
            excluded.manually_excluded_at = __import__("datetime").datetime.now(
                __import__("datetime").UTC
            )
            excluded.keep_drop_decision = "drop"
            await session.commit()
        return result

    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.run_keep_drop_pass",
        fake_run_keep_drop_pass,
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    await db.commit()
    place = (
        await db.execute(
            select(MapsPlace).where(MapsPlace.run_id == run.id, MapsPlace.canonical_name == keep_name)
        )
    ).scalar_one()
    assert place.client_eligibility == MapsClientEligibility.EXCLUDED.value
    assert place.manually_excluded_at is not None
    # The manually-excluded place's keep_drop_decision=drop means it is
    # never even selected for classification (build_classification_query
    # requires KEEP/REVIEW/ELIGIBLE) — the AI was never called for it.
    assert keep_name not in provider.calls
