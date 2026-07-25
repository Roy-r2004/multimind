"""Provider boundary for future country blueprint generation.

Phase 1A deliberately contains no live Gemini implementation.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import Settings
from app.schemas.api import CountryMaximumCoverageStructuredBlueprint


@dataclass(frozen=True)
class BlueprintProviderResult:
    human_readable_blueprint: str
    structured_blueprint: CountryMaximumCoverageStructuredBlueprint
    citations: list[dict[str, Any]]
    provider: str
    model_id: str
    execution_metadata: dict[str, Any] = field(default_factory=dict)


class BlueprintProvider(Protocol):
    """Mockable provider contract; implementations must validate output."""

    async def generate_blueprint(
        self,
        *,
        mission: Any,
        rendered_prompt: str,
        structured_output_schema: type[CountryMaximumCoverageStructuredBlueprint],
    ) -> BlueprintProviderResult: ...


class GeminiBlueprintProvider:
    """Configuration-validating Gemini adapter reserved for Phase 1B."""

    provider_name = "gemini"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def validate_configuration(self) -> None:
        if not self._settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required before Gemini blueprint generation.")
        if not self._settings.gemini_blueprint_model.strip():
            raise ValueError("GEMINI_BLUEPRINT_MODEL is required before Gemini blueprint generation.")

    async def generate_blueprint(
        self,
        *,
        mission: Any,
        rendered_prompt: str,
        structured_output_schema: type[CountryMaximumCoverageStructuredBlueprint],
    ) -> BlueprintProviderResult:
        self.validate_configuration()
        raise NotImplementedError("Gemini blueprint generation is not implemented until Phase 1B.")
