from types import SimpleNamespace

import pytest

from app.db.models import (
    MapsClientEligibility,
    MapsFacilityType,
    MapsFundingType,
    MapsLifecycleStatus,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
)
from app.services.scraping.maps_eligibility import (
    compute_client_eligibility,
    derive_is_relevant,
    derive_legacy_verification_verdict,
)


def make_place(**overrides):
    base = {
        "operating_status": "open",
        "lifecycle_status": MapsLifecycleStatus.DISCOVERED.value,
        "ownership_status": MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value,
        "operator_type": MapsOperatorType.UNKNOWN.value,
        "funding_type": MapsFundingType.UNKNOWN.value,
        "facility_type": MapsFacilityType.UNKNOWN.value,
        "organization_scope": MapsOrganizationScope.UNKNOWN.value,
        "addiction_focus_confirmed": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_association_with_public_funding_can_still_be_eligible():
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        operator_type=MapsOperatorType.ASSOCIATION.value,
        funding_type=MapsFundingType.PUBLIC.value,
        facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


def test_public_hospital_is_excluded():
    place = make_place(
        ownership_status=MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value,
        operator_type=MapsOperatorType.PUBLIC_HOSPITAL.value,
        facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.EXCLUDED.value


def test_outpatient_addiction_center_can_be_eligible():
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


def test_individual_practitioner_is_excluded_as_center():
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        operator_type=MapsOperatorType.INDIVIDUAL_PRACTICE.value,
        facility_type=MapsFacilityType.INDIVIDUAL_ADDICTOLOGIST.value,
        organization_scope=MapsOrganizationScope.INDIVIDUAL_PRACTICE.value,
        addiction_focus_confirmed=True,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.EXCLUDED.value


@pytest.mark.parametrize(
    "facility_type",
    [
        MapsFacilityType.CESSATION_SERVICE.value,
        MapsFacilityType.HARM_REDUCTION_ONLY.value,
    ],
)
def test_cessation_or_laser_style_services_are_excluded(facility_type: str):
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        facility_type=facility_type,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.EXCLUDED.value


@pytest.mark.parametrize(
    ("facility_type", "addiction_focus_confirmed"),
    [
        (MapsFacilityType.GENERAL_MENTAL_HEALTH_CLINIC.value, True),
        (MapsFacilityType.PSYCHIATRIC_CLINIC_WITH_ADDICTION_PROGRAM.value, False),
    ],
)
def test_psychiatric_without_addiction_is_excluded(
    facility_type: str, addiction_focus_confirmed: bool
):
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        facility_type=facility_type,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=addiction_focus_confirmed,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.EXCLUDED.value


def test_residential_ngo_is_eligible():
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        operator_type=MapsOperatorType.NONPROFIT.value,
        funding_type=MapsFundingType.DONATION_BASED.value,
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


# --- Geographic result validation guard: an otherwise-eligible facility
# whose coordinates are VERIFIED to be outside the run's selected polygon
# must never reach "eligible" on business classification alone. Business
# inputs are untouched by this guard — only the final ELIGIBLE outcome is
# downgraded to REVIEW (never excluded, never applied when coordinates or
# a polygon are missing). ---


def _eligible_place(**overrides):
    return make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        operator_type=MapsOperatorType.NONPROFIT.value,
        funding_type=MapsFundingType.DONATION_BASED.value,
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
        **overrides,
    )


def test_outside_polygon_downgrades_eligible_to_review_not_excluded():
    place = _eligible_place(geo_boundary_match="outside_polygon")
    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


def test_inside_polygon_stays_eligible():
    place = _eligible_place(geo_boundary_match="inside_polygon")
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


def test_no_coordinates_never_blocked_by_geo_guard():
    """A facility the provider gave no coordinates for must never be
    penalized — missing data is not evidence of being outside the region."""
    place = _eligible_place(geo_boundary_match="no_coordinates")
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


def test_no_polygon_available_never_blocked_by_geo_guard():
    """No cached dataset boundary for this run (the common case today)
    must never be treated as if the facility were out of scope."""
    place = _eligible_place(geo_boundary_match="no_polygon_available")
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


def test_missing_geo_boundary_match_field_never_blocked_by_geo_guard():
    """Rows discovered before this field existed (or any place-like object
    that simply doesn't carry it) must behave exactly as before this
    guard was added."""
    place = _eligible_place()  # no geo_boundary_match attribute at all
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


def test_outside_polygon_does_not_change_an_already_excluded_outcome():
    """The guard only ever downgrades what would have been ELIGIBLE — it
    must never upgrade or otherwise alter an EXCLUDED business-classification
    outcome."""
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value,
        geo_boundary_match="outside_polygon",
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.EXCLUDED.value


def test_probable_non_government_with_addiction_focus_goes_to_review_without_scope_or_type_gate():
    place = make_place(
        ownership_status=MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value,
        facility_type=MapsFacilityType.UNKNOWN.value,
        organization_scope=MapsOrganizationScope.UNKNOWN.value,
        addiction_focus_confirmed=True,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


def test_ownership_unknown_eligible_facility_goes_to_review():
    place = make_place(
        ownership_status=MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value,
        facility_type=MapsFacilityType.INPATIENT_DETOX_CENTER.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


def test_ownership_unknown_eligible_facility_goes_to_review_without_scope_gate():
    place = make_place(
        ownership_status=MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value,
        facility_type=MapsFacilityType.INPATIENT_DETOX_CENTER.value,
        organization_scope=MapsOrganizationScope.UNKNOWN.value,
        addiction_focus_confirmed=None,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


@pytest.mark.parametrize(
    ("lifecycle_status", "expected"),
    [
        (MapsLifecycleStatus.DISCOVERED.value, False),
        (MapsLifecycleStatus.PLAUSIBLE.value, True),
        (MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value, True),
        (MapsLifecycleStatus.PROBABLE_ELIGIBLE.value, True),
        (MapsLifecycleStatus.NEEDS_REVIEW.value, True),
        (MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value, True),
        (MapsLifecycleStatus.CONFIRMED_PUBLIC.value, False),
        (MapsLifecycleStatus.UNRELATED.value, False),
        (MapsLifecycleStatus.PERMANENTLY_CLOSED.value, False),
    ],
)
def test_derive_is_relevant_matches_backward_compatibility(
    lifecycle_status: str, expected: bool
):
    assert derive_is_relevant(lifecycle_status) is expected


@pytest.mark.parametrize(
    ("lifecycle_status", "expected"),
    [
        (MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value, "confirmed"),
        (MapsLifecycleStatus.PLAUSIBLE.value, "unknown"),
        (MapsLifecycleStatus.PROBABLE_ELIGIBLE.value, "unknown"),
        (MapsLifecycleStatus.NEEDS_REVIEW.value, "unknown"),
        (MapsLifecycleStatus.CONFIRMED_PUBLIC.value, "contradicted"),
        (MapsLifecycleStatus.CONFIRMED_CESSATION_ONLY.value, "contradicted"),
        (MapsLifecycleStatus.CONTRADICTED.value, "contradicted"),
        (MapsLifecycleStatus.UNRELATED.value, "contradicted"),
    ],
)
def test_derive_legacy_verification_verdict_maps_lifecycle_status(
    lifecycle_status: str, expected: str
):
    assert derive_legacy_verification_verdict(lifecycle_status) == expected
