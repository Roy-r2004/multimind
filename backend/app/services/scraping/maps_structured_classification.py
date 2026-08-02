"""Layer 2: structured AI classification from Maps metadata and optional crawl excerpts."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import get_settings
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import get_provider_registry
from app.services.scraping.maps_primary_extraction import PrimaryExtractionEvidence

logger = logging.getLogger(__name__)

MAX_EVIDENCE_ITEMS = 40


class MapsStructuredClassificationResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    facility_type: str | None = None
    operator_type: str | None = None
    ownership_status: str | None = None
    organization_scope: str | None = None
    addiction_treatment_mission: bool | None = None
    care_setting: list[str] = Field(default_factory=list)
    outpatient_treatment: bool | None = None
    inpatient_treatment: bool | None = None
    residential_accommodation: bool | None = None
    medical_detox: bool | None = None
    therapeutic_community: bool | None = None
    psychiatric_addiction_program: bool | None = None
    operating_status: str | None = None
    evidence: list[PrimaryExtractionEvidence] = Field(default_factory=list, max_length=MAX_EVIDENCE_ITEMS)


class StructuredClassificationProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output: MapsStructuredClassificationResult
    input_tokens: int = 0
    output_tokens: int = 0
    provider_request_id: str | None = None


class StructuredClassificationProvider(ABC):
    provider_name: str
    model: str

    @abstractmethod
    async def classify_one(
        self,
        *,
        country_code: str,
        country_name: str,
        facility_payload: dict[str, Any],
        crawl_excerpt: str | None,
    ) -> StructuredClassificationProviderResult:
        pass


def _classification_json_schema() -> dict[str, Any]:
    from app.services.scraping.openrouter_facility_extraction_provider import _strict_json_schema

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "maps_structured_classification",
            "strict": True,
            "schema": _strict_json_schema(MapsStructuredClassificationResult.model_json_schema()),
        },
    }


def _parse_classification_response(text: str) -> MapsStructuredClassificationResult:
    payload = json.loads(text or "{}")
    if isinstance(payload, dict) and "results" in payload and len(payload) == 1:
        payload = payload["results"][0]
    return MapsStructuredClassificationResult.model_validate(payload)


class RegistryStructuredClassificationProvider(StructuredClassificationProvider):
    def __init__(self, *, provider_key: str | None = None, model_id: str | None = None) -> None:
        settings = get_settings()
        self.provider_name = provider_key or settings.maps_primary_extraction_provider
        self.model_id = model_id or settings.maps_primary_extraction_model
        model = get_model(self.model_id)
        self.model = model.provider_model
        self._provider = get_provider_registry().get_provider(model.provider)

    async def classify_one(
        self,
        *,
        country_code: str,
        country_name: str,
        facility_payload: dict[str, Any],
        crawl_excerpt: str | None,
    ) -> StructuredClassificationProviderResult:
        prompt = get_prompt_engine().render(
            "scraping/maps_structured_classifier.j2",
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            facility_json=json.dumps(facility_payload, ensure_ascii=False),
            crawl_excerpt=(crawl_excerpt or "")[: get_settings().maps_crawl_max_total_context_chars],
        )
        response = await self._provider.complete(
            system=(
                "You classify addiction-treatment facilities from metadata and bounded website excerpts. "
                "Classification only — no addictions or languages. Return JSON only."
            ),
            user=prompt,
            model=self.model,
            max_tokens=2048,
            response_format=_classification_json_schema(),
        )
        try:
            output = _parse_classification_response(response.text or "")
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning("maps_structured_classification_parse_failed error=%s", exc)
            raise
        raw = getattr(response, "raw", None)
        usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        return StructuredClassificationProviderResult(
            output=output,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            provider_request_id=str(getattr(response, "request_id", "") or "") or None,
        )


def create_structured_classification_provider() -> StructuredClassificationProvider:
    return RegistryStructuredClassificationProvider()


def map_classification_to_enrichment_fields(
    result: MapsStructuredClassificationResult,
) -> dict[str, Any]:
    from app.services.scraping.maps_primary_extraction import map_primary_to_enrichment_fields
    from app.services.scraping.maps_primary_extraction import MapsPrimaryExtractionResult

    primary_like = MapsPrimaryExtractionResult(
        facility_type=result.facility_type,
        operator_type=result.operator_type,
        ownership_status=result.ownership_status,
        organization_scope=result.organization_scope,
        addiction_treatment_mission=result.addiction_treatment_mission,
        care_setting=result.care_setting,
        outpatient_treatment=result.outpatient_treatment,
        inpatient_treatment=result.inpatient_treatment,
        residential_accommodation=result.residential_accommodation,
        medical_detox=result.medical_detox,
        therapeutic_community=result.therapeutic_community,
        psychiatric_addiction_program=result.psychiatric_addiction_program,
        evidence=result.evidence,
    )
    mapped = map_primary_to_enrichment_fields(primary_like)
    mapped["operating_status"] = result.operating_status or "unknown"
    mapped["addictions_treated"] = []
    mapped["languages_spoken"] = []
    return mapped


def apply_deterministic_classification_fields(
    place: Any,
    rule: Any,
) -> None:
    from app.services.scraping.maps_classification_rules import DeterministicClassification

    assert isinstance(rule, DeterministicClassification)
    place.facility_type = rule.facility_type
    place.ownership_status = rule.ownership_status
    place.operator_type = rule.operator_type
    place.organization_scope = rule.organization_scope
    place.addiction_focus_confirmed = rule.addiction_focus_confirmed
    place.lifecycle_status = rule.lifecycle_status
    place.client_eligibility = rule.client_eligibility
    evidence = dict(place.classification_evidence or {})
    evidence["deterministic_rule"] = {"value": rule.rule_id, "source_type": "deterministic"}
    place.classification_evidence = evidence
    place.classification_confidence = 0.95
    place.enrichment_extraction_source = "deterministic_rules"


__all__ = [
    "MapsStructuredClassificationResult",
    "StructuredClassificationProvider",
    "StructuredClassificationProviderResult",
    "apply_deterministic_classification_fields",
    "create_structured_classification_provider",
    "map_classification_to_enrichment_fields",
]
