"""Rendering for versioned, backend-owned country blueprint prompts."""

from dataclasses import dataclass

from app.llm.prompt_engine import PromptEngine, get_prompt_engine
from app.services.scraping.countries import Country

# Historical v1 prompt retained for audit/provenance; new generation uses v2 only.
COUNTRY_MAXIMUM_COVERAGE_TEMPLATE_V1 = "scraping/country_maximum_coverage_blueprint_v1.jinja2"
COUNTRY_MAXIMUM_COVERAGE_TEMPLATE_VERSION_V1 = "country_maximum_coverage_blueprint_v1"
COUNTRY_MAXIMUM_COVERAGE_TEMPLATE = "scraping/country_maximum_coverage_blueprint_v2.jinja2"
COUNTRY_MAXIMUM_COVERAGE_TEMPLATE_VERSION = "country_maximum_coverage_blueprint_v2"


@dataclass(frozen=True)
class RenderedBlueprintPrompt:
    template_version: str
    rendered_prompt: str


class BlueprintPromptService:
    def __init__(self, prompt_engine: PromptEngine | None = None) -> None:
        self._prompt_engine = prompt_engine or get_prompt_engine()

    def render_country_maximum_coverage(
        self, *, mission_title: str, country: Country
    ) -> RenderedBlueprintPrompt:
        title = mission_title.strip()
        if not title:
            raise ValueError("Mission title is required to render a blueprint prompt.")
        rendered = self._prompt_engine.render(
            COUNTRY_MAXIMUM_COVERAGE_TEMPLATE,
            MISSION_TITLE=title,
            COUNTRY_NAME=country.name,
            COUNTRY_ISO2=country.code,
            COUNTRY_ISO3=country.iso3,
            CONTINENT=country.continent,
        )
        return RenderedBlueprintPrompt(
            template_version=COUNTRY_MAXIMUM_COVERAGE_TEMPLATE_VERSION,
            rendered_prompt=rendered,
        )


blueprint_prompt_service = BlueprintPromptService()
