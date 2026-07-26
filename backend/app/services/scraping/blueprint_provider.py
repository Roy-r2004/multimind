"""OpenRouter-backed country blueprint generation; no provider-owned polling."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.config import Settings
from app.llm.providers import LLMProvider, LLMResponse, OpenRouterProvider
from app.schemas.api import CountryMaximumCoverageStructuredBlueprint


class BlueprintProviderError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class BlueprintProviderResult:
    human_readable_blueprint: str
    structured_blueprint: CountryMaximumCoverageStructuredBlueprint
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
        structured_output_schema: type[CountryMaximumCoverageStructuredBlueprint],
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
        structured_output_schema: type[CountryMaximumCoverageStructuredBlueprint],
    ) -> BlueprintProviderResult:
        self.validate_configuration()
        started_at = datetime.now(UTC)
        try:
            research = await self._client.complete(
                system="Produce a source-backed human-readable country research blueprint.",
                user=rendered_prompt,
                model=self._settings.openrouter_blueprint_research_model,
                max_tokens=self._settings.openrouter_blueprint_max_output_tokens,
            )
        except Exception as exc:
            raise self._map_error(exc) from exc
        if not research.text.strip():
            raise BlueprintProviderError("empty_research", "OpenRouter returned an empty research report.")

        try:
            structured_response = await self._client.complete(
                system=(
                    "Transform the research report into the requested JSON schema. "
                    "Do not invent facts or citations."
                ),
                user=research.text,
                model=self._settings.openrouter_blueprint_structuring_model,
                max_tokens=self._settings.openrouter_blueprint_max_output_tokens,
                response_format={"type": "json_schema", "json_schema": {"name": "blueprint", "schema": structured_output_schema.model_json_schema()}},
            )
            structured = structured_output_schema.model_validate(json.loads(structured_response.text))
        except (json.JSONDecodeError, ValueError) as exc:
            raise BlueprintProviderError("structured_output", "OpenRouter returned invalid structured output.") from exc
        except Exception as exc:
            raise self._map_error(exc) from exc

        citations = self._citations(research)
        metadata = {
            "research_model": self._settings.openrouter_blueprint_research_model,
            "structuring_model": self._settings.openrouter_blueprint_structuring_model,
            "research_tokens_input": research.tokens_input,
            "research_tokens_output": research.tokens_output,
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
    def _map_error(exc: Exception) -> BlueprintProviderError:
        text = str(exc).lower()
        category = "authentication" if "401" in text or "auth" in text else (
            "payment" if "402" in text or "credit" in text else
            "rate_limit" if "429" in text or "rate" in text else
            "invalid_model" if "model" in text and ("invalid" in text or "not found" in text) else
            "network"
        )
        return BlueprintProviderError(category, "OpenRouter blueprint generation failed.")
