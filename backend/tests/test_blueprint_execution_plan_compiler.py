"""Pure compiler coverage for approved blueprint → FrozenExecutionPlan."""

from __future__ import annotations

import copy

import pytest
from test_country_blueprint_foundation import valid_structured_blueprint

from app.core.exceptions import ValidationError
from app.schemas.scraping_execution_plan import (
    EXECUTION_PLAN_SCHEMA_VERSION_V1,
    REQUIRED_QUALIFICATION_CRITERIA,
    FrozenExecutionPlan,
    QualificationStatus,
)
from app.services.scraping.blueprint_execution_plan_service import (
    MissionCountryIdentity,
    BlueprintExecutionPlanService,
    sha256_hex,
)

AUSTRIA = MissionCountryIdentity(
    country_code="AT",
    country_name="Austria",
    country_iso3="AUT",
    continent="Europe",
)


def _compile(payload: dict, *, country: MissionCountryIdentity = AUSTRIA, require_v2: bool = False):
    return BlueprintExecutionPlanService().compile(
        mission_id="mission-1",
        blueprint_id="blueprint-1",
        blueprint_version=3,
        mission_country=country,
        structured_blueprint=payload,
        require_v2=require_v2,
    )


def test_valid_structured_blueprint_compiles() -> None:
    result = _compile(valid_structured_blueprint())
    plan = result.frozen_execution_plan
    assert isinstance(plan, FrozenExecutionPlan)
    assert plan.schema_version == EXECUTION_PLAN_SCHEMA_VERSION_V1
    assert plan.country.country_code == "AT"
    assert plan.regions == ["Vienna"]
    assert plan.languages == ["German"]
    assert plan.query_seed_plan.seeds
    assert plan.qualification_policy.statuses == [
        QualificationStatus.PASS,
        QualificationStatus.FAIL,
        QualificationStatus.UNKNOWN,
    ]
    assert [item.criterion_id for item in plan.qualification_policy.required_criteria] == list(
        REQUIRED_QUALIFICATION_CRITERIA
    )


def test_compilation_is_deterministic() -> None:
    payload = valid_structured_blueprint()
    first = _compile(payload)
    second = _compile(payload)
    assert first.frozen_execution_plan_json == second.frozen_execution_plan_json
    assert first.execution_plan_hash == second.execution_plan_hash
    assert first.source_blueprint_hash == second.source_blueprint_hash


def test_compiler_does_not_mutate_input() -> None:
    payload = valid_structured_blueprint()
    original = copy.deepcopy(payload)
    _compile(payload)
    assert payload == original


def test_stable_hash_across_dictionary_key_order() -> None:
    payload_a = valid_structured_blueprint()
    payload_b = {
        "approval_recommendation": payload_a["approval_recommendation"],
        "weak_areas": payload_a["weak_areas"],
        "country_dossier": payload_a["country_dossier"],
        "completion_criteria": payload_a["completion_criteria"],
        "regions": payload_a["regions"],
        "languages": payload_a["languages"],
        "regulatory_sources": payload_a["regulatory_sources"],
        "commercial_sources": payload_a["commercial_sources"],
        "query_matrix": payload_a["query_matrix"],
        "region_coverage_plan": payload_a["region_coverage_plan"],
        "discovery_strategy": payload_a["discovery_strategy"],
        "crawl_strategy": payload_a["crawl_strategy"],
        "contact_completeness_strategy": payload_a["contact_completeness_strategy"],
        "verification_rules": payload_a["verification_rules"],
        "country_containment_rules": payload_a["country_containment_rules"],
        "deduplication_rules": payload_a["deduplication_rules"],
        "confidence_model": payload_a["confidence_model"],
        "risks": payload_a["risks"],
        "citations": payload_a["citations"],
        "estimated_coverage": payload_a["estimated_coverage"],
        "human_review_questions": payload_a["human_review_questions"],
    }
    first = _compile(payload_a)
    second = _compile(payload_b)
    assert first.execution_plan_hash == second.execution_plan_hash
    assert first.source_blueprint_hash == second.source_blueprint_hash


def test_mission_blueprint_country_mismatch_rejected() -> None:
    payload = valid_structured_blueprint()
    with pytest.raises(ValidationError, match="country"):
        _compile(
            payload,
            country=MissionCountryIdentity(
                country_code="LB",
                country_name="Lebanon",
                country_iso3="LBN",
                continent="Asia",
            ),
        )


def test_iso_mismatch_rejected() -> None:
    with pytest.raises(ValidationError, match="ISO-3"):
        _compile(
            valid_structured_blueprint(),
            country=MissionCountryIdentity(
                country_code="AT",
                country_name="Austria",
                country_iso3="LBN",
                continent="Europe",
            ),
        )


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda p: p.update({"regions": []}), "region"),
        (lambda p: p.update({"languages": []}), "language"),
        (lambda p: p.update({"query_matrix": []}), "query"),
        (
            lambda p: p.update({"regulatory_sources": [], "commercial_sources": []}),
            "source strategy",
        ),
        (lambda p: p.update({"country_containment_rules": {"summary": "   "}}), "containment"),
        (lambda p: p.update({"verification_rules": {"summary": "  "}}), "Verification"),
        (lambda p: p.update({"completion_criteria": []}), "Completion"),
    ],
)
def test_material_scope_errors_rejected(mutate, match: str) -> None:
    payload = valid_structured_blueprint()
    mutate(payload)
    with pytest.raises(ValidationError, match=match):
        _compile(payload)


def test_duplicates_removed_preserving_first_seen_order() -> None:
    payload = valid_structured_blueprint()
    payload["regions"] = ["Vienna", "vienna", "Tyrol", "Vienna"]
    payload["languages"] = ["German", "german", "English"]
    payload["country_dossier"]["local_term"] = "Suchtbehandlung"
    payload["country_dossier"]["alias_term"] = "Suchtbehandlung"
    payload["regulatory_sources"] = [
        {"url": "https://example.test/a", "title": "A"},
        {"url": "https://example.test/a", "title": "A"},
        {"url": None, "title": "Named only"},
    ]
    payload["commercial_sources"] = [{"url": None, "title": "Named only"}]
    payload["query_matrix"] = [
        {"query": "rehab", "language": "German", "purpose": "discovery"},
        {"query": "Rehab", "language": "german", "purpose": "discovery"},
        {"query": "clinic", "language": "German", "purpose": "discovery"},
    ]
    plan = _compile(payload).frozen_execution_plan
    assert plan.regions == ["Vienna", "Tyrol"]
    assert plan.languages == ["German", "English"]
    assert plan.terminology == ["Suchtbehandlung"]
    assert len(plan.source_plan.regulatory_sources) == 2
    assert [seed.query for seed in plan.query_seed_plan.seeds] == ["rehab", "clinic"]


def test_null_source_urls_preserved_and_nothing_invented() -> None:
    payload = valid_structured_blueprint()
    payload["regulatory_sources"] = [{"url": None, "title": "Regulator"}]
    payload["commercial_sources"] = [{"url": None, "title": "Directory"}]
    result = _compile(payload)
    plan = result.frozen_execution_plan
    assert all(entry.url is None for entry in plan.source_plan.regulatory_sources)
    assert all(entry.url is None for entry in plan.source_plan.commercial_sources)
    dumped = result.frozen_execution_plan_json
    assert "https://" not in str(dumped.get("source_plan"))
    assert plan.evidence_policy.invent_values is False
    assert "null URL" in " ".join(plan.compiler_warnings)


def test_non_fatal_ambiguity_becomes_warnings_or_clarifications() -> None:
    payload = valid_structured_blueprint()
    payload["commercial_sources"] = []
    payload["approval_recommendation"] = {"ready": False, "reason": "Needs human review."}
    payload["human_review_questions"] = ["Confirm private-pay scope."]
    payload["weak_areas"] = ["Rural clinics"]
    plan = _compile(payload).frozen_execution_plan
    assert any("commercial" in warning.lower() for warning in plan.compiler_warnings)
    assert any("Confirm private-pay scope." in item for item in plan.clarification_questions)
    assert any("Rural clinics" in item for item in plan.clarification_questions)
    assert any("not ready" in item.lower() for item in plan.clarification_questions)


def test_product_qualification_rules_always_present() -> None:
    plan = _compile(valid_structured_blueprint()).frozen_execution_plan
    assert plan.qualification_policy.overall_rule
    assert {item.criterion_id for item in plan.qualification_policy.required_criteria} == set(
        REQUIRED_QUALIFICATION_CRITERIA
    )


def test_execution_plan_hash_excludes_self_and_matches_canonical_dump() -> None:
    result = _compile(valid_structured_blueprint())
    assert "execution_plan_hash" not in result.frozen_execution_plan_json
    assert result.execution_plan_hash == sha256_hex(result.frozen_execution_plan_json)
    assert result.source_blueprint_hash == sha256_hex(result.blueprint_snapshot_json)
