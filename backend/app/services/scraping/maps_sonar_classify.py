"""Sonar web-search classification for unresolved Maps places (classify-only)."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ConfigDict, Field, ValidationError

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
from app.services.scraping.maps_evidence_normalization import (
    contains_hedge_language,
    is_likely_branch_specific_evidence,
    is_name_or_domain_inference_only,
)
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
from app.services.scraping.maps_pydantic_utils import TruncatingModel

logger = logging.getLogger(__name__)

SONAR_CLASSIFY_MAX_TOKENS = 1024
VALID_BUCKETS = frozenset(
    {"eligible_candidate", "needs_review", "public", "individual", "unrelated"}
)
VALID_MISSIONS = frozenset({"confirmed", "contradicted", "unknown"})
# Sonar's own explicit ownership assessment — previously there was no such
# field at all, and map_sonar_classify_to_enrichment unconditionally
# hardcoded PROBABLE_NON_GOVERNMENT for every eligible_candidate result
# regardless of how strong the underlying evidence actually was (confirmed
# root cause of Belarus run 5acff6f7's facility 32e01f47: an explicitly
# named registered NGO operator could never reach CONFIRMED_NON_GOVERNMENT
# via this path). ``resolve_sonar_ownership_status`` below only trusts
# "confirmed_*" once it independently clears the SAME hedge-language,
# name/domain-inference-only, and branch-specific-identity guards every
# other "confirmed" ownership fact in this codebase must clear.
VALID_OWNERSHIP_ASSESSMENTS = frozenset(
    {"confirmed_non_government", "probable_non_government", "confirmed_government", "unknown"}
)
_KNOWN_EVIDENCE_FIELDS = frozenset(
    {
        "facility_type",
        "ownership_status",
        "inpatient_treatment",
        "residential_accommodation",
        "medical_detox",
        "classification_bucket",
    }
)


class SonarClassifyEvidence(TruncatingModel):
    model_config = ConfigDict(extra="ignore")

    # Which field this item supports — "facility_type", "inpatient_treatment",
    # "residential_accommodation", "medical_detox", or "classification_bucket".
    # Without this, every evidence item used to be attributed to
    # "facility_type" regardless of what it was actually about (see the old
    # map_sonar_classify_to_enrichment), so inpatient/residential claims
    # could never carry their own evidence at all.
    field: str = Field(default="facility_type", max_length=40)
    quote: str = Field(default="", max_length=240)
    source_url: str = Field(default="", max_length=1000)


class SonarClassifyResult(TruncatingModel):
    model_config = ConfigDict(extra="ignore")

    classification_bucket: str = Field(default="needs_review", max_length=40)
    facility_type: str | None = None
    operator_type: str | None = None
    # Sonar's own explicit ownership determination — see
    # VALID_OWNERSHIP_ASSESSMENTS. Attribute name matches the evidence
    # field key "ownership_status" exactly (map_sonar_classify_to_enrichment
    # resolves an evidence item's value via getattr(result, field_name)).
    ownership_status: str = Field(default="unknown", max_length=40)
    addiction_treatment_mission: str = Field(default="unknown", max_length=20)
    # Research targets this fallback exists to resolve when structured
    # classification (crawled official-site evidence) left them unresolved —
    # see needs_sonar_classification. Each is only trusted when backed by a
    # same-named SonarClassifyEvidence item with a verifiable source_url
    # (see maps_sonar_citation_verification); otherwise it stays unresolved,
    # exactly like any other missing evidence — never forced to a value.
    inpatient_treatment: bool | None = None
    residential_accommodation: bool | None = None
    medical_detox: bool | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=400)
    evidence: list[SonarClassifyEvidence] = Field(default_factory=list, max_length=12)


def needs_sonar_classification(place: Any) -> bool:
    """True when structured classification left important fields unresolved.

    Includes inpatient_treatment/residential_accommodation specifically —
    confirmed root cause of Belarus run 5acff6f7-21f8-4427-88a1-38d0a394ce83
    (Phase 2 Eligible stuck at 0): candidates whose official site was never
    crawled (or didn't say enough) had facility_type/ownership/
    addiction_focus resolved from Maps metadata alone, so this gate never
    fired even though the two fields that actually decide eligibility were
    still unresolved. A confirmed OUTPATIENT-ONLY determination
    (``outpatient_treatment is True`` with both of these already resolved
    to ``False``) is correctly excluded below — this fallback is for
    genuinely missing evidence, not for re-litigating an already-settled
    outpatient-only verdict.
    """
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

    inpatient = getattr(place, "inpatient_treatment", None)
    residential = getattr(place, "residential_accommodation", None)
    if inpatient is None or residential is None:
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
    ownership = (result.ownership_status or "").strip().casefold()
    result.ownership_status = (
        ownership if ownership in VALID_OWNERSHIP_ASSESSMENTS else "unknown"
    )
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

    # Each evidence item is keyed by its OWN declared field now (not every
    # item dumped onto "facility_type" regardless of what it actually
    # supports) — this is what lets an inpatient/residential claim carry
    # its own, independently verifiable quote + source_url.
    evidence_map: dict[str, ClassificationEvidenceField] = {}
    for item in result.evidence:
        if not item.quote.strip() or not item.source_url.strip():
            continue
        field_name = item.field if item.field in _KNOWN_EVIDENCE_FIELDS else "facility_type"
        field_value = getattr(result, field_name, None)
        if field_value is None:
            field_value = facility_type
        evidence_map.setdefault(
            field_name,
            ClassificationEvidenceField(
                value=field_value,
                confidence=result.confidence,
                evidence_quote=item.quote[:240],
                source_url=item.source_url[:1000],
                source_type="web_search",
            ),
        )
    if result.reason:
        evidence_map.setdefault(
            "classification_bucket",
            ClassificationEvidenceField(
                value=bucket,
                confidence=result.confidence,
                evidence_quote=result.reason[:240],
                source_url=(result.evidence[0].source_url if result.evidence else "")[:1000],
                source_type="web_search",
            ),
        )

    return MapsPlaceEnrichmentResult(
        place_id=place_id,
        operator_type=operator_type,
        ownership_status=ownership,
        facility_type=facility_type,
        organization_scope=organization_scope,
        addiction_focus_confirmed=addiction_focus,
        medical_detox=result.medical_detox,
        residential_accommodation=result.residential_accommodation,
        inpatient_treatment=result.inpatient_treatment,
        operating_status="unknown",
        classification_evidence=evidence_map,
        classification_confidence=result.confidence,
        addictions_treated=[],
        languages_spoken=[],
    )


async def verify_and_prune_unverified_evidence(
    db: Any, *, place: Any, result: MapsPlaceEnrichmentResult
) -> MapsPlaceEnrichmentResult:
    """Fetches every cited source_url through the existing bounded crawler
    (see ``maps_sonar_citation_verification``) and drops any evidence entry
    whose citation does not actually, reachably confirm it is about THIS
    facility — never trusting a Sonar citation blindly.

    Dropping the evidence entry (rather than forcing the field itself) is
    deliberate: ``_apply_structured_fields``'s existing
    ``_normalize_branch_scoped_boolean`` guard already downgrades an
    unsupported ``True`` to unresolved whenever a field has no evidence
    quote — the exact same path a genuinely missing citation takes. An
    unverifiable citation therefore ends up exactly where "no evidence at
    all" already correctly ends up: unresolved, in review — never a false
    rejection, never a manufactured acceptance.
    """
    from app.services.scraping.maps_sonar_citation_verification import (
        verify_citation_refers_to_place,
    )

    verified_cache: dict[str, bool] = {}
    pruned = dict(result.classification_evidence)
    for field_name in list(pruned):
        url = (pruned[field_name].source_url or "").strip()
        if not url:
            continue
        if url not in verified_cache:
            verified_cache[url] = await verify_citation_refers_to_place(
                db, place=place, source_url=url
            )
        if not verified_cache[url]:
            logger.info(
                "maps_sonar_citation_unverified field=%s url=%s place_id=%s",
                field_name,
                url,
                getattr(place, "id", ""),
            )
            del pruned[field_name]
    result.classification_evidence = pruned
    return result


def resolve_sonar_ownership_status(place: Any, result: MapsPlaceEnrichmentResult) -> MapsPlaceEnrichmentResult:
    """Upgrade ``result.ownership_status`` from the conservative bucket
    default to CONFIRMED_NON_GOVERNMENT/CONFIRMED_GOVERNMENT only when
    Sonar's own ownership assessment is backed by surviving, citation-
    verified, non-hedged, place-specific evidence — never by a bare
    "this is probably private" guess or a name/suffix pattern.

    Must run AFTER ``verify_and_prune_unverified_evidence`` (so an
    unreachable/unrelated citation has already been dropped) and BEFORE
    ``_apply_structured_fields_filling_unresolved_only`` persists
    anything. If there is no surviving "ownership_status" evidence item,
    or it does not clear every guard below, this is a complete no-op —
    the existing bucket-derived default (e.g. PROBABLE_NON_GOVERNMENT for
    eligible_candidate) is left exactly as it was; this function only
    ever makes ownership MORE certain, never less, and never invents a
    government/non-government fact that was not actually stated.
    """
    evidence = result.classification_evidence.get("ownership_status")
    if evidence is None:
        return result
    assessed = str(evidence.value or "").strip().casefold()
    if assessed not in {"confirmed_non_government", "confirmed_government"}:
        # "probable_non_government"/"unknown" already match (or are safer
        # than) the existing bucket default — nothing to upgrade.
        return result
    quote = evidence.evidence_quote or ""
    if contains_hedge_language(quote):
        return result
    if is_name_or_domain_inference_only(quote):
        # The model's OWN stated reasoning is a bare name/suffix/domain
        # pattern match ("the name suggests...") with no actual documented
        # ownership fact — exactly the guess this field must not be based
        # on, per the prompt's own instruction.
        return result
    if not is_likely_branch_specific_evidence(
        place, quote=quote, source_url=evidence.source_url, require_name_match=True
    ):
        # Evidence about a different branch/entity, or that cannot be tied
        # to this specific facility at all — never grants a confirmed fact.
        return result
    result.ownership_status = (
        MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value
        if assessed == "confirmed_non_government"
        else MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value
    )
    return result


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
    "resolve_sonar_ownership_status",
    "verify_and_prune_unverified_evidence",
]
