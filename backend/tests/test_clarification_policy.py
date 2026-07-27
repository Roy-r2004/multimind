"""Deterministic typed clarification policy coverage."""

from __future__ import annotations

import copy

from test_country_blueprint_foundation import valid_structured_blueprint

from app.services.scraping.blueprint_execution_plan_service import (
    MissionCountryIdentity,
    BlueprintExecutionPlanService,
)
from app.schemas.scraping_clarification import (
    ClarificationSafety,
    ClarificationType,
)
from app.services.scraping.clarification_policy_service import ClarificationPolicyService


def _austria_plan(**mutate):
    payload = valid_structured_blueprint()
    # Distinct source identities so default fixtures do not force category conflicts.
    payload["regulatory_sources"] = [
        {"url": "https://example.test/regulatory", "title": "Regulatory"}
    ]
    payload["commercial_sources"] = [
        {"url": "https://example.test/commercial", "title": "Commercial"}
    ]
    for key, value in mutate.items():
        payload[key] = value
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
        require_v2=False,
    )
    return compiled.frozen_execution_plan


def test_free_form_clarification_strings_are_informational_only() -> None:
    plan = _austria_plan(
        weak_areas=["Rural clinics"],
        human_review_questions=["Confirm private-pay scope."],
        approval_recommendation={"ready": False, "reason": "Needs review."},
    )
    analysis = ClarificationPolicyService().analyze(plan)
    texts = [note.text for note in analysis.informational_notes]
    assert any("Rural clinics" in text for text in texts)
    assert any("Confirm private-pay scope." in text for text in texts)
    assert analysis.safe_candidates == []
    assert analysis.human_review_findings == []
    # Re-analyze must not invent typed candidates from free-form prose.
    again = ClarificationPolicyService().analyze(plan)
    assert again.safe_candidates == []
    assert [note.text for note in again.informational_notes] == texts


def test_no_typed_ambiguity_means_not_required_inputs() -> None:
    plan = _austria_plan()
    analysis = ClarificationPolicyService().analyze(plan)
    assert analysis.safe_candidates == []
    assert analysis.human_review_findings == []
    assert analysis.deterministic_resolutions == []


def test_unique_region_alias_resolves_in_python() -> None:
    plan = _austria_plan(
        regions=["Vienna", "Tyrol"],
        region_coverage_plan=[
            {"region_name": "vienna city", "coverage_actions": ["Search registry"]}
        ],
    )
    analysis = ClarificationPolicyService().analyze(plan)
    assert len(analysis.deterministic_resolutions) == 1
    assert analysis.safe_candidates == []
    assert analysis.deterministic_resolutions[0].selected_value.value == "Vienna"


def test_multiple_region_matches_create_safe_candidate() -> None:
    plan = _austria_plan(
        regions=["Lower Austria", "Upper Austria"],
        region_coverage_plan=[
            {"region_name": "Austria", "coverage_actions": ["Search registry"]}
        ],
    )
    analysis = ClarificationPolicyService().analyze(plan)
    assert len(analysis.safe_candidates) == 1
    candidate = analysis.safe_candidates[0]
    assert candidate.clarification_type == ClarificationType.REGION_REFERENCE_ALIAS
    assert candidate.safety == ClarificationSafety.SAFE_MODEL_SELECTION
    assert {item.value for item in candidate.allowed_values} == {
        "Lower Austria",
        "Upper Austria",
    }


def test_missing_region_requires_human_review_without_new_region() -> None:
    plan = _austria_plan(
        regions=["Vienna"],
        region_coverage_plan=[
            {"region_name": "Salzburg", "coverage_actions": ["Search registry"]}
        ],
    )
    analysis = ClarificationPolicyService().analyze(plan)
    assert analysis.safe_candidates == []
    assert len(analysis.human_review_findings) == 1
    assert analysis.human_review_findings[0].allowed_values == []


def test_unrelated_region_tokens_do_not_create_broad_fuzzy_matches() -> None:
    """'district'/'area' do not substring-match 'Lower Beirut' etc. → human review."""
    plan = _austria_plan(
        regions=["Lower Beirut", "Upper Beirut", "East Beirut", "West Beirut"],
        region_coverage_plan=[
            {"region_name": "Beirut district", "coverage_actions": ["Search A"]},
            {"region_name": "Beirut area", "coverage_actions": ["Search B"]},
        ],
    )
    analysis = ClarificationPolicyService().analyze(plan)
    assert analysis.safe_candidates == []
    assert analysis.deterministic_resolutions == []
    assert len(analysis.human_review_findings) == 2
    for finding in analysis.human_review_findings:
        assert finding.allowed_values == []
        assert finding.safety == ClarificationSafety.REQUIRES_HUMAN_REVIEW


def test_two_independent_region_aliases_create_two_safe_candidates() -> None:
    plan = _austria_plan(
        regions=["Lower Beirut", "Upper Beirut", "Lower Sidon", "Upper Sidon"],
        region_coverage_plan=[
            {"region_name": "Beirut", "coverage_actions": ["Search A"]},
            {"region_name": "Sidon", "coverage_actions": ["Search B"]},
        ],
    )
    analysis = ClarificationPolicyService().analyze(plan)
    assert len(analysis.safe_candidates) == 2
    assert {item.value for item in analysis.safe_candidates[0].allowed_values} == {
        "Lower Beirut",
        "Upper Beirut",
    }
    assert {item.value for item in analysis.safe_candidates[1].allowed_values} == {
        "Lower Sidon",
        "Upper Sidon",
    }
    assert analysis.human_review_findings == []


def test_language_alias_and_missing_language_rules() -> None:
    analysis_unique = ClarificationPolicyService().analyze(
        _austria_plan(
            languages=["German"],
            query_matrix=[{"query": "rehab", "language": "Germ", "purpose": "discovery"}],
        )
    )
    assert len(analysis_unique.deterministic_resolutions) == 1
    assert analysis_unique.deterministic_resolutions[0].selected_value.value == "German"

    multi = ClarificationPolicyService().analyze(
        _austria_plan(
            languages=["German", "Germania"],
            query_matrix=[{"query": "rehab", "language": "Germ", "purpose": "discovery"}],
        )
    )
    assert len(multi.safe_candidates) == 1

    missing = ClarificationPolicyService().analyze(
        _austria_plan(
            languages=["German"],
            query_matrix=[{"query": "rehab", "language": "French", "purpose": "discovery"}],
        )
    )
    assert len(missing.human_review_findings) == 1
    assert missing.human_review_findings[0].allowed_values == []
    assert missing.safe_candidates == []


def test_source_category_conflict_creates_constrained_candidate() -> None:
    citation = {"url": "https://example.test/a", "title": "Shared"}
    plan = _austria_plan(
        regulatory_sources=[citation],
        commercial_sources=[citation],
    )
    analysis = ClarificationPolicyService().analyze(plan)
    assert len(analysis.safe_candidates) == 1
    candidate = analysis.safe_candidates[0]
    assert candidate.clarification_type == ClarificationType.SOURCE_CATEGORY_CONFLICT
    assert [item.value for item in candidate.allowed_values] == ["regulatory", "commercial"]


def test_candidate_ids_and_ordering_are_deterministic() -> None:
    plan = _austria_plan(
        regions=["Lower Austria", "Upper Austria"],
        region_coverage_plan=[
            {"region_name": "Austria", "coverage_actions": ["Search registry"]}
        ],
    )
    first = ClarificationPolicyService().analyze(plan)
    second = ClarificationPolicyService().analyze(copy.deepcopy(plan))
    assert [item.clarification_id for item in first.safe_candidates] == [
        item.clarification_id for item in second.safe_candidates
    ]
