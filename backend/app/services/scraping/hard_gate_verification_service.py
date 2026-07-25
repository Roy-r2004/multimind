"""Phase C hard-gate verification for publication safety."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


RESIDENTIAL_TERMS = (
    "residential",
    "inpatient",
    "live-in",
    "housing",
    "sober living",
    "therapeutic community",
)
OUTPATIENT_TERMS = (
    "outpatient",
    "day program",
    "day-program",
    "ambulatory",
)


@dataclass(frozen=True)
class HardGateEvidence:
    field_name: str
    raw_value: str | None = None
    evidence_quote: str | None = None


@dataclass(frozen=True)
class HardGateLocation:
    full_address: str | None
    country_containment_status: str
    location_completeness_status: str
    location_gap_reason: str | None = None


@dataclass(frozen=True)
class HardGateVerificationInput:
    target_country_code: str
    mission_profile: str = "full_national_census"
    facility_country_containment_status: str = "uncertain"
    facility_type: str | None = None
    locations: list[HardGateLocation] = field(default_factory=list)
    phone_values: list[str] = field(default_factory=list)
    verified_evidence_count: int = 0
    evidence: list[HardGateEvidence] = field(default_factory=list)


@dataclass(frozen=True)
class HardGateVerificationResult:
    publication_class: str
    mission_profile: str
    gate_results: dict[str, dict[str, Any]]
    contradictions: list[dict[str, Any]]

    def as_json(self) -> dict[str, Any]:
        return {
            "publication_class": self.publication_class,
            "mission_profile": self.mission_profile,
            "gate_results": self.gate_results,
            "contradictions": self.contradictions,
        }


class HardGateVerificationService:
    def evaluate(self, payload: HardGateVerificationInput) -> HardGateVerificationResult:
        mission_profile = _normalize_profile(payload.mission_profile)
        contradictions = _collect_contradictions(payload)
        gate_results: dict[str, dict[str, Any]] = {
            "physical_location_in_target_country": self._country_gate(payload),
            "location_and_phone_complete": self._location_phone_gate(payload),
        }
        if mission_profile == "private_residential":
            gate_results["private_residential_signal"] = self._private_residential_gate(payload)
        else:
            gate_results["full_national_census_corroboration"] = self._census_gate(payload)
        publication_class = _publication_class(gate_results, contradictions)
        return HardGateVerificationResult(
            publication_class=publication_class,
            mission_profile=mission_profile,
            gate_results=gate_results,
            contradictions=contradictions,
        )

    def location_snapshot(
        self,
        *,
        location: HardGateLocation,
        has_phone: bool,
    ) -> dict[str, Any]:
        location_payload = HardGateVerificationInput(
            target_country_code="XX",
            locations=[location],
            phone_values=["present"] if has_phone else [],
            facility_country_containment_status=location.country_containment_status,
            verified_evidence_count=1,
        )
        gate_results = {
            "physical_location_in_target_country": self._country_gate(location_payload),
            "location_and_phone_complete": self._location_phone_gate(location_payload),
        }
        return {"gate_results": gate_results}

    def _country_gate(self, payload: HardGateVerificationInput) -> dict[str, Any]:
        status = (payload.facility_country_containment_status or "uncertain").strip().lower()
        if status == "confirmed_target":
            return {"status": "passed", "reason": "contained_in_target_country"}
        if status == "confirmed_outside":
            return {"status": "failed", "reason": "contained_outside_target_country"}
        return {"status": "uncertain", "reason": "country_not_confirmed"}

    def _location_phone_gate(self, payload: HardGateVerificationInput) -> dict[str, Any]:
        has_phone = any((value or "").strip() for value in payload.phone_values)
        if not payload.locations:
            return {"status": "failed", "reason": "location_missing"}
        incomplete = [
            location
            for location in payload.locations
            if location.location_completeness_status != "complete"
        ]
        if not incomplete and has_phone:
            return {"status": "passed", "reason": "all_locations_have_address_and_phone"}
        if not has_phone:
            return {"status": "failed", "reason": "phone_missing"}
        if all(location.location_completeness_status == "unknown" for location in payload.locations):
            return {"status": "uncertain", "reason": "location_completeness_unknown"}
        gap_reasons = sorted(
            {
                location.location_gap_reason
                for location in payload.locations
                if location.location_gap_reason
            }
        )
        return {
            "status": "failed",
            "reason": gap_reasons[0] if gap_reasons else "location_incomplete",
        }

    def _private_residential_gate(self, payload: HardGateVerificationInput) -> dict[str, Any]:
        blob = " ".join(
            [
                payload.facility_type or "",
                *[
                    evidence.raw_value or ""
                    for evidence in payload.evidence
                    if evidence.field_name in {"facility_type", "programs", "services"}
                ],
            ]
        ).casefold()
        if any(term in blob for term in RESIDENTIAL_TERMS):
            return {"status": "passed", "reason": "residential_signal_detected"}
        if any(term in blob for term in OUTPATIENT_TERMS):
            return {"status": "uncertain", "reason": "outpatient_only_signal_detected"}
        return {"status": "uncertain", "reason": "residential_signal_missing"}

    def _census_gate(self, payload: HardGateVerificationInput) -> dict[str, Any]:
        if payload.verified_evidence_count >= 2:
            return {"status": "passed", "reason": "multiple_verified_citations"}
        if payload.verified_evidence_count == 1:
            return {"status": "uncertain", "reason": "single_verified_citation"}
        return {"status": "failed", "reason": "no_verified_citations"}


def _normalize_profile(value: str | None) -> str:
    normalized = (value or "full_national_census").strip().casefold()
    if normalized == "private_residential":
        return "private_residential"
    return "full_national_census"


def _publication_class(
    gate_results: dict[str, dict[str, Any]],
    contradictions: list[dict[str, Any]],
) -> str:
    country_gate = gate_results["physical_location_in_target_country"]["status"]
    if country_gate == "failed":
        return "excluded"
    statuses = {result["status"] for result in gate_results.values()}
    if "failed" in statuses or "uncertain" in statuses or contradictions:
        return "review_required"
    return "verified"


def _collect_contradictions(payload: HardGateVerificationInput) -> list[dict[str, Any]]:
    contradictions: list[dict[str, Any]] = []
    country_values = {
        _normalize_text(evidence.raw_value)
        for evidence in payload.evidence
        if evidence.field_name in {"country", "countries"} and _normalize_text(evidence.raw_value)
    }
    if len(country_values) > 1:
        contradictions.append(
            {
                "code": "conflicting_country_evidence",
                "values": sorted(country_values),
            }
        )
    # Multiple treatment addresses are normal branches, not contradictions.
    return contradictions


def _normalize_text(value: str | None) -> str:
    return " ".join((value or "").split()).strip().casefold()


hard_gate_verification_service = HardGateVerificationService()
