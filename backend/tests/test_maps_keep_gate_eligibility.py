"""The strict keep/drop gate decides eligibility, as in the Google Places runs.

Phase 2 can only remove a kept place on hard negative evidence, or send it to
a human when it contradicts itself or sits outside the run's geography.
Missing Phase 2 evidence never demotes a kept place.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.db.models import (
    MapsClientEligibility,
    MapsFacilityType,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
)
from app.services.scraping.maps_eligibility import compute_client_eligibility

ELIGIBLE = MapsClientEligibility.ELIGIBLE.value
REVIEW = MapsClientEligibility.REVIEW.value
EXCLUDED = MapsClientEligibility.EXCLUDED.value


def _kept(**fields):
    return {"keep_drop_decision": "keep", "addiction_focus_confirmed": True, **fields}


def test_kept_place_without_phase2_evidence_is_eligible():
    assert compute_client_eligibility(_kept()) == ELIGIBLE


def test_kept_place_with_unknown_ownership_and_type_stays_eligible():
    place = _kept(
        ownership_status=MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value,
        facility_type=MapsFacilityType.UNKNOWN.value,
    )
    assert compute_client_eligibility(place) == ELIGIBLE


@pytest.mark.parametrize(
    "fields",
    [
        {"operating_status": "closed"},
        {"ownership_status": MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value},
        {"operator_type": MapsOperatorType.PUBLIC_HOSPITAL.value},
        {"facility_type": MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value},
        {"facility_type": MapsFacilityType.CESSATION_SERVICE.value},
        {"facility_type": MapsFacilityType.UNRELATED.value},
        {"organization_scope": MapsOrganizationScope.INDIVIDUAL_PRACTICE.value},
        {"manually_excluded_at": datetime.now(UTC)},
    ],
)
def test_hard_negative_evidence_excludes_a_kept_place(fields):
    assert compute_client_eligibility(_kept(**fields)) == EXCLUDED


@pytest.mark.parametrize(
    "fields",
    [
        {"geo_boundary_match": "outside_polygon"},
        {"geo_boundary_match": "outside_bbox_fallback"},
        {"residential_accommodation": False, "care_setting": "outpatient"},
        {
            "facility_type": MapsFacilityType.GENERAL_MENTAL_HEALTH_CLINIC.value,
            "addiction_focus_confirmed": False,
        },
    ],
)
def test_contradiction_or_out_of_area_sends_kept_place_to_review(fields):
    assert compute_client_eligibility(_kept(**fields)) == REVIEW


def test_place_not_kept_still_needs_positive_phase2_evidence():
    assert compute_client_eligibility({"keep_drop_decision": "review"}) == REVIEW
