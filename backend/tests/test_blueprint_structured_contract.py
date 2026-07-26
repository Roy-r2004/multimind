"""Regression coverage for structured-blueprint contract alignment."""

import json

import pytest
from pydantic import ValidationError
from test_country_blueprint_foundation import valid_structured_blueprint

from app.schemas.api import CountryMaximumCoverageStructuredBlueprint
from app.services.scraping.blueprint_prompt_service import BlueprintPromptService
from app.services.scraping.blueprint_provider import OpenRouterBlueprintProvider
from app.services.scraping.blueprint_structured_contract import (
    canonical_structured_blueprint_skeleton,
    describe_validation_contract_gap,
    normalize_structured_blueprint_payload,
)
from app.services.scraping.countries import resolve_country


def test_canonical_skeleton_matches_pydantic_required_top_level_fields() -> None:
    skeleton = canonical_structured_blueprint_skeleton()
    validated = CountryMaximumCoverageStructuredBlueprint.model_validate(
        {
            **skeleton,
            "country_dossier": {
                "country_name": "Austria",
                "country_iso3": "AUT",
                "continent": "Europe",
            },
            "regions": ["Vienna"],
            "languages": ["German"],
            "regulatory_sources": [{"url": None, "title": "Ministry", "source_type": "regulator"}],
            "commercial_sources": [],
            "query_matrix": [
                {"query": "Austria rehab", "language": "German", "purpose": "discovery"}
            ],
            "region_coverage_plan": [
                {"region_name": "Vienna", "coverage_actions": ["Search registry"]}
            ],
            "discovery_strategy": {"summary": "Discover"},
            "crawl_strategy": {"summary": "Crawl"},
            "contact_completeness_strategy": {"summary": "Contacts"},
            "verification_rules": {"summary": "Verify"},
            "country_containment_rules": {"summary": "Contain"},
            "deduplication_rules": {"summary": "Dedupe"},
            "confidence_model": {"summary": "Confidence"},
            "completion_criteria": ["Cover regions"],
            "risks": ["Gaps"],
            "citations": [],
            "estimated_coverage": {"summary": "Coverage"},
            "weak_areas": ["Unindexed"],
            "human_review_questions": ["Review?"],
            "approval_recommendation": {"ready": False, "reason": "Await review"},
        }
    )
    assert validated.country_dossier.country_iso3 == "AUT"


def test_prompt_skeleton_uses_canonical_pydantic_field_names() -> None:
    prompt = BlueprintPromptService().render_country_maximum_coverage(
        mission_title="Austria coverage",
        country=resolve_country("AT"),
    ).rendered_prompt
    assert '"regions": []' in prompt
    assert '"languages": []' in prompt
    assert '"regulatory_sources"' in prompt
    assert '"region_coverage_plan"' in prompt
    assert '"country_containment_rules"' in prompt
    assert "source_type" in prompt
    assert "regional_coverage_plan" not in prompt
    assert "source_landscape" not in prompt
    assert "template_version" not in prompt.split("Required machine-readable")[1]


def test_normalization_fills_missing_regions_and_languages_aliases() -> None:
    payload = valid_structured_blueprint()
    del payload["regions"]
    del payload["languages"]
    payload["administrative_regions"] = ["Vienna", "Salzburg"]
    payload["local_languages"] = ["German"]
    payload["official_languages"] = ["German", "English"]
    normalized = normalize_structured_blueprint_payload(payload)
    assert normalized["regions"] == ["Vienna", "Salzburg"]
    assert normalized["languages"] == ["German", "English"]
    CountryMaximumCoverageStructuredBlueprint.model_validate(normalized)


def test_normalization_extracts_regions_from_coverage_plan_without_invention() -> None:
    payload = valid_structured_blueprint()
    del payload["regions"]
    payload["region_coverage_plan"] = [
        {"region_name": "Tyrol", "coverage_actions": ["Search"]},
        {"region_name": "Vienna", "coverage_actions": ["Search"]},
    ]
    normalized = normalize_structured_blueprint_payload(payload)
    assert normalized["regions"] == ["Tyrol", "Vienna"]


def test_normalization_maps_source_name_type_and_allows_missing_url() -> None:
    payload = valid_structured_blueprint()
    payload["regulatory_sources"] = [
        {"name": "Ministry of Health", "type": "regulator"},
        {"title": "Directory", "url": "", "source_type": "directory"},
    ]
    normalized = normalize_structured_blueprint_payload(payload)
    assert normalized["regulatory_sources"] == [
        {
            "url": None,
            "title": "Ministry of Health",
            "source_type": "regulator",
        },
        {
            "url": None,
            "title": "Directory",
            "source_type": "directory",
        },
    ]
    validated = CountryMaximumCoverageStructuredBlueprint.model_validate(normalized)
    assert validated.regulatory_sources[0].url is None
    assert validated.regulatory_sources[0].title == "Ministry of Health"


def test_normalization_defaults_empty_optional_collections_without_inventing_values() -> None:
    payload = valid_structured_blueprint()
    del payload["commercial_sources"]
    del payload["citations"]
    del payload["weak_areas"]
    normalized = normalize_structured_blueprint_payload(payload)
    assert normalized["commercial_sources"] == []
    assert normalized["citations"] == []
    assert normalized["weak_areas"] == []
    assert "https://" not in json.dumps(normalized["commercial_sources"])


def test_country_containment_fields_remain_strict() -> None:
    payload = valid_structured_blueprint()
    del payload["country_dossier"]["country_iso3"]
    normalized = normalize_structured_blueprint_payload(payload)
    with pytest.raises(ValidationError):
        CountryMaximumCoverageStructuredBlueprint.model_validate(normalized)


def test_real_failure_shape_validates_after_deterministic_normalization() -> None:
    """Mirrors the live Stage 2 failure modes without inventing research facts."""
    payload = {
        "country_dossier": {
            "country_name": "Austria",
            "country_iso3": "AUT",
            "continent": "Europe",
        },
        "administrative_regions": ["Vienna"],
        "official_languages": ["German"],
        "regulatory_sources": [{"name": "AGES", "type": "regulator"}],
        "commercial_sources": [],
        "query_matrix": [
            {"query": "Austria inpatient rehab", "language": "German", "purpose": "discovery"}
        ],
        "region_coverage_plan": [
            {"region_name": "Vienna", "coverage_actions": ["Search official registry"]}
        ],
        "discovery_strategy": {"summary": "Official then commercial discovery"},
        "crawl_strategy": {"summary": "Deep crawl admissions pages"},
        "contact_completeness_strategy": {"summary": "Capture contacts with provenance"},
        "verification_rules": {"summary": "PASS/FAIL/UNKNOWN only"},
        "country_containment_rules": {"summary": "Physical address must be in Austria"},
        "deduplication_rules": {"summary": "Merge by canonical identity"},
        "confidence_model": {"summary": "Evidence-weighted confidence"},
        "completion_criteria": ["Cover all first-level regions"],
        "risks": ["Directory gaps"],
        "citations": [],
        "estimated_coverage": {"summary": "High in Vienna, lower rural"},
        "weak_areas": [],
        "human_review_questions": ["Confirm borderline clinics"],
        "approval_recommendation": {"ready": False, "reason": "Await human review"},
    }
    normalized = normalize_structured_blueprint_payload(payload)
    validated = CountryMaximumCoverageStructuredBlueprint.model_validate(normalized)
    assert validated.regions == ["Vienna"]
    assert validated.languages == ["German"]
    assert validated.regulatory_sources[0].title == "AGES"
    assert validated.regulatory_sources[0].source_type == "regulator"
    assert validated.regulatory_sources[0].url is None


@pytest.mark.asyncio
async def test_provider_validates_after_one_repair_using_exact_contract() -> None:
    from app.core.config import Settings
    from app.llm.providers import LLMResponse
    from app.services.scraping.blueprint_provider import OpenRouterBlueprintProvider

    class StubClient:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def complete(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return LLMResponse(text="Research", tokens_input=1, tokens_output=1)
            if len(self.calls) == 2:
                return LLMResponse(
                    text=json.dumps(
                        {
                            "country_dossier": {
                                "country_name": "Austria",
                                "country_iso3": "AUT",
                                "continent": "Europe",
                            },
                            "regulatory_sources": [{"name": "AGES", "type": "regulator"}],
                        }
                    ),
                    tokens_input=1,
                    tokens_output=1,
                )
            return LLMResponse(
                text=json.dumps(valid_structured_blueprint()),
                tokens_input=1,
                tokens_output=1,
            )

    client = StubClient()
    result = await OpenRouterBlueprintProvider(
        Settings(openrouter_api_key="test-key"), client=client
    ).generate_blueprint(
        mission=object(),
        rendered_prompt="Austria coverage",
        structured_output_schema=CountryMaximumCoverageStructuredBlueprint,
    )
    assert len(client.calls) == 3
    repair = client.calls[2]["user"]
    assert "Canonical JSON skeleton" in repair
    assert "Exact missing fields" in repair
    assert "regions" in repair
    assert "languages" in repair
    assert "extra fields" in repair.lower()
    assert "regulatory_sources.0.name" in repair or "name" in repair
    assert result.execution_metadata["structuring_correction_attempted"] is True


def test_correction_message_includes_exact_skeleton_and_gap_details() -> None:
    invalid = {
        "country_dossier": {
            "country_name": "Austria",
            "country_iso3": "AUT",
            "continent": "Europe",
        },
        "regulatory_sources": [{"name": "AGES", "type": "regulator"}],
    }
    with pytest.raises(ValidationError) as exc:
        CountryMaximumCoverageStructuredBlueprint.model_validate(invalid)
    message = OpenRouterBlueprintProvider._correction_user_message(json.dumps(invalid), exc.value)
    assert "Canonical JSON skeleton" in message
    assert "Exact missing fields" in message
    assert "regions" in message
    assert "languages" in message
    gap = describe_validation_contract_gap(invalid, exc.value)
    assert "regions" in gap["missing_fields"]
    assert "country_dossier" in gap["required_top_level_fields"]
