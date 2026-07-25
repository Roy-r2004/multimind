"""Phase E branch-safe facility identity decisions."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class BranchIdentityInput:
    canonical_name: str
    address: str | None = None
    postal_code: str | None = None
    phone_values: list[str] = field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    parent_org_name: str | None = None
    website_host: str | None = None


@dataclass(frozen=True)
class BranchIdentityResult:
    outcome: str
    reasons: list[str]
    should_merge: bool
    should_flag_possible_duplicate: bool


class BranchIdentityService:
    def compare(
        self,
        left: BranchIdentityInput,
        right: BranchIdentityInput,
        *,
        ai_recommendation: str | None = None,
    ) -> BranchIdentityResult:
        reasons: list[str] = []
        same_name = _norm(left.canonical_name) == _norm(right.canonical_name)
        same_address = bool(_norm(left.address) and _norm(left.address) == _norm(right.address))
        same_postal = bool(_norm(left.postal_code) and _norm(left.postal_code) == _norm(right.postal_code))
        phone_overlap = bool(_normalized_phones(left.phone_values) & _normalized_phones(right.phone_values))
        parent_match = _same_parent(left, right)
        coords_close = _coords_close(left, right)
        addresses_distinct = bool(
            _norm(left.address)
            and _norm(right.address)
            and _norm(left.address) != _norm(right.address)
        )

        if same_address:
            reasons.append("matching_address")
        if same_postal:
            reasons.append("matching_postal_code")
        if phone_overlap:
            reasons.append("overlapping_phone")
        if parent_match:
            reasons.append("matching_parent_signal")
        if coords_close:
            reasons.append("nearby_coordinates")

        if same_name and same_address and (phone_overlap or same_postal or coords_close):
            return _result("exact_same_facility", reasons)
        if same_name and (same_address or (phone_overlap and parent_match) or coords_close):
            return _result("probable_same", reasons)
        if same_name and parent_match and addresses_distinct:
            return _result("same_parent_different_branch", reasons + ["different_treatment_address"])
        if same_name and addresses_distinct and not phone_overlap and not parent_match:
            return _result("clearly_distinct", reasons + ["conflicting_branch_signals"])
        if same_name and not addresses_distinct and not reasons:
            return _result("exact_same_facility", ["matching_name_without_conflict"])
        if same_name and (phone_overlap or parent_match):
            return _result("possible_duplicate", reasons)
        if ai_recommendation in {"exact_same_facility", "probable_same"} and not addresses_distinct:
            return _result("probable_same", reasons + ["ai_recommendation_considered"])
        return _result("unresolved", reasons or ["insufficient_identity_signals"])


def _result(outcome: str, reasons: list[str]) -> BranchIdentityResult:
    return BranchIdentityResult(
        outcome=outcome,
        reasons=reasons,
        should_merge=outcome in {"exact_same_facility", "probable_same"},
        should_flag_possible_duplicate=outcome in {"possible_duplicate", "unresolved"},
    )


def _same_parent(left: BranchIdentityInput, right: BranchIdentityInput) -> bool:
    candidates = [
        (_norm(left.parent_org_name), _norm(right.parent_org_name)),
        (_norm(left.website_host), _norm(right.website_host)),
    ]
    return any(left_value and left_value == right_value for left_value, right_value in candidates)


def _normalized_phones(values: list[str]) -> set[str]:
    normalized: set[str] = set()
    for value in values:
        digits = re.sub(r"\D", "", value or "")
        if digits:
            normalized.add(digits)
    return normalized


def _coords_close(left: BranchIdentityInput, right: BranchIdentityInput) -> bool:
    if left.latitude is None or left.longitude is None or right.latitude is None or right.longitude is None:
        return False
    return math.hypot(left.latitude - right.latitude, left.longitude - right.longitude) <= 0.002


def _norm(value: str | None) -> str:
    return " ".join((value or "").split()).strip().casefold()


branch_identity_service = BranchIdentityService()
