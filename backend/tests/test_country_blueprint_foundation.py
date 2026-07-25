"""Focused Phase 1A foundation tests; no live provider or network use."""

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.config import Settings
from app.db.models import ScrapingBlueprintStatus
from app.schemas.api import (
    CountryMaximumCoverageStructuredBlueprint,
    ScrapingMissionCreate,
)
from app.services.scraping.blueprint_prompt_service import BlueprintPromptService
from app.services.scraping.blueprint_provider import GeminiBlueprintProvider
from app.services.scraping.countries import resolve_country


def valid_structured_blueprint() -> dict:
    citation = {"url": "https://example.test/source", "title": "Source"}
    strategy = {"summary": "Use documented evidence and retain provenance."}
    return {
        "country_dossier": {
            "country_name": "Austria",
            "country_iso3": "AUT",
            "continent": "Europe",
            "local_term": "Suchtbehandlung",
        },
        "regions": ["Vienna"],
        "languages": ["German"],
        "regulatory_sources": [citation],
        "commercial_sources": [citation],
        "query_matrix": [{"query": "Austria inpatient addiction rehabilitation", "language": "German", "purpose": "discovery"}],
        "region_coverage_plan": [{"region_name": "Vienna", "coverage_actions": ["Search registry"]}],
        "discovery_strategy": strategy,
        "crawl_strategy": strategy,
        "contact_completeness_strategy": strategy,
        "verification_rules": strategy,
        "country_containment_rules": strategy,
        "deduplication_rules": strategy,
        "confidence_model": strategy,
        "completion_criteria": ["Search every region."],
        "risks": ["Incomplete directory coverage."],
        "citations": [citation],
        "estimated_coverage": strategy,
        "weak_areas": ["Unindexed sites."],
        "human_review_questions": ["Review borderline providers."],
        "approval_recommendation": {"ready": False, "reason": "Await review."},
    }


def test_country_metadata_resolves_name_and_normalizes_codes() -> None:
    by_name = resolve_country("Austria")
    assert (by_name.name, by_name.iso2, by_name.iso3, by_name.continent) == (
        "Austria",
        "AT",
        "AUT",
        "Europe",
    )
    assert resolve_country("aut") == by_name
    assert resolve_country(" at ") == by_name


def test_invalid_country_is_rejected() -> None:
    with pytest.raises(Exception, match="Country must be"):
        resolve_country("Not a country")


def test_mission_schema_rejects_empty_title_and_requires_one_country_input() -> None:
    with pytest.raises(PydanticValidationError):
        ScrapingMissionCreate(
            title=" ",
            country="Austria",
            original_prompt="Find facilities",
            model_set_id="test",
        )
    with pytest.raises(PydanticValidationError, match="exactly one"):
        ScrapingMissionCreate(
            title="Austria coverage",
            country="Austria",
            country_code="AT",
            original_prompt="Find facilities",
            model_set_id="test",
        )


def test_prompt_rendering_is_country_specific_and_never_starts_scraping() -> None:
    rendered = BlueprintPromptService().render_country_maximum_coverage(
        mission_title="Austria Private Rehab Coverage",
        country=resolve_country("AUT"),
    )
    assert rendered.template_version == "country_maximum_coverage_blueprint_v1"
    assert "Austria" in rendered.rendered_prompt
    assert "AUT" in rendered.rendered_prompt
    assert "Europe" in rendered.rendered_prompt
    assert "must not become results" in rendered.rendered_prompt
    assert "do not begin final facility scraping" in rendered.rendered_prompt
    assert "not a worldwide mission" in rendered.rendered_prompt


def test_structured_blueprint_validation_and_country_specific_extras() -> None:
    blueprint = CountryMaximumCoverageStructuredBlueprint.model_validate(valid_structured_blueprint())
    assert blueprint.country_dossier.model_extra == {"local_term": "Suchtbehandlung"}
    invalid = valid_structured_blueprint()
    del invalid["completion_criteria"]
    with pytest.raises(PydanticValidationError):
        CountryMaximumCoverageStructuredBlueprint.model_validate(invalid)
    invalid_approval = valid_structured_blueprint()
    invalid_approval["approval_recommendation"]["ready"] = None
    with pytest.raises(PydanticValidationError):
        CountryMaximumCoverageStructuredBlueprint.model_validate(invalid_approval)


def test_blueprint_statuses_include_phase_one_foundation_values() -> None:
    assert {
        ScrapingBlueprintStatus.DRAFT.value,
        ScrapingBlueprintStatus.QUEUED.value,
        ScrapingBlueprintStatus.RUNNING.value,
        ScrapingBlueprintStatus.READY_FOR_REVIEW.value,
        ScrapingBlueprintStatus.FAILED.value,
        ScrapingBlueprintStatus.APPROVED.value,
        ScrapingBlueprintStatus.REJECTED.value,
        ScrapingBlueprintStatus.DISCARDED.value,
    }


@pytest.mark.asyncio
async def test_gemini_provider_validates_configuration_without_network() -> None:
    provider = GeminiBlueprintProvider(Settings(gemini_api_key=None))
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        provider.validate_configuration()
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        await provider.generate_blueprint(
            mission=object(),
            rendered_prompt="local test",
            structured_output_schema=CountryMaximumCoverageStructuredBlueprint,
        )
