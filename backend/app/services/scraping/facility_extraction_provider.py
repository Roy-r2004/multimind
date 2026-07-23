"""Provider-neutral structured facility extraction contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_VALUE_LENGTH = 1000
EXTRACTION_SCHEMA_VERSION = "facility-extraction-schema-v2"


class ExtractedEvidenceValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=MAX_VALUE_LENGTH)
    evidence_quote: str = Field(min_length=1, max_length=MAX_VALUE_LENGTH)

    @field_validator("value", "evidence_quote", mode="before")
    @classmethod
    def trim(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value


class ExtractedFacility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ExtractedEvidenceValue
    aliases: list[ExtractedEvidenceValue] = Field(default_factory=list)
    facility_type: ExtractedEvidenceValue | None = None
    operator: ExtractedEvidenceValue | None = None
    addresses: list[ExtractedEvidenceValue] = Field(default_factory=list)
    phones: list[ExtractedEvidenceValue] = Field(default_factory=list)
    emails: list[ExtractedEvidenceValue] = Field(default_factory=list)
    websites: list[ExtractedEvidenceValue] = Field(default_factory=list)
    services: list[ExtractedEvidenceValue] = Field(default_factory=list)
    license_or_registration: list[ExtractedEvidenceValue] = Field(default_factory=list)
    model_confidence: float | None = Field(default=None, ge=0, le=1)


class FacilityExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_relevant: bool
    facilities: list[ExtractedFacility] = Field(default_factory=list)


class FacilityExtractionProviderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output: FacilityExtractionOutput
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    provider_request_id: str | None = Field(default=None, max_length=255)


class FacilityExtractionProvider(ABC):
    provider_name: str
    model: str
    prompt_version: str
    schema_version: str = EXTRACTION_SCHEMA_VERSION

    @abstractmethod
    async def extract(
        self, *, chunk_text: str, language_hint: str | None = None
    ) -> FacilityExtractionProviderResult:
        pass


class FacilityStructuredOutputError(Exception):
    def __init__(self, safe_message: str, diagnostics: dict[str, Any]) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.diagnostics = diagnostics
