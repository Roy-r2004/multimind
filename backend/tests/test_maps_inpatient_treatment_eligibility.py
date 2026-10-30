"""Regression tests: confirmed inpatient addiction treatment must satisfy
the mandatory residential/inpatient-treatment requirement on its own,
without requiring an additional explicit "patients stay overnight"
statement — the same applies to confirmed residential or 24-hour care.

Root cause fixed: the structured AI classifier has always extracted an
``inpatient_treatment`` boolean, but it was discarded before ever reaching
``MapsPlace`` (no column existed, ``map_primary_to_enrichment_fields`` and
``_apply_structured_fields`` both silently dropped it), and
``compute_client_eligibility``'s ``confirmed_residential`` check only
recognized ``residential_accommodation`` and ``care_setting == "residential"``
— never ``care_setting == "inpatient"`` or a confirmed inpatient signal.
A facility with clear, credible inpatient-care evidence but no separate
"residential" wording was wrongly left at REVIEW/EXCLUDED.

Investigation note: live Finland data (run 0afe4ca7-2dd3-47aa-a2cd-
566e6b19b208) shows zero places with care_setting="inpatient" and no
inpatient_treatment column existed yet — this bug could not have produced
any of the run's 64 currently-unresolved cases (those are explained by the
two previously-fixed bugs: the SERPER crash and the source_url evidence
requirement). It may affect outcomes once those 19 classification_completed
places are genuinely reclassified with the fixed pipeline, but that has not
happened yet.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.db.models import (
    MapsClientEligibility,
    MapsFacilityType,
    MapsFundingType,
    MapsLifecycleStatus,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
)
from app.services.scraping.maps_eligibility import compute_client_eligibility
from app.services.scraping.maps_place_enrichment_service import (
    ClassificationEvidenceField,
    MapsPlaceEnrichmentResult,
    _apply_structured_fields,
)
from app.services.scraping.maps_primary_extraction import (
    MapsPrimaryExtractionResult,
    map_primary_to_enrichment_fields,
)


def make_place(**overrides):
    base = {
        "operating_status": "open",
        "lifecycle_status": MapsLifecycleStatus.DISCOVERED.value,
        "ownership_status": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        "operator_type": MapsOperatorType.NONPROFIT.value,
        "funding_type": MapsFundingType.UNKNOWN.value,
        "facility_type": MapsFacilityType.GENERAL_MENTAL_HEALTH_CLINIC.value,
        "organization_scope": MapsOrganizationScope.FACILITY.value,
        "addiction_focus_confirmed": True,
        "residential_accommodation": None,
        "inpatient_treatment": None,
        "care_setting": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# --- Confirmed inpatient alone satisfies the requirement ---


def test_confirmed_inpatient_treatment_alone_reaches_eligible():
    place = make_place(inpatient_treatment=True)
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


def test_care_setting_inpatient_alone_reaches_eligible():
    place = make_place(care_setting="inpatient")
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


def test_confirmed_residential_accommodation_still_reaches_eligible():
    """Unchanged behavior — residential alone was always sufficient."""
    place = make_place(residential_accommodation=True)
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


def test_care_setting_residential_still_reaches_eligible():
    place = make_place(care_setting="residential")
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


# --- Inherently-residential facility types: inpatient_treatment is not
# required at all (ELIGIBLE_FACILITY_TYPES already grants eligibility
# unconditionally) — but confirming it must never hurt. ---


def test_inpatient_detox_center_facility_type_eligible_without_inpatient_treatment_set():
    place = make_place(
        facility_type=MapsFacilityType.INPATIENT_DETOX_CENTER.value,
        inpatient_treatment=None,
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


# --- Must NOT auto-accept without evidence ---


def test_no_inpatient_or_residential_evidence_stays_review_not_auto_accepted():
    place = make_place(inpatient_treatment=None, residential_accommodation=None, care_setting=None)
    result = compute_client_eligibility(place)
    assert result != MapsClientEligibility.ELIGIBLE.value
    assert result == MapsClientEligibility.REVIEW.value


# --- Outpatient / day treatment / PHP must NOT be confused with inpatient ---


def test_explicit_outpatient_treatment_not_accepted_via_inpatient_path():
    place = make_place(inpatient_treatment=False, care_setting="outpatient")
    assert compute_client_eligibility(place) != MapsClientEligibility.ELIGIBLE.value


def test_outpatient_addiction_center_without_confirmed_inpatient_still_excluded():
    """Outpatient facility_type with no confirmed inpatient evidence stays
    excluded — only an explicit, confirmed inpatient_treatment=True signal
    overrides the category label (see test_maps_eligibility.py's mixed-
    setting regression tests for the override case itself; UNRELATED/
    CESSATION_SERVICE/HARM_REDUCTION_ONLY remain unconditionally excluded
    regardless of inpatient_treatment — those describe what a facility
    fundamentally is, not its care setting)."""
    place = make_place(
        facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        inpatient_treatment=None,
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.EXCLUDED.value


def test_unrelated_facility_type_excluded_even_with_confirmed_inpatient():
    place = make_place(
        facility_type=MapsFacilityType.UNRELATED.value,
        inpatient_treatment=True,
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.EXCLUDED.value


def test_internal_contradiction_inpatient_but_facility_type_says_outpatient_setting_stays_review():
    """facility_type alone implied residential (one of the three inherently
    residential types), but the SAME call's own finer fields explicitly
    say outpatient/virtual with residential_accommodation False — a real
    contradiction, not proof of eligibility, even if some other field
    claims inpatient. Unchanged guard."""
    place = make_place(
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        residential_accommodation=False,
        care_setting="outpatient",
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


# --- Mandatory requirements other than residential/inpatient are untouched ---


def test_government_operator_still_excluded_despite_confirmed_inpatient():
    place = make_place(
        inpatient_treatment=True,
        ownership_status=MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value,
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.EXCLUDED.value


def test_geo_outside_still_downgrades_despite_confirmed_inpatient():
    place = make_place(inpatient_treatment=True, geo_boundary_match="outside_bbox_fallback")
    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


# --- Structured-field mapping: inpatient_treatment must be persisted, not
# discarded like before this fix. ---


def test_apply_structured_fields_persists_inpatient_treatment():
    class _Place:
        pass

    place = _Place()
    result = MapsPlaceEnrichmentResult(
        place_id="p1",
        facility_type="general_mental_health_clinic",
        ownership_status="confirmed_non_government",
        operator_type="nonprofit",
        organization_scope="facility",
        addiction_focus_confirmed=True,
        inpatient_treatment=True,
        classification_evidence={
            "facility_type": ClassificationEvidenceField(
                value="general_mental_health_clinic",
                evidence_quote="Confirmed inpatient addiction program on-site.",
                source_url="",
            ),
            "inpatient_treatment": ClassificationEvidenceField(
                value=True,
                evidence_quote="The facility operates its own on-site inpatient ward.",
                source_url="",
            ),
        },
    )
    _apply_structured_fields(place, result)
    assert place.inpatient_treatment is True


def test_map_primary_to_enrichment_fields_includes_inpatient_treatment():
    result = MapsPrimaryExtractionResult(inpatient_treatment=True)
    mapped = map_primary_to_enrichment_fields(result)
    assert mapped["inpatient_treatment"] is True
