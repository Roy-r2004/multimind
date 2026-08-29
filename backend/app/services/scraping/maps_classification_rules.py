"""Layer 1: deterministic Maps place classification from name and place types."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.db.models import (
    MapsClientEligibility,
    MapsFacilityType,
    MapsLifecycleStatus,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
)

_UNRELATED_NAME = re.compile(
    r"\b("
    r"nutrition|optique|pharmacie|parapharmacie|boulangerie|supermarket|supermarch|"
    r"hotel|hôtel|motel|gym|fitness|spa(?!\s*addict)|école|ecole|school|universit|"
    r"recyclage|plombier|electric|imprimerie|banque|assurance|garage|"
    r"physioth(?:erap|érap)|kinésith|kinesith|orthop(?:ed|éd)"
    r")\b",
    re.IGNORECASE,
)
_PUBLIC_NAME = re.compile(
    r"\b("
    r"hopital|hôpital|chu\b|epsp|ephs|etablissement public|établissement public|"
    r"ministère|ministere|direction de la sant|service de sant|"
    r"hospitalier public|centre hospitalier|psychiatrique universitaire"
    r")\b",
    re.IGNORECASE,
)
_INDIVIDUAL_NAME = re.compile(
    r"\b("
    r"\bdr\.?\s|\bdocteur\b|\bpr\.?\s|\bprof\.?\s|cabinet\b|"
    r"psychologue\b|psychiatre\b(?!\s*clinique)|addictologue\b|"
    r"praticien\b|consultation individuelle"
    r")\b",
    re.IGNORECASE,
)
_CESSATION_NAME = re.compile(
    r"\b("
    r"anti[- ]?tabac|anti tabac|laserostop|laser stop|sevrage tabac|stop smoking|"
    r"tabac info|anti nicotine"
    r")\b",
    re.IGNORECASE,
)
_ADDICTION_SIGNAL = re.compile(
    r"\b("
    r"toxicoman|toxicom|addictolog|narcolog|désintoxic|desintoxic|"
    r"addiction|rehab|recovery|sevrage|drogue|substance|"
    r"comportement.*addict|anti.?drog|lutte contre.*toxic|"
    r"centre.*addict|clinique.*addict|hébergement.*addict|hebergement.*addict"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DeterministicClassification:
    lifecycle_status: str
    client_eligibility: str
    facility_type: str | None = None
    ownership_status: str | None = None
    operator_type: str | None = None
    organization_scope: str | None = None
    addiction_focus_confirmed: bool | None = None
    rule_id: str = ""


def _name(place: Any) -> str:
    return ((getattr(place, "canonical_name", None) or getattr(place, "raw_name", None)) or "").strip()


def apply_deterministic_classification(place: Any) -> DeterministicClassification | None:
    """Return a confident bucket when name/types alone are enough."""
    name = _name(place)
    if not name:
        return None
    types = " ".join(getattr(place, "place_types", None) or []).casefold()

    if (_PUBLIC_NAME.search(name) or "hospital" in types and "government" in types) and not _ADDICTION_SIGNAL.search(name):
        return DeterministicClassification(
            lifecycle_status=MapsLifecycleStatus.CONFIRMED_PUBLIC.value,
            client_eligibility=MapsClientEligibility.EXCLUDED.value,
            facility_type=MapsFacilityType.GENERAL_MENTAL_HEALTH_CLINIC.value,
            ownership_status=MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value,
            operator_type=MapsOperatorType.PUBLIC_HOSPITAL.value,
            organization_scope=MapsOrganizationScope.FACILITY.value,
            addiction_focus_confirmed=False,
            rule_id="public_name_or_type",
        )

    if _INDIVIDUAL_NAME.search(name) and not _ADDICTION_SIGNAL.search(name):
        return DeterministicClassification(
            lifecycle_status=MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value,
            client_eligibility=MapsClientEligibility.EXCLUDED.value,
            facility_type=MapsFacilityType.INDIVIDUAL_ADDICTOLOGIST.value,
            ownership_status=MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value,
            operator_type=MapsOperatorType.INDIVIDUAL_PRACTICE.value,
            organization_scope=MapsOrganizationScope.INDIVIDUAL_PRACTICE.value,
            addiction_focus_confirmed=None,
            rule_id="individual_practice_name",
        )

    if _CESSATION_NAME.search(name):
        return DeterministicClassification(
            lifecycle_status=MapsLifecycleStatus.CONFIRMED_CESSATION_ONLY.value,
            client_eligibility=MapsClientEligibility.EXCLUDED.value,
            facility_type=MapsFacilityType.CESSATION_SERVICE.value,
            ownership_status=MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value,
            operator_type=MapsOperatorType.UNKNOWN.value,
            organization_scope=MapsOrganizationScope.FACILITY.value,
            addiction_focus_confirmed=False,
            rule_id="cessation_only_name",
        )

    if _UNRELATED_NAME.search(name) and not _ADDICTION_SIGNAL.search(name):
        return DeterministicClassification(
            lifecycle_status=MapsLifecycleStatus.UNRELATED.value,
            client_eligibility=MapsClientEligibility.EXCLUDED.value,
            facility_type=MapsFacilityType.UNRELATED.value,
            ownership_status=MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value,
            operator_type=MapsOperatorType.UNKNOWN.value,
            organization_scope=MapsOrganizationScope.UNKNOWN.value,
            addiction_focus_confirmed=False,
            rule_id="unrelated_name",
        )

    return None


__all__ = ["DeterministicClassification", "apply_deterministic_classification"]
