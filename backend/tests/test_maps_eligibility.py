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
    """Public FUNDING (how the facility is paid for) is not the same thing
    as public/government OWNERSHIP — a confirmed non-government nonprofit/
    association running a confirmed residential/inpatient program must not
    be disqualified merely because some of its funding is public."""
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        operator_type=MapsOperatorType.ASSOCIATION.value,
        funding_type=MapsFundingType.PUBLIC.value,
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
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


def test_outpatient_addiction_center_without_confirmed_inpatient_is_excluded():
    """Outpatient-only remains hard-excluded — it never satisfies the
    mandatory residential/inpatient-treatment requirement on its own."""
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.EXCLUDED.value


def test_outpatient_category_with_confirmed_inpatient_treatment_is_eligible():
    """Mixed-setting regression: a facility generally categorized as
    outpatient (e.g. a broad or stale Google Maps label) that ALSO has
    explicit, clear evidence of its own confirmed inpatient addiction-
    treatment program must be ELIGIBLE — the specific confirmed-inpatient
    signal overrides the general/incomplete category label, not the other
    way round."""
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
        inpatient_treatment=True,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


def test_outpatient_category_with_unconfirmed_inpatient_stays_excluded():
    """Merely offering SOME outpatient services alongside an unconfirmed/
    unknown inpatient program is not enough — inpatient_treatment must be
    explicitly True, not just absent evidence, to override the category."""
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
        inpatient_treatment=None,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.EXCLUDED.value


def test_outpatient_category_with_contradictory_inpatient_evidence_stays_review():
    """Contradictory evidence (one field confirms inpatient, another field
    from the SAME classification call explicitly says no overnight stay
    and an outpatient care setting) must never be automatically accepted —
    it is a genuine internal contradiction for a human to resolve, not
    proof of eligibility."""
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
        inpatient_treatment=True,
        residential_accommodation=False,
        care_setting="outpatient",
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


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


def test_psychiatric_without_addiction_focus_is_excluded():
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        facility_type=MapsFacilityType.PSYCHIATRIC_CLINIC_WITH_ADDICTION_PROGRAM.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=False,
    )

    assert compute_client_eligibility(place) == MapsClientEligibility.EXCLUDED.value


# --- Addiction treatment no longer has to be a facility's PRIMARY program
# (removed requirement): a general psychiatric/multidisciplinary facility
# is no longer hard-excluded purely for that label. ---


def test_psychiatric_with_addiction_focus_but_unconfirmed_residential_goes_to_review():
    """Genuine addiction-focus signal but no yet-confirmed inpatient/
    residential evidence is uncertain (review), never hard-excluded and
    never invented as eligible."""
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        facility_type=MapsFacilityType.GENERAL_MENTAL_HEALTH_CLINIC.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


def test_psychiatric_hospital_with_confirmed_residential_addiction_program_is_eligible():
    """A psychiatric hospital providing an actual inpatient/residential
    addiction program as a SECONDARY (not primary) program must qualify."""
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        facility_type=MapsFacilityType.GENERAL_MENTAL_HEALTH_CLINIC.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
        residential_accommodation=True,
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


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


# --- Residential/inpatient internal-contradiction guard: facility_type
# alone saying "residential" must not override the SAME classification
# call's own more specific residential_accommodation/care_setting fields
# when they explicitly disagree. Absence of these fields must never
# itself block eligibility (that would break every existing eligible case
# above, none of which set them). ---


def test_contradicting_residential_evidence_downgrades_to_review():
    place = _eligible_place(residential_accommodation=False, care_setting="outpatient")
    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


def test_contradicting_residential_evidence_virtual_care_setting_downgrades_to_review():
    place = _eligible_place(residential_accommodation=False, care_setting="virtual")
    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


def test_missing_residential_evidence_still_eligible():
    """No residential_accommodation/care_setting evidence at all (the
    normal case for most existing eligible facilities) must behave exactly
    as before this guard — facility_type + ownership + addiction_focus
    alone is still sufficient."""
    place = _eligible_place()
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


def test_residential_accommodation_true_stays_eligible():
    place = _eligible_place(residential_accommodation=True, care_setting="residential")
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


def test_medical_detox_alongside_real_residential_program_stays_eligible():
    """A residential program that ALSO offers medically supervised detox
    must never be penalized for it — only an explicit outpatient/virtual
    care_setting triggers the contradiction guard."""
    place = _eligible_place(
        residential_accommodation=True, care_setting="residential", medical_detox=True
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


def test_residential_accommodation_false_with_residential_care_setting_stays_eligible():
    """Only the specific outpatient/virtual care_setting values count as a
    genuine contradiction — an ambiguous/unrecognized care_setting value
    paired with residential_accommodation=False is not enough on its own
    to downgrade (conservative: avoid over-triggering on noisy AI output)."""
    place = _eligible_place(residential_accommodation=False, care_setting="mixed")
    assert compute_client_eligibility(place) == MapsClientEligibility.ELIGIBLE.value


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


def test_unresolved_facility_type_stays_review_even_with_confirmed_ownership():
    """Regression: confirming ownership (a positive fact) must never, by
    itself, make an unrelated and still-genuinely-unresolved fact
    (facility_type) look like a disqualification. A place with
    facility_type unknown/unresolved must stay REVIEW regardless of how
    confirmed ownership_status is — previously this fell through to
    EXCLUDED once ownership became confirmed_non_government, purely
    because the review catch-all only listed ownership_unknown/probable_
    non_government, not confirmed_non_government (government ownership
    is already hard-excluded earlier, so confirmed_non_government is the
    only other value reachable here)."""
    place = make_place(
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        facility_type=MapsFacilityType.UNKNOWN.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value
