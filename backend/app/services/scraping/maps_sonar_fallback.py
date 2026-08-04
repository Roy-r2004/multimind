"""Sonar fallback for unresolved Maps enrichment records (one facility per call)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.llm.catalog import get_model
from app.llm.providers import get_provider_registry
from app.services.scraping.maps_enrichment_fetch import EnrichmentFetchError, cap_payload_excerpt
from app.services.scraping.maps_enrichment_response_parser import EnrichmentParseStats
from app.services.scraping.maps_place_enrichment_service import MapsPlaceEnrichmentResult

logger = logging.getLogger(__name__)

SONAR_FALLBACK_REASONS = frozenset(
    {
        "no_official_website",
        "insufficient_website_information",
        "ownership_unclear",
        "facility_type_unclear",
        "addiction_mission_unclear",
        "conflicting_sources",
        "primary_confidence_low",
        "needs_review_unresolved",
        "missing_contact_email",
    }
)


@dataclass
class SonarFallbackStats:
    sonar_calls: int = 0
    sonar_parse_failures: int = 0
    sonar_repair_attempts: int = 0
    sonar_repair_successes: int = 0
    sonar_final_failures: int = 0
    sonar_input_tokens: int = 0
    sonar_output_tokens: int = 0
    sonar_actual_cost: float = 0.0
    budget_exhausted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "sonar_calls": self.sonar_calls,
            "sonar_parse_failures": self.sonar_parse_failures,
            "sonar_repair_attempts": self.sonar_repair_attempts,
            "sonar_repair_successes": self.sonar_repair_successes,
            "sonar_final_failures": self.sonar_final_failures,
            "sonar_input_tokens": self.sonar_input_tokens,
            "sonar_output_tokens": self.sonar_output_tokens,
            "sonar_actual_cost": self.sonar_actual_cost,
            "budget_exhausted": self.budget_exhausted,
        }

    def merge(self, other: "SonarFallbackStats") -> "SonarFallbackStats":
        return SonarFallbackStats(
            sonar_calls=self.sonar_calls + other.sonar_calls,
            sonar_parse_failures=self.sonar_parse_failures + other.sonar_parse_failures,
            sonar_repair_attempts=self.sonar_repair_attempts + other.sonar_repair_attempts,
            sonar_repair_successes=self.sonar_repair_successes + other.sonar_repair_successes,
            sonar_final_failures=self.sonar_final_failures + other.sonar_final_failures,
            sonar_input_tokens=self.sonar_input_tokens + other.sonar_input_tokens,
            sonar_output_tokens=self.sonar_output_tokens + other.sonar_output_tokens,
            sonar_actual_cost=self.sonar_actual_cost + other.sonar_actual_cost,
            budget_exhausted=self.budget_exhausted or other.budget_exhausted,
        )


@dataclass
class SonarBudget:
    enabled: bool
    max_percent: float
    max_per_campaign: int
    selected_candidates: int
    calls_used: int = 0

    @property
    def max_calls(self) -> int:
        if not self.enabled:
            return 0
        percent_cap = int(self.selected_candidates * self.max_percent / 100.0)
        return min(self.max_per_campaign, max(percent_cap, 0))

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    def can_call(self) -> bool:
        return self.enabled and self.calls_used < self.max_calls


def sonar_fallback_reason(
    *,
    has_website: bool,
    primary_confidence: float | None,
    lifecycle_status: str,
    client_eligibility: str,
    facility_type: str | None,
    ownership_status: str | None,
    addiction_focus_confirmed: bool | None,
    has_contact_email: bool = True,
) -> str | None:
    if not has_website:
        return "no_official_website"
    if addiction_focus_confirmed is None:
        return "addiction_mission_unclear"
    if not facility_type or facility_type == "unknown":
        return "facility_type_unclear"
    if not ownership_status or ownership_status == "ownership_unknown":
        return "ownership_unclear"
    if primary_confidence is not None and primary_confidence < get_settings().maps_primary_extraction_confidence_threshold:
        return "primary_confidence_low"
    if lifecycle_status == "needs_review" and client_eligibility == "review":
        return "needs_review_unresolved"
    # Runs even for an already-eligible facility (bypasses the eligibility gate
    # in the caller) — classification can be fully resolved while contact info
    # is still missing, and finding an email is valuable independent of that.
    if not has_contact_email:
        return "missing_contact_email"
    return None


async def fetch_sonar_fallback_one(
    *,
    country_code: str,
    country_name: str,
    payload: dict[str, Any],
    parse_stats: EnrichmentParseStats,
    sonar_stats: SonarFallbackStats,
) -> MapsPlaceEnrichmentResult:
    from app.services.scraping.maps_enrichment_fetch import fetch_enrichment_batch

    settings = get_settings()
    model = get_model(settings.maps_census_enrichment_model)
    provider = get_provider_registry().get_provider(model.provider)
    capped = cap_payload_excerpt(
        payload,
        max_chars=max(1, int(settings.maps_census_enrichment_max_crawl_excerpt_chars)),
    )
    sonar_stats.sonar_calls += 1
    try:
        results = await fetch_enrichment_batch(
            provider,
            model_slug=model.provider_model,
            country_code=country_code,
            country_name=country_name,
            payloads=[capped],
            stats=parse_stats,
            max_tokens=4096,
        )
    except EnrichmentFetchError as exc:
        sonar_stats.sonar_final_failures += 1
        sonar_stats.sonar_parse_failures += parse_stats.parse_failures
        sonar_stats.sonar_repair_attempts += parse_stats.repair_attempts
        sonar_stats.sonar_repair_successes += parse_stats.repair_successes
        raise
    sonar_stats.sonar_parse_failures += parse_stats.parse_failures
    sonar_stats.sonar_repair_attempts += parse_stats.repair_attempts
    sonar_stats.sonar_repair_successes += parse_stats.repair_successes
    if not results:
        sonar_stats.sonar_final_failures += 1
        raise EnrichmentFetchError("sonar fallback returned no results")
    return results[0]


__all__ = [
    "SonarBudget",
    "SonarFallbackStats",
    "fetch_sonar_fallback_one",
    "sonar_fallback_reason",
]
