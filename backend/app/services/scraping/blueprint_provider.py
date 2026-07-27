"""OpenRouter-backed country blueprint generation; no provider-owned polling."""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import ValidationError

from app.core.config import Settings
from app.llm.providers import LLMProvider, LLMResponse, OpenRouterProvider, OpenRouterProviderError
from app.schemas.api import (
    CountryMaximumCoverageStructuredBlueprint,
    CountryMaximumCoverageStructuredBlueprintV2,
    StructuredBlueprintAny,
)
from app.services.scraping.blueprint_structured_contract import (
    canonical_structured_blueprint_skeleton,
    describe_validation_contract_gap,
    detect_structured_blueprint_schema_version,
    normalize_structured_blueprint_payload,
)

BLUEPRINT_RESEARCH_WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "openrouter:web_search",
    "parameters": {
        "engine": "auto",
        "max_results": 10,
        "max_total_results": 30,
        "search_context_size": "high",
    },
}

BLUEPRINT_RESEARCH_SYSTEM_PROMPT = (
    "Produce the requested country-specific research blueprint. "
    "Use web search when needed, cite sources with URLs, and do not invent facilities."
)

BLUEPRINT_STRUCTURING_RESPONSE_FORMAT: dict[str, str] = {"type": "json_object"}

BLUEPRINT_STRUCTURING_SYSTEM_PROMPT = (
    "You convert a country-specific research blueprint into machine-readable JSON. "
    "Return JSON only with no Markdown fences and no surrounding commentary. "
    "Use exactly the CountryMaximumCoverageStructuredBlueprintV2 field names provided "
    "in the canonical JSON skeleton. Preserve the Stage 1 research strategy, keep "
    "physical-country containment, do not invent facilities or URLs, and do not "
    "change the selected country. Use null for unknown optional scalar fields and "
    "[] for unknown optional collections. PASS/FAIL/UNKNOWN remain the only "
    "qualification statuses."
)

BLUEPRINT_STRUCTURING_CORRECTION_SYSTEM_PROMPT = (
    "You correct invalid structured blueprint JSON. "
    "Return corrected JSON only with no Markdown fences and no surrounding commentary. "
    "Use exactly the provided canonical JSON skeleton and field names. Rename or "
    "remove extra fields, add only genuinely known missing fields, preserve research "
    "meaning, keep physical-country containment, do not invent facilities or URLs, "
    "and do not change the selected country."
)


class BlueprintProviderError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        stage: str | None = None,
        model: str | None = None,
        http_status: int | None = None,
        provider_error_code: str | None = None,
        provider_error_type: str | None = None,
        provider_message: str | None = None,
        retryable: bool | None = None,
        research_text: str | None = None,
        citations: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.stage = stage
        self.model = model
        self.http_status = http_status
        self.provider_error_code = provider_error_code
        self.provider_error_type = provider_error_type
        self.provider_message = provider_message
        self.retryable = retryable
        self.research_text = research_text
        self.citations = citations

    def safe_diagnostics(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "model": self.model,
            "http_status": self.http_status,
            "provider_error_code": self.provider_error_code,
            "provider_error_type": self.provider_error_type,
            "provider_message": self.provider_message,
            "retryable": self.retryable,
            "category": self.category,
        }


@dataclass(frozen=True)
class BlueprintProviderResult:
    human_readable_blueprint: str
    structured_blueprint: StructuredBlueprintAny
    citations: list[dict[str, Any]]
    provider: str
    model_id: str
    execution_metadata: dict[str, Any] = field(default_factory=dict)
    operation_id: str | None = None  # Generic provider response/request correlation ID.
    started_at: datetime | None = None
    completed_at: datetime | None = None


class BlueprintProvider(Protocol):
    async def generate_blueprint(
        self, *, mission: Any, rendered_prompt: str,
        structured_output_schema: type[StructuredBlueprintAny],
    ) -> BlueprintProviderResult: ...


class OpenRouterBlueprintProvider:
    provider_name = "openrouter"

    def __init__(self, settings: Settings, client: LLMProvider | None = None) -> None:
        self._settings = settings
        self._client = client or OpenRouterProvider()

    def validate_configuration(self) -> None:
        if not self._settings.openrouter_api_key:
            raise BlueprintProviderError("configuration", "OpenRouter is not configured.")
        if not self._settings.openrouter_blueprint_research_model.strip():
            raise BlueprintProviderError("configuration", "Research model is not configured.")
        if not self._settings.openrouter_blueprint_structuring_model.strip():
            raise BlueprintProviderError("configuration", "Structuring model is not configured.")

    async def generate_blueprint(
        self, *, mission: Any, rendered_prompt: str,
        structured_output_schema: type[StructuredBlueprintAny] = CountryMaximumCoverageStructuredBlueprintV2,
    ) -> BlueprintProviderResult:
        self.validate_configuration()
        started_at = datetime.now(UTC)
        try:
            research = await self._client.complete(
                system=BLUEPRINT_RESEARCH_SYSTEM_PROMPT,
                user=rendered_prompt,
                model=self._settings.openrouter_blueprint_research_model,
                max_tokens=self._settings.openrouter_blueprint_max_output_tokens,
                tools=[BLUEPRINT_RESEARCH_WEB_SEARCH_TOOL],
                timeout_seconds=self._settings.openrouter_blueprint_timeout_seconds,
            )
        except Exception as exc:
            raise self._map_error(
                exc,
                stage="research",
                model=self._settings.openrouter_blueprint_research_model,
            ) from exc
        if not research.text.strip():
            raise BlueprintProviderError("empty_research", "OpenRouter returned an empty research report.")

        citations = self._citations(research)
        try:
            structured, correction_attempted = await self._structure_research(
                research_text=research.text,
                structured_output_schema=structured_output_schema,
            )
        except BlueprintProviderError as exc:
            if exc.research_text is None:
                exc.research_text = research.text
            if exc.citations is None:
                exc.citations = citations
            raise
        except Exception as exc:
            raise self._map_error(
                exc,
                stage="structuring",
                model=self._settings.openrouter_blueprint_structuring_model,
                research_text=research.text,
                citations=citations,
            ) from exc

        metadata = {
            "research_model": self._settings.openrouter_blueprint_research_model,
            "structuring_model": self._settings.openrouter_blueprint_structuring_model,
            "research_tokens_input": research.tokens_input,
            "research_tokens_output": research.tokens_output,
            "structuring_correction_attempted": correction_attempted,
        }
        return BlueprintProviderResult(
            human_readable_blueprint=research.text,
            structured_blueprint=structured,
            citations=citations,
            provider=self.provider_name,
            model_id=self._settings.openrouter_blueprint_research_model,
            execution_metadata=metadata,
            operation_id=(research.raw or {}).get("id"),
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    async def _structure_research(
        self,
        *,
        research_text: str,
        structured_output_schema: type[StructuredBlueprintAny],
    ) -> tuple[StructuredBlueprintAny, bool]:
        model = self._settings.openrouter_blueprint_structuring_model
        try:
            first = await self._client.complete(
                system=BLUEPRINT_STRUCTURING_SYSTEM_PROMPT,
                user=self._structuring_user_message(research_text),
                model=model,
                max_tokens=self._settings.openrouter_blueprint_max_output_tokens,
                response_format=BLUEPRINT_STRUCTURING_RESPONSE_FORMAT,
                timeout_seconds=self._settings.openrouter_blueprint_timeout_seconds,
            )
        except Exception as exc:
            raise self._map_error(exc, stage="structuring", model=model) from exc

        try:
            return self._validate_structured(first.text, structured_output_schema), False
        except (json.JSONDecodeError, ValidationError, ValueError) as first_exc:
            try:
                correction = await self._client.complete(
                    system=BLUEPRINT_STRUCTURING_CORRECTION_SYSTEM_PROMPT,
                    user=self._correction_user_message(first.text, first_exc),
                    model=model,
                    max_tokens=self._settings.openrouter_blueprint_max_output_tokens,
                    response_format=BLUEPRINT_STRUCTURING_RESPONSE_FORMAT,
                    timeout_seconds=self._settings.openrouter_blueprint_timeout_seconds,
                )
            except Exception as exc:
                raise self._map_error(exc, stage="structuring", model=model) from exc
            try:
                return self._validate_structured(correction.text, structured_output_schema), True
            except (json.JSONDecodeError, ValidationError, ValueError) as second_exc:
                raise BlueprintProviderError(
                    "structured_output",
                    "OpenRouter returned invalid structured output.",
                    stage="structuring",
                    model=model,
                    retryable=False,
                    provider_message=self._safe_validation_summary(second_exc),
                ) from second_exc

    @staticmethod
    def _structuring_user_message(research_text: str) -> str:
        skeleton = json.dumps(canonical_structured_blueprint_skeleton(), indent=2)
        return (
            "Convert the following Stage 1 research into one JSON object.\n\n"
            "Requirements:\n"
            "- JSON only\n"
            "- no Markdown fences\n"
            "- exact canonical field names from the skeleton\n"
            "- preserve the Stage 1 research strategy\n"
            "- keep physical-country containment and selected-country identity\n"
            "- do not invent facilities, URLs, languages, regions, or citations\n"
            "- regulatory/commercial source URL may be null when unknown\n"
            "- use source title (not name) and source_type (not type)\n\n"
            f"Canonical JSON skeleton:\n{skeleton}\n\n"
            f"Stage 1 research:\n{research_text}"
        )

    @staticmethod
    def _correction_user_message(invalid_text: str, exc: Exception) -> str:
        try:
            payload = OpenRouterBlueprintProvider._parse_json_object(invalid_text)
        except (json.JSONDecodeError, TypeError, ValueError):
            payload = None
        gap = describe_validation_contract_gap(
            payload, exc, expected_schema_version="2"
        )
        return (
            "The previous structured response was invalid. Return corrected JSON only.\n\n"
            f"Validation errors:\n{OpenRouterBlueprintProvider._safe_validation_summary(exc)}\n\n"
            "Exact missing fields:\n"
            f"{json.dumps(gap['missing_fields'], indent=2)}\n\n"
            "Exact extra fields that must be renamed or removed:\n"
            f"{json.dumps(gap['extra_fields'], indent=2)}\n\n"
            "Canonical JSON skeleton (exact expected field structure):\n"
            f"{json.dumps(gap['canonical_skeleton'], indent=2)}\n\n"
            "Do not change research meaning or the selected country. "
            "Do not invent facilities or URLs. "
            'Emit schema_version as the string "2" explicitly and include every required v2 field.\n\n'
            f"Invalid structured response:\n{invalid_text}"
        )

    @staticmethod
    def _validate_structured(
        text: str,
        structured_output_schema: type[StructuredBlueprintAny],
    ) -> StructuredBlueprintAny:
        parsed = OpenRouterBlueprintProvider._parse_json_object(text)
        if structured_output_schema is CountryMaximumCoverageStructuredBlueprintV2:
            version = detect_structured_blueprint_schema_version(parsed)
            if version != "2":
                raise ValueError(
                    'New blueprints require schema_version to be the string "2"; '
                    "do not omit it or emit another value."
                )
        normalized = normalize_structured_blueprint_payload(parsed)
        return structured_output_schema.model_validate(normalized)

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise json.JSONDecodeError("Expected a JSON object", cleaned, 0)
        return data

    @staticmethod
    def _safe_validation_summary(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            message = "; ".join(
                f"{'.'.join(str(part) for part in error.get('loc', ()))}: {error.get('msg')}"
                for error in exc.errors()[:8]
            )
        else:
            message = str(exc)
        message = message.replace("\n", " ").replace("\r", " ").strip()
        message = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", message)
        return message[:240] or "Structured output validation failed."

    @staticmethod
    def _citations(response: LLMResponse) -> list[dict[str, Any]]:
        raw = response.raw or {}
        annotations = raw.get("annotations", [])
        return [
            {"url": item["url"], "title": item.get("title"), "source_type": "openrouter_annotation"}
            for item in annotations
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        ]

    @staticmethod
    def _map_error(
        exc: Exception,
        *,
        stage: str | None = None,
        model: str | None = None,
        research_text: str | None = None,
        citations: list[dict[str, Any]] | None = None,
    ) -> BlueprintProviderError:
        if isinstance(exc, OpenRouterProviderError):
            category = (
                "authentication" if exc.http_status == 401 else
                "payment" if exc.http_status == 402 else
                "rate_limit" if exc.http_status == 429 else
                "invalid_model" if exc.http_status == 400 and exc.provider_error_code == "invalid_model" else
                "provider"
            )
            return BlueprintProviderError(
                category,
                "OpenRouter blueprint generation failed.",
                stage=stage,
                model=model,
                http_status=exc.http_status,
                provider_error_code=exc.provider_error_code,
                provider_error_type=exc.provider_error_type,
                provider_message=exc.provider_message,
                retryable=exc.retryable,
                research_text=research_text,
                citations=citations,
            )
        text = str(exc).lower()
        category = "authentication" if "401" in text or "auth" in text else (
            "payment" if "402" in text or "credit" in text else
            "rate_limit" if "429" in text or "rate" in text else
            "invalid_model" if "model" in text and ("invalid" in text or "not found" in text) else
            "network"
        )
        return BlueprintProviderError(
            category,
            "OpenRouter blueprint generation failed.",
            stage=stage,
            model=model,
            retryable=category == "network",
            research_text=research_text,
            citations=citations,
        )
