"""Resolved-plan application and immutability coverage."""

from __future__ import annotations

import copy

import pytest
from test_country_blueprint_foundation import valid_structured_blueprint

from app.core.exceptions import ValidationError
from app.schemas.scraping_clarification import (
    ClarificationAllowedValue,
    ClarificationDecision,
    ClarificationType,
    ValidatedClarificationDecision,
)
from app.services.scraping.blueprint_execution_plan_service import (
    BlueprintExecutionPlanService,
    MissionCountryIdentity,
    sha256_hex,
)
from app.services.scraping.clarification_resolution_service import (
    ClarificationResolutionService,
)


def _plan():
    payload = valid_structured_blueprint()
    payload["regulatory_sources"] = [
        {"url": "https://example.test/regulatory", "title": "Regulatory"}
    ]
    payload["commercial_sources"] = [
        {"url": "https://example.test/commercial", "title": "Commercial"}
    ]
    compiled = BlueprintExecutionPlanService().compile(
        mission_id="mission-1",
        blueprint_id="blueprint-1",
        blueprint_version=3,
        mission_country=MissionCountryIdentity(
            country_code="AT",
            country_name="Austria",
            country_iso3="AUT",
            continent="Europe",
        ),
        structured_blueprint=payload,
    )
    return compiled.frozen_execution_plan, compiled.execution_plan_hash


def test_no_clarification_path_is_deterministic_copy() -> None:
    plan, plan_hash = _plan()
    original = copy.deepcopy(plan.model_dump(mode="json"))
    service = ClarificationResolutionService()
    first, first_hash = service.build_no_clarification_envelope(
        plan, source_execution_plan_hash=plan_hash
    )
    second, second_hash = service.build_no_clarification_envelope(
        plan, source_execution_plan_hash=plan_hash
    )
    assert plan.model_dump(mode="json") == original
    assert first.plan.model_dump(mode="json") == original
    assert first.applied_clarification_ids == []
    assert first.source_execution_plan_hash == plan_hash
    assert first_hash == second_hash
    assert first_hash == sha256_hex(first.model_dump(mode="json"))
    assert "resolved_execution_plan_hash" not in first.model_dump(mode="json")


def test_region_decision_changes_only_requested_field() -> None:
    plan, plan_hash = _plan()
    plan.regions = ["Vienna", "Tyrol"]
    plan.crawl_policy.region_coverage_actions = [
        {"region_name": "Austria", "coverage_actions": ["Search"]}
    ]
    original = copy.deepcopy(plan.model_dump(mode="json"))
    decision = ValidatedClarificationDecision(
        clarification_id="c" * 40,
        clarification_type=ClarificationType.REGION_REFERENCE_ALIAS,
        field_path="crawl_policy.region_coverage_actions[0].region_name",
        decision=ClarificationDecision.RESOLVED,
        selected_value=ClarificationAllowedValue(value="Vienna", label="Vienna"),
        reason="selected",
        confidence=1.0,
        source="provider",
    )
    envelope, _ = ClarificationResolutionService().apply(
        plan, [decision], source_execution_plan_hash=plan_hash
    )
    assert plan.model_dump(mode="json") == original
    assert envelope.plan.crawl_policy.region_coverage_actions[0]["region_name"] == "Vienna"
    assert envelope.plan.country == plan.country
    assert envelope.plan.qualification_policy == plan.qualification_policy
    assert envelope.plan.languages == plan.languages
    assert envelope.applied_clarification_ids == ["c" * 40]


def test_language_decision_changes_only_seed_language() -> None:
    plan, plan_hash = _plan()
    plan.languages = ["German", "English"]
    plan.query_seed_plan.seeds[0].language = "Germ"
    original = copy.deepcopy(plan.model_dump(mode="json"))
    decision = ValidatedClarificationDecision(
        clarification_id="d" * 40,
        clarification_type=ClarificationType.LANGUAGE_REFERENCE_ALIAS,
        field_path="query_seed_plan.seeds[0].language",
        decision=ClarificationDecision.RESOLVED,
        selected_value=ClarificationAllowedValue(value="German", label="German"),
        reason="selected",
        confidence=1.0,
        source="python_deterministic",
    )
    envelope, _ = ClarificationResolutionService().apply(
        plan, [decision], source_execution_plan_hash=plan_hash
    )
    assert plan.model_dump(mode="json") == original
    assert envelope.plan.query_seed_plan.seeds[0].language == "German"
    assert envelope.plan.query_seed_plan.seeds[0].query == plan.query_seed_plan.seeds[0].query


def test_country_region_language_qualification_url_mutations_rejected() -> None:
    plan, plan_hash = _plan()
    service = ClarificationResolutionService()
    with pytest.raises(ValidationError, match="region"):
        service.apply(
            plan,
            [
                ValidatedClarificationDecision(
                    clarification_id="c" * 40,
                    clarification_type=ClarificationType.REGION_REFERENCE_ALIAS,
                    field_path="crawl_policy.region_coverage_actions[0].region_name",
                    decision=ClarificationDecision.RESOLVED,
                    selected_value=ClarificationAllowedValue(value="Salzburg", label="Salzburg"),
                    reason="x",
                    confidence=1.0,
                )
            ],
            source_execution_plan_hash=plan_hash,
        )
    with pytest.raises(ValidationError, match="language"):
        plan.languages = ["German"]
        service.apply(
            plan,
            [
                ValidatedClarificationDecision(
                    clarification_id="e" * 40,
                    clarification_type=ClarificationType.LANGUAGE_REFERENCE_ALIAS,
                    field_path="query_seed_plan.seeds[0].language",
                    decision=ClarificationDecision.RESOLVED,
                    selected_value=ClarificationAllowedValue(value="French", label="French"),
                    reason="x",
                    confidence=1.0,
                )
            ],
            source_execution_plan_hash=plan_hash,
        )
    with pytest.raises(ValidationError, match="Duplicate"):
        decision = ValidatedClarificationDecision(
            clarification_id="d" * 40,
            clarification_type=ClarificationType.LANGUAGE_REFERENCE_ALIAS,
            field_path="query_seed_plan.seeds[0].language",
            decision=ClarificationDecision.RESOLVED,
            selected_value=ClarificationAllowedValue(value="German", label="German"),
            reason="x",
            confidence=1.0,
        )
        service.apply(plan, [decision, decision], source_execution_plan_hash=plan_hash)


def test_reapplying_completed_decisions_is_idempotent() -> None:
    plan, plan_hash = _plan()
    plan.regions = ["Vienna", "Tyrol"]
    plan.crawl_policy.region_coverage_actions = [
        {"region_name": "Austria", "coverage_actions": ["Search"]}
    ]
    decision = ValidatedClarificationDecision(
        clarification_id="c" * 40,
        clarification_type=ClarificationType.REGION_REFERENCE_ALIAS,
        field_path="crawl_policy.region_coverage_actions[0].region_name",
        decision=ClarificationDecision.RESOLVED,
        selected_value=ClarificationAllowedValue(value="Vienna", label="Vienna"),
        reason="selected",
        confidence=1.0,
    )
    service = ClarificationResolutionService()
    first, first_hash = service.apply(plan, [decision], source_execution_plan_hash=plan_hash)
    second, second_hash = service.apply(plan, [decision], source_execution_plan_hash=plan_hash)
    assert first_hash == second_hash
    assert first.plan.model_dump(mode="json") == second.plan.model_dump(mode="json")


def test_unresolved_decision_cannot_produce_resolved_plan() -> None:
    plan, plan_hash = _plan()
    with pytest.raises(ValidationError, match="RESOLVED"):
        ClarificationResolutionService().apply(
            plan,
            [
                ValidatedClarificationDecision(
                    clarification_id="c" * 40,
                    clarification_type=ClarificationType.REGION_REFERENCE_ALIAS,
                    field_path="crawl_policy.region_coverage_actions[0].region_name",
                    decision=ClarificationDecision.UNRESOLVED,
                    selected_value=None,
                    reason="unclear",
                    confidence=0.1,
                    requires_human_review=True,
                )
            ],
            source_execution_plan_hash=plan_hash,
        )
