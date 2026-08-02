"""Sonar web-search classification for unresolved Maps places (classify-only)."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import get_settings
from app.db.models import (
    MapsClientEligibility,
    MapsFacilityType,
    MapsLifecycleStatus,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
)
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import get_provider_registry
from app.services.scraping.maps_enrichment_fetch import EnrichmentFetchError, cap_payload_excerpt
from app.services.scraping.maps_enrichment_response_parser import (
    EnrichmentParseError,
    EnrichmentParseStats,
    extract_first_json_value,
    prepare_response_text,
    record_parse_failure,
)
from app.services.scraping.maps_place_enrichment_service import (
    ClassificationEvidenceField,
    MapsPlaceEnrichmentResult,
)

logger = logging.getLogger(__name__)

SONAR_CLASSIFY_MAX_TOKENS = 1024
VALID_BUCKETS = frozenset(
    {"eligible_candidate", "needs_review", "public", "individual", "unrelated"}
)
VALID_MISSIONS = frozenset({"confirmed", "contradicted", "unknown"})


class SonarClassifyEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quote: str = Field(default="", max_length=240)
    source_url: str = Field(default="", max_length=1000)


class SonarClassifyResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    classification_bucket: str = Field(default="needs_review", max_length=40)
    facility_type: str | None = None
    operator_type: str | None = None
    addiction_treatment_mission: str = Field(default="unknown", max_length=20)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=400)
    evidence: list[SonarClassifyEvidence] = Field(default_factory=list, max_length=8)


def needs_sonar_classification(place: Any) -> bool:
    """True when structured classification left important fields unresolved."""
    from app.services.scraping.maps_enrichment_selection import CONFIDENT_SKIP_LIFECYCLE

    lifecycle = getattr(place, "lifecycle_status", None) or ""
    if lifecycle in CONFIDENT_SKIP_LIFECYCLE:
        return False

    facility_type = getattr(place, "facility_type", None) or ""
    ownership = getattr(place, "ownership_status", None) or ""
    addiction_focus = getattr(place, "addiction_focus_confirmed", None)
    confidence = getattr(place, "classification_confidence", None)
    threshold = get_settings().maps_primary_extraction_confidence_threshold

    if not facility_type or facility_type == "unknown":
        return True
    if not ownership or ownership == "ownership_unknown":
        return True
    if addiction_focus is None:
        return True
    if confidence is not None and confidence < threshold:
        return True
    return False


def parse_sonar_classify_response(text: str) -> SonarClassifyResult:
    prepared = prepare_response_text(text)
    snippet = extract_first_json_value(prepared)
    payload = json.loads(snippet)
    if isinstance(payload, dict) and "results" in payload and isinstance(payload["results"], list):
        if not payload["results"]:
            raise EnrichmentParseError("sonar classify results empty")
        payload = payload["results"][0]
    if not isinstance(payload, dict):
        raise EnrichmentParseError("sonar classify root must be an object")
    result = SonarClassifyResult.model_validate(payload)
    bucket = (result.classification_bucket or "").strip().casefold()
    if bucket not in VALID_BUCKETS:
        result.classification_bucket = "needs_review"
    else:
        result.classification_bucket = bucket
    mission = (result.addiction_treatment_mission or "").strip().casefold()
    result.addiction_treatment_mission = mission if mission in VALID_MISSIONS else "unknown"
    return result


def map_sonar_classify_to_enrichment(
    result: SonarClassifyResult,
    *,
    place_id: str,
) -> MapsPlaceEnrichmentResult:
    bucket = result.classification_bucket
    mission = result.addiction_treatment_mission
    addiction_focus = {
        "confirmed": True,
        "contradicted": False,
        "unknown": None,
    }.get(mission)

    if bucket == "public":
        facility_type = result.facility_type or MapsFacilityType.GENERAL_MENTAL_HEALTH_CLINIC.value
        operator_type = result.operator_type or MapsOperatorType.PUBLIC_HOSPITAL.value
        ownership = MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value
        organization_scope = MapsOrganizationScope.FACILITY.value
    elif bucket == "individual":
        facility_type = result.facility_type or MapsFacilityType.INDIVIDUAL_ADDICTOLOGIST.value
        operator_type = result.operator_type or MapsOperatorType.INDIVIDUAL_PRACTICE.value
        ownership = MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value
        organization_scope = MapsOrganizationScope.INDIVIDUAL_PRACTICE.value
    elif bucket == "unrelated":
        facility_type = result.facility_type or MapsFacilityType.UNRELATED.value
        operator_type = result.operator_type or MapsOperatorType.UNKNOWN.value
        ownership = MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value
        organization_scope = MapsOrganizationScope.UNKNOWN.value
        addiction_focus = False if addiction_focus is None else addiction_focus
    elif bucket == "eligible_candidate":
        facility_type = result.facility_type or MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value
        operator_type = result.operator_type or MapsOperatorType.NONPROFIT.value
        ownership = MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value
        organization_scope = MapsOrganizationScope.FACILITY.value
        if addiction_focus is None:
            addiction_focus = True
    else:
        facility_type = result.facility_type or "unknown"
        operator_type = result.operator_type or "unknown"
        ownership = MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value
        organization_scope = MapsOrganizationScope.UNKNOWN.value

    evidence_map: dict[str, ClassificationEvidenceField] = {}
    for item in result.evidence:
        if not item.quote.strip() or not item.source_url.strip():
            continue
        evidence_map.setdefault(
            "facility_type",
            ClassificationEvidenceField(
                value=facility_type,
                confidence=result.confidence,
                evidence_quote=item.quote[:240],
                source_url=item.source_url[:1000],
                source_type="web_search",
            ),
        )
    if result.reason:
        evidence_map["classification_bucket"] = ClassificationEvidenceField(
            value=bucket,
            confidence=result.confidence,
            evidence_quote=result.reason[:240],
            source_url=(result.evidence[0].source_url if result.evidence else "")[:1000],
            source_type="web_search",
        )

    return MapsPlaceEnrichmentResult(
        place_id=place_id,
        operator_type=operator_type,
        ownership_status=ownership,
        facility_type=facility_type,
        organization_scope=organization_scope,
        addiction_focus_confirmed=addiction_focus,
        operating_status="unknown",
        classification_evidence=evidence_map,
        classification_confidence=result.confidence,
        addictions_treated=[],
        languages_spoken=[],
    )


def apply_sonar_bucket_lifecycle(place: Any, bucket: str) -> None:
    """Apply lifecycle/eligibility from Sonar classification bucket."""
    if bucket == "public":
        place.lifecycle_status = MapsLifecycleStatus.CONFIRMED_PUBLIC.value
        place.client_eligibility = MapsClientEligibility.EXCLUDED.value
    elif bucket == "individual":
        place.lifecycle_status = MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value
        place.client_eligibility = MapsClientEligibility.EXCLUDED.value
    elif bucket == "unrelated":
        place.lifecycle_status = MapsLifecycleStatus.UNRELATED.value
        place.client_eligibility = MapsClientEligibility.EXCLUDED.value
    elif bucket == "eligible_candidate":
        place.lifecycle_status = MapsLifecycleStatus.PROBABLE_ELIGIBLE.value
        place.client_eligibility = MapsClientEligibility.REVIEW.value
    else:
        place.lifecycle_status = MapsLifecycleStatus.NEEDS_REVIEW.value
        place.client_eligibility = MapsClientEligibility.REVIEW.value


async def fetch_sonar_classify_one(
    *,
    country_code: str,
    country_name: str,
    payload: dict[str, Any],
    parse_stats: EnrichmentParseStats,
) -> tuple[MapsPlaceEnrichmentResult, SonarClassifyResult]:
    settings = get_settings()
    model = get_model(settings.maps_census_enrichment_model)
    provider = get_provider_registry().get_provider(model.provider)
    capped = cap_payload_excerpt(
        payload,
        max_chars=max(1, int(settings.maps_census_enrichment_max_crawl_excerpt_chars)),
    )
    prompt = get_prompt_engine().render(
        "scraping/maps_sonar_classifier.j2",
        country_code=(country_code or "XX")[:2].upper(),
        country_name=(country_name or "Unknown")[:120],
        facility_json=json.dumps(capped, ensure_ascii=False),
    )
    try:
        response = await provider.complete(
            system=(
                "You have live web search. Classify one addiction-treatment facility. "
                "Return a single compact JSON object only — no markdown, no results array."
            ),
            user=prompt,
            model=model.provider_model,
            max_tokens=SONAR_CLASSIFY_MAX_TOKENS,
        )
    except Exception as exc:
        raise EnrichmentFetchError(str(exc)) from exc

    text = response.text or ""
    try:
        classified = parse_sonar_classify_response(text)
    except (json.JSONDecodeError, ValidationError, EnrichmentParseError, ValueError) as exc:
        record_parse_failure(parse_stats, error=exc, raw_text=text, attempt="sonar_classify")
        raise EnrichmentFetchError(str(exc), raw_excerpt=text[:500]) from exc

    place_id = str(payload.get("place_id") or "")
    mapped = map_sonar_classify_to_enrichment(classified, place_id=place_id)
    return mapped, classified


__all__ = [
    "SONAR_CLASSIFY_MAX_TOKENS",
    "SonarClassifyResult",
    "apply_sonar_bucket_lifecycle",
    "fetch_sonar_classify_one",
    "map_sonar_classify_to_enrichment",
    "needs_sonar_classification",
    "parse_sonar_classify_response",
]
