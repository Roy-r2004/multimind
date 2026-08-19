from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.db.models import (
    MapsClientEligibility,
    MapsFacilityType,
    MapsLifecycleStatus,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
)

ELIGIBLE_FACILITY_TYPES = {
    MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
    MapsFacilityType.INPATIENT_DETOX_CENTER.value,
    MapsFacilityType.THERAPEUTIC_COMMUNITY.value,
}

RELEVANT_LIFECYCLE_STATUSES = {
    MapsLifecycleStatus.PLAUSIBLE.value,
    MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value,
    MapsLifecycleStatus.PROBABLE_ELIGIBLE.value,
    MapsLifecycleStatus.NEEDS_REVIEW.value,
    MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value,
}

CONTRADICTED_LEGACY_LIFECYCLE_STATUSES = {
    MapsLifecycleStatus.CONFIRMED_PUBLIC.value,
    MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value,
    MapsLifecycleStatus.CONFIRMED_CESSATION_ONLY.value,
    MapsLifecycleStatus.CONTRADICTED.value,
    MapsLifecycleStatus.UNRELATED.value,
    MapsLifecycleStatus.DUPLICATE.value,
    MapsLifecycleStatus.PERMANENTLY_CLOSED.value,
}


def _field(place: Any, name: str) -> Any:
    if isinstance(place, Mapping):
        return place.get(name)
    return getattr(place, name, None)


def derive_is_relevant(lifecycle_status: str | None) -> bool:
    return lifecycle_status in RELEVANT_LIFECYCLE_STATUSES


def derive_legacy_verification_verdict(lifecycle_status: str | None) -> str:
    if lifecycle_status == MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value:
        return "confirmed"
    if lifecycle_status in CONTRADICTED_LEGACY_LIFECYCLE_STATUSES:
        return "contradicted"
    return "unknown"


def compute_client_eligibility(place: Any) -> str:
    operating_status = _field(place, "operating_status")
    lifecycle_status = _field(place, "lifecycle_status")
    ownership_status = _field(place, "ownership_status")
    operator_type = _field(place, "operator_type")
    facility_type = _field(place, "facility_type")
    organization_scope = _field(place, "organization_scope")
    addiction_focus_confirmed = _field(place, "addiction_focus_confirmed")

    if operating_status == "closed" or lifecycle_status == MapsLifecycleStatus.PERMANENTLY_CLOSED.value:
        return MapsClientEligibility.EXCLUDED.value

    if ownership_status == MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value:
        return MapsClientEligibility.EXCLUDED.value

    if operator_type in {
        MapsOperatorType.PUBLIC_HOSPITAL.value,
        MapsOperatorType.GOVERNMENT_AGENCY.value,
    }:
        return MapsClientEligibility.EXCLUDED.value

    if facility_type in {
        MapsFacilityType.UNRELATED.value,
        MapsFacilityType.CESSATION_SERVICE.value,
        MapsFacilityType.HARM_REDUCTION_ONLY.value,
        MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        MapsFacilityType.TELEHEALTH_ONLY.value,
        MapsFacilityType.SOBER_LIVING.value,
        MapsFacilityType.WELLNESS_COACHING_CENTER.value,
        MapsFacilityType.MENTAL_HEALTH_CLINIC.value,
    }:
        return MapsClientEligibility.EXCLUDED.value

    if organization_scope == MapsOrganizationScope.INDIVIDUAL_PRACTICE.value or facility_type in {
        MapsFacilityType.INDIVIDUAL_ADDICTOLOGIST.value,
        MapsFacilityType.THERAPIST_OR_COUNSELOR.value,
    }:
        return MapsClientEligibility.EXCLUDED.value

    if (
        ownership_status == MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value
        and organization_scope == MapsOrganizationScope.FACILITY.value
        and facility_type in ELIGIBLE_FACILITY_TYPES
        and addiction_focus_confirmed is True
    ):
        return MapsClientEligibility.ELIGIBLE.value

    if (
        ownership_status == MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value
        and addiction_focus_confirmed is True
    ):
        return MapsClientEligibility.REVIEW.value

    if (
        ownership_status == MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value
        and facility_type in ELIGIBLE_FACILITY_TYPES
    ):
        return MapsClientEligibility.REVIEW.value

    # Unresolved classification (unknown facility / ownership) stays in review so
    # Phase 2 detail enrichment can still research treatment fields. Do not treat
    # incomplete evidence as a hard exclusion.
    if facility_type in {None, "", MapsFacilityType.UNKNOWN.value} and ownership_status in {
        None,
        "",
        MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value,
        MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value,
    }:
        return MapsClientEligibility.REVIEW.value

    # Facilities classified as eligible types (residential rehab, inpatient detox,
    # therapeutic community) but with uncertain ownership get review status.
    # This increases coverage for official registries like FINESS where ownership
    # classification may be incomplete or inferred from facility type alone.
    if facility_type in ELIGIBLE_FACILITY_TYPES and ownership_status in {
        None,
        "",
        MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value,
        MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value,
    }:
        return MapsClientEligibility.REVIEW.value

    return MapsClientEligibility.EXCLUDED.value


def normalize_lifecycle_eligibility_consistency(place: Any) -> bool:
    """Fix known contradictory (lifecycle_status, client_eligibility) combinations.

    A provider/API failure or unresolved classification must never silently end
    up ``excluded`` — that hides real candidates from Needs Review. Returns True
    if the place's client_eligibility was changed.
    """
    lifecycle_status = _field(place, "lifecycle_status")
    client_eligibility = _field(place, "client_eligibility")

    # needs_review facilities are, by definition, still under consideration —
    # "excluded" here is always a stale/incorrect combination.
    if (
        lifecycle_status == MapsLifecycleStatus.NEEDS_REVIEW.value
        and client_eligibility == MapsClientEligibility.EXCLUDED.value
    ):
        if not isinstance(place, Mapping):
            place.client_eligibility = MapsClientEligibility.REVIEW.value
        return True
    return False


__all__ = [
    "ELIGIBLE_FACILITY_TYPES",
    "compute_client_eligibility",
    "derive_is_relevant",
    "derive_legacy_verification_verdict",
    "normalize_lifecycle_eligibility_consistency",
]
