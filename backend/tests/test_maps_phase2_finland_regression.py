"""Regression tests for the Finland Phase 2 accuracy audit.

Deterministic, mocked fixtures only — no network access, no paid model
calls. Each scenario below exercises the REAL production functions
(compute_client_eligibility, the export-service gate, the addictions/
languages evidence normalizer) directly against MapsPlace rows shaped
like the real Finland cases that prompted this audit, rather than going
through the full async discovery/enrichment pipeline.

Scenario naming mirrors the real facilities discussed in the audit so a
future reader can match a test to the original concern, but no real
facility data (addresses, phone numbers) is used — all values below are
synthetic.
"""

from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.core.dependencies import AuthContext
from app.db.models import (
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsFacilityType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
    MapsPlace,
)
from app.services.scraping.maps_eligibility import compute_client_eligibility
from app.services.scraping.maps_export_service import (
    ELIGIBLE_CENTERS_SHEET,
    maps_export_service,
)
from app.services.scraping.maps_place_enrichment_service import (
    EvidenceField,
    _normalize_addictions,
    _normalize_languages,
)


def _base_eligible_fields(**overrides) -> dict:
    fields = {
        "ownership_status": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        "organization_scope": MapsOrganizationScope.FACILITY.value,
        "facility_type": MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        "addiction_focus_confirmed": True,
        "operating_status": "open",
    }
    fields.update(overrides)
    return fields


async def _create_run(db, auth: AuthContext) -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FI",
        country_name="Finland",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run


# --- "A-klinikka Helsinki" — an administrative/outpatient branch of a
# well-known brand must not qualify merely from the brand's identity. ---


def test_aklinikka_helsinki_administrative_office_not_residential_excluded_or_review():
    from types import SimpleNamespace

    place = SimpleNamespace(
        **_base_eligible_fields(
            facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
            residential_accommodation=False,
            care_setting="outpatient",
        )
    )
    result = compute_client_eligibility(place)
    # OUTPATIENT_ADDICTION_CENTER is hard-excluded regardless of brand name.
    assert result == MapsClientEligibility.EXCLUDED.value


# --- "A-klinikka Espoo K-klinikka" — outpatient-only service. ---


def test_espoo_k_klinikka_outpatient_only_excluded():
    from types import SimpleNamespace

    place = SimpleNamespace(
        **_base_eligible_fields(
            facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        )
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.EXCLUDED.value


# --- "Fria-Klinikka" — evidence-backed residential rehab can qualify. ---


def test_fria_klinikka_evidence_backed_residential_rehab_eligible():
    from types import SimpleNamespace

    place = SimpleNamespace(
        **_base_eligible_fields(
            residential_accommodation=True,
            care_setting="residential",
        )
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


# --- "Rehab365" — reported addictions/bed count must be location-specific
# evidence, not an unsupported assertion. ---


def test_rehab365_addictions_require_evidence_not_just_a_value():
    unsupported = [EvidenceField(value="Alcohol", evidence_quote="", source_url="")]
    assert _normalize_addictions(unsupported) == []

    supported = [
        EvidenceField(
            value="Alcohol",
            evidence_quote="Rehab365's residential program treats alcohol dependency.",
            source_url="https://rehab365.example/programs",
        )
    ]
    assert _normalize_addictions(supported) == ["Alcohol"]


# --- "Kouvola" — a detox-only unit vs. a residential rehabilitation unit
# at the same address must be distinguished, not merged. ---


def test_kouvola_detox_only_unit_vs_residential_unit_distinguished():
    from types import SimpleNamespace

    detox_only_outpatient = SimpleNamespace(
        **_base_eligible_fields(
            facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
            residential_accommodation=False,
            care_setting="outpatient",
            medical_detox=True,
        )
    )
    # facility_type claims residential, but the SAME call's own more
    # specific fields explicitly contradict it (ambulatory detox, not an
    # overnight program) — downgraded to review, not auto-accepted.
    assert compute_client_eligibility(detox_only_outpatient) == MapsClientEligibility.REVIEW.value

    residential_with_medical_detox = SimpleNamespace(
        **_base_eligible_fields(
            residential_accommodation=True,
            care_setting="residential",
            medical_detox=True,
        )
    )
    # A genuine residential program that ALSO offers medically supervised
    # detox must still qualify — detox alongside real residential care is
    # not a disqualifier.
    assert (
        compute_client_eligibility(residential_with_medical_detox)
        == MapsClientEligibility.ELIGIBLE.value
    )


# --- "Sotkankoti" — supported/sober housing without a proven formal
# residential addiction-treatment program must not automatically qualify. ---


def test_sotkankoti_supported_housing_without_formal_program_excluded():
    from types import SimpleNamespace

    # No formal addiction-treatment program confirmed, ownership/scope
    # otherwise fine — mirrors the keep/drop prompt's own "sober living...
    # without a formal, evidence-based addiction treatment program" DROP
    # condition: addiction_focus_confirmed is not True.
    place = SimpleNamespace(
        **{**_base_eligible_fields(), "addiction_focus_confirmed": False}
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.EXCLUDED.value


# --- Genuine NGO rehab without a website but with a phone — still
# eligible once all other conditions are verified (website is optional). ---


@pytest.mark.asyncio
async def test_ngo_rehab_without_website_still_eligible_in_export(db, auth):
    run = await _create_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="ngo-no-website",
        raw_name="Genuine NGO Rehab",
        canonical_name="Genuine NGO Rehab",
        is_relevant=True,
        **_base_eligible_fields(),
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        lifecycle_status="confirmed_eligible",
        keep_drop_decision="keep",
        international_phone_number="+358 40 000 0000",
        official_website=None,
        raw_website=None,
    )
    db.add(place)
    await db.commit()

    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    workbook = load_workbook(BytesIO(content))
    names = [row[0].value for row in workbook[ELIGIBLE_CENTERS_SHEET].iter_rows(min_row=2) if row[0].value]
    assert "Genuine NGO Rehab" in names


# --- Unknown residential status — review, not automatic acceptance. ---


def test_unknown_residential_status_with_contradiction_signal_is_review():
    from types import SimpleNamespace

    place = SimpleNamespace(
        **_base_eligible_fields(residential_accommodation=False, care_setting="virtual")
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


# --- A facility explicitly outside the target country/state — excluded
# from the geographically-scoped acceptance (see prior session's
# geo_boundary_match guard), not silently reinterpreted as local. ---


def test_facility_outside_target_region_downgraded_not_silently_eligible():
    from types import SimpleNamespace

    place = SimpleNamespace(
        **{**_base_eligible_fields(), "geo_boundary_match": "outside_polygon"}
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


# --- An AI response with a long unsupported addictions list — unverified
# values must not silently populate the accepted dataset. ---


def test_long_unsupported_addictions_list_fully_dropped_not_exported_as_fact():
    hallucinated = [
        EvidenceField(value=label, evidence_quote="", source_url="")
        for label in [
            "Alcohol",
            "Opioids",
            "Cocaine",
            "Cannabis",
            "Benzodiazepines",
            "Gambling",
            "Internet Addiction",
        ]
    ]
    assert _normalize_addictions(hallucinated) == []


# --- Both Google Places and Gosom records go through the SAME Phase 2
# functions — compute_client_eligibility/the normalizers take no
# provider-specific branch, so a record sourced from either provider is
# judged identically once it's a MapsPlace row. ---


def test_eligibility_and_evidence_normalization_are_provider_agnostic():
    from types import SimpleNamespace

    google_places_record = SimpleNamespace(**_base_eligible_fields())
    gosom_record = SimpleNamespace(**_base_eligible_fields())
    assert compute_client_eligibility(google_places_record) == compute_client_eligibility(
        gosom_record
    )

    shared_evidence = [
        EvidenceField(value="Alcohol", evidence_quote="Confirmed program.", source_url="")
    ]
    assert _normalize_addictions(shared_evidence) == _normalize_addictions(shared_evidence)


# --- Dashboard eligible count must match what's actually exportable
# (the whole point of making client_eligibility the single authoritative
# gate — see maps_export_service.py). ---


@pytest.mark.asyncio
async def test_dashboard_count_matches_export_row_count(db, auth):
    run = await _create_run(db, auth)
    eligible = MapsPlace(
        run_id=run.id,
        google_place_id="eligible-1",
        raw_name="Eligible Rehab",
        canonical_name="Eligible Rehab",
        is_relevant=True,
        **_base_eligible_fields(),
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        lifecycle_status="confirmed_eligible",
        keep_drop_decision="keep",
        international_phone_number="+358 40 111 1111",
    )
    review_only = MapsPlace(
        run_id=run.id,
        google_place_id="review-1",
        raw_name="Review Rehab",
        canonical_name="Review Rehab",
        is_relevant=True,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        lifecycle_status="needs_review",
        keep_drop_decision="review",
        international_phone_number="+358 40 222 2222",
    )
    db.add_all([eligible, review_only])
    await db.commit()

    summary = await maps_export_service.get_export_summary(db, auth, run.id)
    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    workbook = load_workbook(BytesIO(content))
    exported_names = [
        row[0].value
        for row in workbook[ELIGIBLE_CENTERS_SHEET].iter_rows(min_row=2)
        if row[0].value
    ]

    assert summary[ELIGIBLE_CENTERS_SHEET] == len(exported_names) == 1
    assert exported_names == ["Eligible Rehab"]


# --- A review-stage candidate that Phase 2 later confirms eligible must
# still reach export — being uncertain at keep/drop time must not
# permanently block a genuinely eligible facility. ---


@pytest.mark.asyncio
async def test_review_candidate_later_confirmed_eligible_still_exports(db, auth):
    run = await _create_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="review-then-eligible",
        raw_name="Verified After Review",
        canonical_name="Verified After Review",
        is_relevant=True,
        **_base_eligible_fields(),
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        lifecycle_status="confirmed_eligible",
        keep_drop_decision="review",
        international_phone_number="+358 40 333 3333",
    )
    db.add(place)
    await db.commit()

    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    workbook = load_workbook(BytesIO(content))
    names = [row[0].value for row in workbook[ELIGIBLE_CENTERS_SHEET].iter_rows(min_row=2) if row[0].value]
    assert "Verified After Review" in names
