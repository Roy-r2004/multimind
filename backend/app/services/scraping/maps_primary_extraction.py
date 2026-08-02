"""Configurable primary structured extractor for Maps enrichment cascade."""

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
from app.services.scraping.maps_pydantic_utils import TruncatingModel

logger = logging.getLogger(__name__)

PRIMARY_EXTRACTION_TIMEOUT_SECONDS = 90.0
MAX_EVIDENCE_ITEMS = 40


class PrimaryExtractionEvidence(TruncatingModel):
    model_config = ConfigDict(extra="ignore")

    field: str = Field(default="", max_length=64)
    value: str | None = Field(default=None, max_length=500)
    quote: str = Field(default="", max_length=240)
    source_url: str = Field(default="", max_length=1000)
    page_title: str = Field(default="", max_length=300)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class MapsPrimaryExtractionResult(BaseModel):
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
    substances_or_addictions_treated: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    address: str | None = None
    phone: str | None = None
    email: str | None = None
    evidence: list[PrimaryExtractionEvidence] = Field(default_factory=list, max_length=MAX_EVIDENCE_ITEMS)


class PrimaryExtractionProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output: MapsPrimaryExtractionResult
    input_tokens: int = 0
    output_tokens: int = 0
    actual_cost: float | None = None
    provider_request_id: str | None = None


class PrimaryExtractionProvider(ABC):
    provider_name: str
    model: str

    @abstractmethod
    async def extract_one(
        self,
        *,
        country_code: str,
        country_name: str,
        facility_payload: dict[str, Any],
        crawl_excerpt: str | None,
    ) -> PrimaryExtractionProviderResult:
        pass


def _primary_json_schema() -> dict[str, Any]:
    from app.services.scraping.openrouter_facility_extraction_provider import (
        _strict_json_schema,
    )

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "maps_primary_extraction",
            "strict": True,
            "schema": _strict_json_schema(MapsPrimaryExtractionResult.model_json_schema()),
        },
    }


def _parse_primary_response(text: str) -> MapsPrimaryExtractionResult:
    payload = json.loads(text or "{}")
    if isinstance(payload, dict) and "results" in payload and len(payload) == 1:
        payload = payload["results"][0]
    return MapsPrimaryExtractionResult.model_validate(payload)


class RegistryPrimaryExtractionProvider(PrimaryExtractionProvider):
    def __init__(self, *, provider_key: str | None = None, model_id: str | None = None) -> None:
        settings = get_settings()
        self.provider_name = provider_key or settings.maps_primary_extraction_provider
        self.model_id = model_id or settings.maps_primary_extraction_model
        model = get_model(self.model_id)
        self.model = model.provider_model
        self._provider = get_provider_registry().get_provider(model.provider)

    async def extract_one(
        self,
        *,
        country_code: str,
        country_name: str,
        facility_payload: dict[str, Any],
        crawl_excerpt: str | None,
    ) -> PrimaryExtractionProviderResult:
        prompt = get_prompt_engine().render(
            "scraping/maps_primary_extractor.j2",
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            facility_json=json.dumps(facility_payload, ensure_ascii=False),
            crawl_excerpt=(crawl_excerpt or "")[: get_settings().maps_crawl_max_total_context_chars],
        )
        response = await self._provider.complete(
            system=(
                "You extract structured addiction-treatment facility facts from metadata and "
                "bounded website excerpts. Return JSON only."
            ),
            user=prompt,
            model=self.model,
            max_tokens=4096,
            response_format=_primary_json_schema(),
        )
        try:
            output = _parse_primary_response(response.text or "")
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning("maps_primary_extraction_parse_failed error=%s", exc)
            raise
        raw = getattr(response, "raw", None)
        usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        return PrimaryExtractionProviderResult(
            output=output,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            provider_request_id=str(getattr(response, "request_id", "") or "") or None,
        )


def create_primary_extraction_provider() -> PrimaryExtractionProvider:
    return RegistryPrimaryExtractionProvider()


def map_primary_to_enrichment_fields(result: MapsPrimaryExtractionResult) -> dict[str, Any]:
    care_setting = result.care_setting[0] if len(result.care_setting) == 1 else (
        "mixed" if len(result.care_setting) > 1 else "unknown"
    )
    evidence_map: dict[str, dict[str, Any]] = {}
    for item in result.evidence:
        if not item.field:
            continue
        evidence_map[item.field] = {
            "value": item.value,
            "confidence": item.confidence,
            "evidence_quote": item.quote,
            "source_url": item.source_url,
            "source_type": "official_site",
        }
    return {
        "operator_type": result.operator_type or "unknown",
        "ownership_status": result.ownership_status or "ownership_unknown",
        "facility_type": result.facility_type or "unknown",
        "care_setting": care_setting,
        "organization_scope": result.organization_scope or "unknown",
        "addiction_focus_confirmed": result.addiction_treatment_mission,
        "medical_detox": result.medical_detox,
        "residential_accommodation": result.residential_accommodation,
        "operating_status": "unknown",
        "classification_evidence": evidence_map,
        "classification_confidence": _average_confidence(result.evidence),
        "addictions_treated": [
            {"value": value, "evidence_quote": "", "source_url": ""}
            for value in result.substances_or_addictions_treated
        ],
        "languages_spoken": [
            {"value": value, "evidence_quote": "", "source_url": ""}
            for value in result.languages
        ],
    }


def _average_confidence(evidence: list[PrimaryExtractionEvidence]) -> float | None:
    scores = [item.confidence for item in evidence if item.confidence is not None]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 4)


__all__ = [
    "MapsPrimaryExtractionResult",
    "PrimaryExtractionProvider",
    "PrimaryExtractionProviderResult",
    "create_primary_extraction_provider",
    "map_primary_to_enrichment_fields",
]
