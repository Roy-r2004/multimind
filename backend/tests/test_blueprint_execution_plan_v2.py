"""Focused structured-blueprint and FrozenExecutionPlan v2 contract tests (not run here)."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError as PydanticValidationError
from test_country_blueprint_foundation import (
    valid_structured_blueprint,
    valid_structured_blueprint_v2,
)

from app.core.exceptions import ValidationError
from app.schemas.api import (
    CountryMaximumCoverageStructuredBlueprint,
    CountryMaximumCoverageStructuredBlueprintV2,
)
from app.schemas.scraping_execution_plan import (
    CONTACT_PRODUCT_RULES,
    COUNTRY_CONTAINMENT_PRODUCT_RULES,
    DEDUPLICATION_PRODUCT_RULES,
    EXECUTION_PLAN_SCHEMA_VERSION_V1,
    EXECUTION_PLAN_SCHEMA_VERSION_V2,
    FrozenExecutionPlan,
    FrozenExecutionPlanV2,
    QUALIFICATION_OVERALL_RULE,
    REQUIRED_QUALIFICATION_CRITERIA,
    QualificationStatus,
    parse_frozen_execution_plan,
    supports_deterministic_query_generation,
)
from app.services.scraping.blueprint_execution_plan_service import (
    BlueprintExecutionPlanService,
    MissionCountryIdentity,
    sha256_hex,
)
from app.services.scraping.blueprint_prompt_service import (
    COUNTRY_MAXIMUM_COVERAGE_TEMPLATE,
    COUNTRY_MAXIMUM_COVERAGE_TEMPLATE_V1,
    COUNTRY_MAXIMUM_COVERAGE_TEMPLATE_VERSION,
    COUNTRY_MAXIMUM_COVERAGE_TEMPLATE_VERSION_V1,
    BlueprintPromptService,
)
from app.services.scraping.blueprint_provider import OpenRouterBlueprintProvider
from app.services.scraping.blueprint_structured_contract import (
    detect_structured_blueprint_schema_version,
    normalize_structured_blueprint_payload,
    parse_structured_blueprint,
    validate_structured_blueprint_for_campaign,
)
from app.services.scraping.clarification_resolution_service import (
    ClarificationResolutionService,
)
from app.services.scraping.countries import resolve_country

AUSTRIA = MissionCountryIdentity(
    country_code="AT",
    country_name="Austria",
    country_iso3="AUT",
    continent="Europe",
)

# Fixed historical v1 frozen-plan fixture (compiler-emitted shape). Hash must stay stable.
_HISTORICAL_V1_FROZEN_PLAN: dict = {
    "schema_version": "1",
    "mission_id": "mission-hist-1",
    "blueprint_id": "blueprint-hist-1",
    "blueprint_version": 1,
    "source_blueprint_hash": "a" * 64,
    "country": {
        "country_code": "AT",
        "country_name": "Austria",
        "country_iso3": "AUT",
        "continent": "Europe",
    },
    "scope": {
        "mission_id": "mission-hist-1",
        "blueprint_id": "blueprint-hist-1",
        "blueprint_version": 1,
        "discovery_strategy_summary": "Discover",
        "estimated_coverage_summary": "Coverage",
    },
    "regions": ["Vienna"],
    "languages": ["German"],
    "terminology": ["Suchtbehandlung"],
    "source_plan": {
        "regulatory_sources": [
            {
                "category": "regulatory",
                "url": None,
                "title": "Ministry",
                "source_type": "regulator",
                "notes": None,
            }
        ],
        "commercial_sources": [],
    },
    "query_seed_plan": {
        "seeds": [
            {"query": "Austria rehab", "language": "German", "purpose": "discovery"}
        ]
    },
    "crawl_policy": {
        "summary": "Crawl",
        "region_coverage_actions": [
            {"region_name": "Vienna", "coverage_actions": ["Search registry"]}
        ],
    },
    "contact_policy": {
        "product_rules": list(CONTACT_PRODUCT_RULES),
        "blueprint_summary": "Contacts",
    },
    "qualification_policy": {
        "statuses": [status.value for status in QualificationStatus],
        "required_criteria": [
            {
                "criterion_id": criterion_id,
                "required": True,
                "allowed_statuses": [status.value for status in QualificationStatus],
            }
            for criterion_id in REQUIRED_QUALIFICATION_CRITERIA
        ],
        "overall_rule": QUALIFICATION_OVERALL_RULE,
        "blueprint_verification_summary": "Verify",
    },
    "country_containment_policy": {
        "product_rules": list(COUNTRY_CONTAINMENT_PRODUCT_RULES),
        "blueprint_summary": "Contain",
    },
    "evidence_policy": {
        "blueprint_confidence_summary": "Confidence",
        "preserve_source_urls": True,
        "invent_values": False,
    },
    "deduplication_policy": {
        "product_rules": list(DEDUPLICATION_PRODUCT_RULES),
        "blueprint_summary": "Dedupe",
    },
    "completion_policy": {"criteria": ["Cover regions"]},
    "risks": ["Gaps"],
    "weak_areas": ["Unindexed"],
    "human_review_questions": ["Review?"],
    "compiler_warnings": [],
    "clarification_questions": [],
}

_HISTORICAL_V1_PLAN_HASH = sha256_hex(copy.deepcopy(_HISTORICAL_V1_FROZEN_PLAN))


def _compile_v2(payload: dict):
    return BlueprintExecutionPlanService().compile(
        mission_id="mission-1",
        blueprint_id="blueprint-1",
        blueprint_version=3,
        mission_country=AUSTRIA,
        structured_blueprint=payload,
        require_v2=True,
    )


# --- Blueprint version detection -------------------------------------------------


def test_blueprint_without_schema_version_is_v1() -> None:
    payload = valid_structured_blueprint()
    assert "schema_version" not in payload
    assert detect_structured_blueprint_schema_version(payload) == "1"
    parsed = parse_structured_blueprint(payload)
    assert isinstance(parsed, CountryMaximumCoverageStructuredBlueprint)
    dumped = parsed.model_dump(mode="json", exclude_none=True)
    dumped.pop("schema_version", None)
    assert "important_cities" not in dumped
    assert "local_terminology" not in dumped
    assert "addiction_categories" not in dumped


def test_blueprint_explicit_string_one_is_v1() -> None:
    payload = valid_structured_blueprint()
    payload["schema_version"] = "1"
    assert detect_structured_blueprint_schema_version(payload) == "1"
    assert isinstance(parse_structured_blueprint(payload), CountryMaximumCoverageStructuredBlueprint)


def test_blueprint_explicit_string_two_is_v2() -> None:
    payload = valid_structured_blueprint_v2()
    assert detect_structured_blueprint_schema_version(payload) == "2"
    assert isinstance(parse_structured_blueprint(payload), CountryMaximumCoverageStructuredBlueprintV2)


@pytest.mark.parametrize(
    "bad_version",
    [1, 2, "3", "", " ", None, [], {}, True, 2.0, object()],
)
def test_blueprint_unsupported_versions_are_rejected(bad_version: object) -> None:
    payload = valid_structured_blueprint()
    payload["schema_version"] = bad_version
    with pytest.raises(ValueError, match="Unsupported structured blueprint schema version"):
        detect_structured_blueprint_schema_version(payload)
    with pytest.raises(ValueError, match="Unsupported structured blueprint schema version"):
        parse_structured_blueprint(payload)
    # Unknown version key must remain; never reinterpret as missing-key v1.
    assert payload["schema_version"] is bad_version


def test_blueprint_parser_does_not_mutate_caller_payload() -> None:
    payload = valid_structured_blueprint_v2()
    original = copy.deepcopy(payload)
    parse_structured_blueprint(payload)
    normalize_structured_blueprint_payload(payload)
    assert payload == original


def test_normalize_v1_does_not_invent_v2_fields() -> None:
    normalized = normalize_structured_blueprint_payload(valid_structured_blueprint())
    assert "schema_version" not in normalized
    assert "important_cities" not in normalized
    assert "language_profiles" not in normalized


def test_normalize_v2_does_not_invent_missing_required_lists() -> None:
    payload = valid_structured_blueprint_v2()
    del payload["addiction_categories"]
    normalized = normalize_structured_blueprint_payload(payload)
    assert "addiction_categories" not in normalized


# --- Frozen plan version detection -----------------------------------------------


def test_frozen_plan_string_versions_parse() -> None:
    v1 = parse_frozen_execution_plan(copy.deepcopy(_HISTORICAL_V1_FROZEN_PLAN))
    assert isinstance(v1, FrozenExecutionPlan)
    assert v1.schema_version == "1"
    compiled = _compile_v2(valid_structured_blueprint_v2())
    v2 = parse_frozen_execution_plan(compiled.frozen_execution_plan_json)
    assert isinstance(v2, FrozenExecutionPlanV2)
    assert v2.schema_version == "2"


@pytest.mark.parametrize("bad_version", [1, 2, "3", None, "", []])
def test_frozen_plan_unsupported_versions_rejected(bad_version: object) -> None:
    raw = copy.deepcopy(_HISTORICAL_V1_FROZEN_PLAN)
    if bad_version is None and "schema_version" in raw:
        # Explicit null is unsupported; missing key tested separately.
        raw["schema_version"] = None
    else:
        raw["schema_version"] = bad_version
    with pytest.raises(ValueError, match="schema version|schema_version"):
        parse_frozen_execution_plan(raw)


def test_frozen_plan_missing_version_rejected() -> None:
    raw = copy.deepcopy(_HISTORICAL_V1_FROZEN_PLAN)
    del raw["schema_version"]
    with pytest.raises(ValueError, match="schema_version is required"):
        parse_frozen_execution_plan(raw)


def test_v2_plan_cannot_parse_as_v1_model() -> None:
    compiled = _compile_v2(valid_structured_blueprint_v2())
    raw = compiled.frozen_execution_plan_json
    with pytest.raises(PydanticValidationError):
        FrozenExecutionPlan.model_validate(raw)
    assert isinstance(parse_frozen_execution_plan(raw), FrozenExecutionPlanV2)


# --- V2 completeness / campaign compile ------------------------------------------


def test_blueprint_v2_accepts_required_dimensions() -> None:
    parsed = parse_structured_blueprint(valid_structured_blueprint_v2())
    assert isinstance(parsed, CountryMaximumCoverageStructuredBlueprintV2)
    assert parsed.schema_version == "2"
    assert parsed.important_cities[0].region_name == "Vienna"
    assert "Suchtbehandlung" in parsed.local_terminology


@pytest.mark.parametrize(
    "field",
    [
        "important_cities",
        "language_profiles",
        "local_terminology",
        "inpatient_residential_terminology",
        "private_paid_terminology",
        "addiction_categories",
    ],
)
def test_blueprint_v2_empty_required_list_rejected(field: str) -> None:
    payload = valid_structured_blueprint_v2()
    payload[field] = []
    with pytest.raises(PydanticValidationError):
        CountryMaximumCoverageStructuredBlueprintV2.model_validate(
            normalize_structured_blueprint_payload(payload)
        )
    with pytest.raises((ValidationError, ValueError, PydanticValidationError)):
        validate_structured_blueprint_for_campaign(payload)


def test_blueprint_v2_blank_terminology_fails_at_compile() -> None:
    payload = valid_structured_blueprint_v2()
    payload["local_terminology"] = ["  "]
    with pytest.raises(ValidationError) as exc_info:
        _compile_v2(payload)
    # Blank tokens are stripped during normalize → empty list. Campaign compile wraps the
    # resulting Pydantic min_length failure as a domain ValidationError (before semantic
    # "Local terminology is required." messaging).
    cause = exc_info.value.__cause__
    assert isinstance(cause, PydanticValidationError)
    assert any(
        "local_terminology" in ".".join(str(part) for part in error.get("loc", ()))
        for error in cause.errors()
    )


def test_blueprint_v2_rejects_wrong_city_region() -> None:
    payload = valid_structured_blueprint_v2()
    payload["important_cities"] = [{"name": "Vienna", "region_name": "Tyrol"}]
    with pytest.raises(ValidationError, match="parent region"):
        _compile_v2(payload)


def test_blueprint_v2_dedupes_identical_city_region_pairs() -> None:
    payload = valid_structured_blueprint_v2()
    payload["important_cities"] = [
        {"name": "Vienna", "region_name": "Vienna"},
        {"name": " Vienna ", "region_name": "Vienna"},
        {"name": "vienna", "region_name": "Vienna"},
    ]
    plan = _compile_v2(payload).frozen_execution_plan
    assert isinstance(plan, FrozenExecutionPlanV2)
    assert len(plan.important_cities) == 1


def test_blueprint_v2_rejects_conflicting_city_parents() -> None:
    payload = valid_structured_blueprint_v2()
    payload["regions"] = ["Vienna", "Tyrol"]
    payload["important_cities"] = [
        {"name": "Innsbruck", "region_name": "Vienna"},
        {"name": "Innsbruck", "region_name": "Tyrol"},
    ]
    with pytest.raises(ValidationError, match="conflicting parent"):
        _compile_v2(payload)


def test_language_profiles_must_cover_languages_without_inferring_codes() -> None:
    payload = valid_structured_blueprint_v2()
    payload["language_profiles"] = [{"name": "German", "code": None, "script": None}]
    plan = _compile_v2(payload).frozen_execution_plan
    assert isinstance(plan, FrozenExecutionPlanV2)
    assert plan.language_profiles[0].code is None
    assert plan.language_profiles[0].script is None

    payload["language_profiles"] = [{"name": "French", "code": "fr", "script": None}]
    with pytest.raises(ValidationError, match="Language profile"):
        _compile_v2(payload)


def test_language_profiles_reject_duplicate_non_null_codes() -> None:
    payload = valid_structured_blueprint_v2()
    payload["languages"] = ["German", "English"]
    payload["language_profiles"] = [
        {"name": "German", "code": "de", "script": None},
        {"name": "English", "code": "de", "script": None},
    ]
    with pytest.raises(ValidationError, match="Duplicate language code"):
        _compile_v2(payload)


def test_compiler_v2_is_deterministic_and_preserves_seeds_and_categories() -> None:
    payload = valid_structured_blueprint_v2()
    first = _compile_v2(payload)
    second = _compile_v2(copy.deepcopy(payload))
    assert first.execution_plan_hash == second.execution_plan_hash
    assert first.frozen_execution_plan_json == second.frozen_execution_plan_json
    plan = first.frozen_execution_plan
    assert isinstance(plan, FrozenExecutionPlanV2)
    assert plan.schema_version == EXECUTION_PLAN_SCHEMA_VERSION_V2
    assert plan.query_seed_plan.seeds[0].query
    assert plan.source_plan.regulatory_sources[0].category == "regulatory"
    assert plan.source_plan.commercial_sources[0].category == "commercial"
    assert plan.local_terminology
    assert plan.terminology == plan.local_terminology
    assert plan.inpatient_residential_terminology
    assert plan.private_paid_terminology
    assert plan.addiction_categories
    assert plan.important_cities


def test_v1_plan_parse_does_not_add_v2_keys_or_change_hash() -> None:
    raw = copy.deepcopy(_HISTORICAL_V1_FROZEN_PLAN)
    assert raw["schema_version"] == EXECUTION_PLAN_SCHEMA_VERSION_V1
    assert "important_cities" not in raw
    assert "addiction_categories" not in raw
    assert sha256_hex(raw) == _HISTORICAL_V1_PLAN_HASH
    parsed = parse_frozen_execution_plan(raw)
    assert isinstance(parsed, FrozenExecutionPlan)
    assert parsed.schema_version == "1"
    assert "important_cities" not in parsed.model_dump(mode="json")
    assert sha256_hex(parsed.model_dump(mode="json")) == _HISTORICAL_V1_PLAN_HASH


def test_campaign_compile_rejects_v1_blueprint() -> None:
    with pytest.raises(ValidationError, match="schema version 2"):
        BlueprintExecutionPlanService().compile(
            mission_id="mission-1",
            blueprint_id="blueprint-1",
            blueprint_version=3,
            mission_country=AUSTRIA,
            structured_blueprint=valid_structured_blueprint(),
            require_v2=True,
        )


def test_campaign_compile_rejects_numeric_blueprint_version() -> None:
    payload = valid_structured_blueprint_v2()
    payload["schema_version"] = 2
    with pytest.raises(ValidationError, match="Unsupported structured blueprint schema version"):
        _compile_v2(payload)


# --- Prompt / provider ------------------------------------------------------------


def test_active_generation_uses_v2_prompt() -> None:
    assert COUNTRY_MAXIMUM_COVERAGE_TEMPLATE.endswith("_v2.jinja2")
    assert COUNTRY_MAXIMUM_COVERAGE_TEMPLATE_VERSION.endswith("_v2")
    assert COUNTRY_MAXIMUM_COVERAGE_TEMPLATE_V1.endswith("_v1.jinja2")
    assert COUNTRY_MAXIMUM_COVERAGE_TEMPLATE_VERSION_V1.endswith("_v1")
    rendered = BlueprintPromptService().render_country_maximum_coverage(
        mission_title="Austria coverage",
        country=resolve_country("AT"),
    )
    assert rendered.template_version == "country_maximum_coverage_blueprint_v2"
    assert '"schema_version": "2"' in rendered.rendered_prompt
    assert '"important_cities"' in rendered.rendered_prompt
    assert '"language_profiles"' in rendered.rendered_prompt
    assert '"addiction_categories"' in rendered.rendered_prompt


def test_provider_rejects_missing_schema_version_without_injecting() -> None:
    payload = valid_structured_blueprint_v2()
    del payload["schema_version"]
    text = __import__("json").dumps(payload)
    with pytest.raises(ValueError, match='string "2"'):
        OpenRouterBlueprintProvider._validate_structured(
            text, CountryMaximumCoverageStructuredBlueprintV2
        )


def test_provider_repair_guidance_requires_explicit_v2() -> None:
    payload = valid_structured_blueprint()
    message = OpenRouterBlueprintProvider._correction_user_message(
        __import__("json").dumps(payload),
        ValueError('schema_version must be the string "2"'),
    )
    assert '"schema_version": "2"' in message
    assert "Emit schema_version as the string" in message
    assert "schema_version" in message


def test_worker_readable_is_not_step3_capability() -> None:
    assert supports_deterministic_query_generation("2") is True
    assert supports_deterministic_query_generation("1") is False
    assert supports_deterministic_query_generation(None) is False


# --- Step 2 preservation ----------------------------------------------------------


def test_language_profiles_must_cover_every_approved_language() -> None:
    payload = valid_structured_blueprint_v2()
    payload["languages"] = ["German", "English"]
    payload["language_profiles"] = [{"name": "German", "code": "de", "script": None}]
    with pytest.raises(ValidationError, match="Language profiles must cover"):
        _compile_v2(payload)


def test_clarification_preserves_v2_fields_on_not_required_and_completed() -> None:
    compiled = _compile_v2(valid_structured_blueprint_v2())
    plan = compiled.frozen_execution_plan
    assert isinstance(plan, FrozenExecutionPlanV2)
    frozen_hash = compiled.execution_plan_hash
    frozen_json = copy.deepcopy(compiled.frozen_execution_plan_json)

    service = ClarificationResolutionService()
    envelope, resolved_hash = service.build_no_clarification_envelope(
        plan, source_execution_plan_hash=frozen_hash
    )
    assert isinstance(envelope.plan, FrozenExecutionPlanV2)
    assert envelope.plan.important_cities == plan.important_cities
    assert envelope.plan.language_profiles == plan.language_profiles
    assert envelope.plan.local_terminology == plan.local_terminology
    assert envelope.plan.terminology == plan.terminology
    assert envelope.plan.inpatient_residential_terminology == plan.inpatient_residential_terminology
    assert envelope.plan.private_paid_terminology == plan.private_paid_terminology
    assert envelope.plan.addiction_categories == plan.addiction_categories
    assert sha256_hex(frozen_json) == frozen_hash
    assert resolved_hash == sha256_hex(envelope.model_dump(mode="json"))

    envelope2, _ = service.apply(plan, [], source_execution_plan_hash=frozen_hash)
    assert isinstance(envelope2.plan, FrozenExecutionPlanV2)
    assert envelope2.plan.private_paid_terminology == plan.private_paid_terminology
    assert envelope2.plan.terminology == plan.terminology
    assert envelope2.plan.addiction_categories == plan.addiction_categories
    assert sha256_hex(frozen_json) == frozen_hash
