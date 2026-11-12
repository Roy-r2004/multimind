"""Phase 2 coverage: AI Debate Mode structured output contracts.

Pure parsing/validation tests — no LLM call, database write, or network
access. Mirrors how app.llm.providers.LLMProvider.parse_json_object_lenient
is already exercised for Verdict parsing in the orchestrator, applied here
to the new, isolated Round-2/Round-3 debate contracts.
"""

import json

import pytest

from app.llm.debate_contracts import (
    Agreement,
    ChallengeSet,
    Correction,
    DebateConclusion,
    DebateContractError,
    DebateFindings,
    Risk,
    Uncertainty,
    UnresolvedDisagreement,
    parse_challenge_output,
    parse_conclusion_output,
    parse_response_output,
)

# --- Round 2: challenge output parsing --------------------------------------


def test_parse_challenge_output_valid():
    raw = json.dumps(
        {
            "author_model_id": "model-a",
            "challenges": [
                {
                    "challenge_id": "c1",
                    "target_model_id": "model-b",
                    "issue": "Missed a cost caveat",
                    "reasoning": "The answer ignored the stated budget.",
                    "severity": "medium",
                }
            ],
        }
    )
    result = parse_challenge_output(
        raw, expected_author_model_id="model-a", known_target_model_ids={"model-b", "model-c"}
    )
    assert isinstance(result, ChallengeSet)
    assert result.challenges[0].challenge_id == "c1"
    assert result.challenges[0].uncertainty is None


def test_parse_challenge_output_allows_zero_challenges():
    raw = json.dumps({"author_model_id": "model-a", "challenges": []})
    result = parse_challenge_output(
        raw, expected_author_model_id="model-a", known_target_model_ids={"model-b"}
    )
    assert result.challenges == []


def test_parse_challenge_output_tolerates_code_fence_and_prose_wrapping():
    raw = (
        "Sure, here is my review:\n```json\n"
        '{"author_model_id": "model-a", "challenges": []}\n```\n'
        "Let me know if you need more."
    )
    result = parse_challenge_output(
        raw, expected_author_model_id="model-a", known_target_model_ids={"model-b"}
    )
    assert result.challenges == []


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        "",
        "{ this is not valid json",
    ],
)
def test_parse_challenge_output_rejects_malformed_json(raw):
    with pytest.raises(DebateContractError):
        parse_challenge_output(
            raw, expected_author_model_id="model-a", known_target_model_ids={"model-b"}
        )


def test_parse_challenge_output_rejects_incomplete_object_missing_required_field():
    raw = json.dumps({"challenges": []})  # missing author_model_id
    with pytest.raises(DebateContractError):
        parse_challenge_output(
            raw, expected_author_model_id="model-a", known_target_model_ids={"model-b"}
        )


def test_parse_challenge_output_rejects_mismatched_author():
    raw = json.dumps({"author_model_id": "model-x", "challenges": []})
    with pytest.raises(DebateContractError):
        parse_challenge_output(
            raw, expected_author_model_id="model-a", known_target_model_ids={"model-b"}
        )


def test_parse_challenge_output_rejects_unknown_target_model_id():
    raw = json.dumps(
        {
            "author_model_id": "model-a",
            "challenges": [
                {
                    "challenge_id": "c1",
                    "target_model_id": "model-not-in-council",
                    "issue": "x",
                    "reasoning": "y",
                    "severity": "low",
                }
            ],
        }
    )
    with pytest.raises(DebateContractError):
        parse_challenge_output(
            raw, expected_author_model_id="model-a", known_target_model_ids={"model-b"}
        )


def test_parse_challenge_output_rejects_self_targeting():
    raw = json.dumps(
        {
            "author_model_id": "model-a",
            "challenges": [
                {
                    "challenge_id": "c1",
                    "target_model_id": "model-a",
                    "issue": "x",
                    "reasoning": "y",
                    "severity": "low",
                }
            ],
        }
    )
    with pytest.raises(DebateContractError):
        parse_challenge_output(
            raw, expected_author_model_id="model-a", known_target_model_ids={"model-a", "model-b"}
        )


def test_parse_challenge_output_rejects_duplicate_challenge_ids():
    raw = json.dumps(
        {
            "author_model_id": "model-a",
            "challenges": [
                {
                    "challenge_id": "c1",
                    "target_model_id": "model-b",
                    "issue": "x",
                    "reasoning": "y",
                    "severity": "low",
                },
                {
                    "challenge_id": "c1",
                    "target_model_id": "model-b",
                    "issue": "x2",
                    "reasoning": "y2",
                    "severity": "high",
                },
            ],
        }
    )
    with pytest.raises(DebateContractError):
        parse_challenge_output(
            raw, expected_author_model_id="model-a", known_target_model_ids={"model-b"}
        )


@pytest.mark.parametrize("severity", ["low", "medium", "high"])
def test_parse_challenge_output_accepts_all_valid_severities(severity):
    raw = json.dumps(
        {
            "author_model_id": "model-a",
            "challenges": [
                {
                    "challenge_id": "c1",
                    "target_model_id": "model-b",
                    "issue": "x",
                    "reasoning": "y",
                    "severity": severity,
                }
            ],
        }
    )
    result = parse_challenge_output(
        raw, expected_author_model_id="model-a", known_target_model_ids={"model-b"}
    )
    assert result.challenges[0].severity == severity


def test_parse_challenge_output_rejects_invalid_severity():
    raw = json.dumps(
        {
            "author_model_id": "model-a",
            "challenges": [
                {
                    "challenge_id": "c1",
                    "target_model_id": "model-b",
                    "issue": "x",
                    "reasoning": "y",
                    "severity": "catastrophic",
                }
            ],
        }
    )
    with pytest.raises(DebateContractError):
        parse_challenge_output(
            raw, expected_author_model_id="model-a", known_target_model_ids={"model-b"}
        )


def test_parse_challenge_output_rejects_blank_issue():
    raw = json.dumps(
        {
            "author_model_id": "model-a",
            "challenges": [
                {
                    "challenge_id": "c1",
                    "target_model_id": "model-b",
                    "issue": "   ",
                    "reasoning": "y",
                    "severity": "low",
                }
            ],
        }
    )
    with pytest.raises(DebateContractError):
        parse_challenge_output(
            raw, expected_author_model_id="model-a", known_target_model_ids={"model-b"}
        )


# --- Round 3: response output parsing ---------------------------------------


def test_parse_response_output_valid_defends():
    raw = json.dumps(
        {
            "author_model_id": "model-b",
            "responses": [
                {
                    "challenge_id": "c1",
                    "challenger_model_id": "model-a",
                    "stance": "defends",
                    "explanation": "The budget constraint was implicit in the recommendation.",
                }
            ],
        }
    )
    result = parse_response_output(
        raw, expected_author_model_id="model-b", known_challenge_keys={("model-a", "c1")}
    )
    assert result.responses[0].stance == "defends"
    assert result.responses[0].updated_position is None


@pytest.mark.parametrize("stance", ["revises", "concedes"])
def test_parse_response_output_revision_requires_updated_position(stance):
    raw = json.dumps(
        {
            "author_model_id": "model-b",
            "responses": [
                {
                    "challenge_id": "c1",
                    "challenger_model_id": "model-a",
                    "stance": stance,
                    "explanation": "Fair point.",
                }
            ],
        }
    )
    with pytest.raises(DebateContractError):
        parse_response_output(raw, expected_author_model_id="model-b", known_challenge_keys={("model-a", "c1")})


def test_parse_response_output_accepts_revision_with_updated_position():
    raw = json.dumps(
        {
            "author_model_id": "model-b",
            "responses": [
                {
                    "challenge_id": "c1",
                    "challenger_model_id": "model-a",
                    "stance": "revises",
                    "explanation": "Fair point.",
                    "updated_position": "Budget should be the primary constraint.",
                }
            ],
        }
    )
    result = parse_response_output(
        raw, expected_author_model_id="model-b", known_challenge_keys={("model-a", "c1")}
    )
    assert result.responses[0].updated_position == "Budget should be the primary constraint."


def test_parse_response_output_rejects_invalid_stance():
    raw = json.dumps(
        {
            "author_model_id": "model-b",
            "responses": [
                {
                    "challenge_id": "c1",
                    "challenger_model_id": "model-a",
                    "stance": "ignores",
                    "explanation": "x",
                }
            ],
        }
    )
    with pytest.raises(DebateContractError):
        parse_response_output(raw, expected_author_model_id="model-b", known_challenge_keys={("model-a", "c1")})


def test_parse_response_output_rejects_unknown_challenge_id():
    raw = json.dumps(
        {
            "author_model_id": "model-b",
            "responses": [
                {
                    "challenge_id": "c-does-not-exist",
                    "challenger_model_id": "model-a",
                    "stance": "defends",
                    "explanation": "x",
                }
            ],
        }
    )
    with pytest.raises(DebateContractError):
        parse_response_output(raw, expected_author_model_id="model-b", known_challenge_keys={("model-a", "c1")})


def test_parse_response_output_rejects_mismatched_author():
    raw = json.dumps(
        {
            "author_model_id": "model-x",
            "responses": [
                {
                    "challenge_id": "c1",
                    "challenger_model_id": "model-a",
                    "stance": "defends",
                    "explanation": "x",
                }
            ],
        }
    )
    with pytest.raises(DebateContractError):
        parse_response_output(raw, expected_author_model_id="model-b", known_challenge_keys={("model-a", "c1")})


def test_parse_response_output_accepts_colliding_challenge_ids_from_different_challengers():
    """challenge_id alone may collide across different reviewing models — the
    stable identity is the composite (challenger_model_id, challenge_id).
    Phase 3 reconciliation: this must NOT be rejected as a duplicate."""
    raw = json.dumps(
        {
            "author_model_id": "model-b",
            "responses": [
                {
                    "challenge_id": "c1",
                    "challenger_model_id": "model-a",
                    "stance": "defends",
                    "explanation": "x",
                },
                {
                    "challenge_id": "c1",  # same literal id, different challenger
                    "challenger_model_id": "model-c",
                    "stance": "concedes",
                    "explanation": "y",
                    "updated_position": "z",
                },
            ],
        }
    )
    result = parse_response_output(
        raw,
        expected_author_model_id="model-b",
        known_challenge_keys={("model-a", "c1"), ("model-c", "c1")},
    )
    assert len(result.responses) == 2


def test_parse_response_output_rejects_true_duplicate_same_challenger_and_id():
    raw = json.dumps(
        {
            "author_model_id": "model-b",
            "responses": [
                {
                    "challenge_id": "c1",
                    "challenger_model_id": "model-a",
                    "stance": "defends",
                    "explanation": "x",
                },
                {
                    "challenge_id": "c1",
                    "challenger_model_id": "model-a",  # same challenger AND id -> true duplicate
                    "stance": "concedes",
                    "explanation": "y",
                    "updated_position": "z",
                },
            ],
        }
    )
    with pytest.raises(DebateContractError):
        parse_response_output(
            raw, expected_author_model_id="model-b", known_challenge_keys={("model-a", "c1")}
        )


def test_parse_response_output_rejects_challenge_id_known_under_different_challenger():
    """A response cannot reference challenge_id 'c1' under a challenger it was
    not actually raised by — e.g. it must not cross-reference another
    debate session's (challenger, challenge_id) pair."""
    raw = json.dumps(
        {
            "author_model_id": "model-b",
            "responses": [
                {
                    "challenge_id": "c1",
                    "challenger_model_id": "model-x",  # not the real challenger for this session
                    "stance": "defends",
                    "explanation": "x",
                }
            ],
        }
    )
    with pytest.raises(DebateContractError):
        parse_response_output(
            raw, expected_author_model_id="model-b", known_challenge_keys={("model-a", "c1")}
        )


def test_parse_response_output_handles_truncated_json_via_lenient_recovery():
    # Cut off right after the responses array closes but before the final
    # brace, as a token-capped model response might arrive. The lenient
    # recovery utility balances {}  (not []), so the array itself must
    # already be closed in the truncated text for recovery to succeed.
    raw = (
        '{"author_model_id": "model-b", "responses": [{"challenge_id": "c1", '
        '"challenger_model_id": "model-a", "stance": "defends", '
        '"explanation": "The budget was fine."}]'
    )
    result = parse_response_output(
        raw, expected_author_model_id="model-b", known_challenge_keys={("model-a", "c1")}
    )
    assert result.responses[0].challenge_id == "c1"


# --- Findings contract (no LLM call; structure-only) ------------------------


def test_debate_findings_empty_is_valid():
    findings = DebateFindings()
    assert findings.agreements == ()
    assert findings.unresolved_disagreements == ()


def test_debate_findings_accepts_all_sections():
    findings = DebateFindings(
        agreements=(Agreement(description="Both agree on approach", model_ids=("model-a", "model-b")),),
        unresolved_disagreements=(
            UnresolvedDisagreement(
                description="Still disputed",
                challenge_id="c1",
                challenger_model_id="model-a",
                target_model_id="model-b",
                severity="high",
            ),
        ),
        corrections=(
            Correction(
                description="Model B corrected its budget assumption",
                model_id="model-b",
                challenge_id="c1",
                stance="concedes",
                updated_position="Budget is the binding constraint.",
            ),
        ),
        risks=(Risk(description="Unverified assumption", severity="medium", raised_by_model_ids=("model-a",)),),
        uncertainties=(Uncertainty(description="Insufficient evidence either way"),),
    )
    assert len(findings.corrections) == 1
    assert findings.corrections[0].stance == "concedes"


def test_agreement_requires_at_least_one_model():
    with pytest.raises(Exception):
        Agreement(description="x", model_ids=())


def test_risk_requires_at_least_one_raiser():
    with pytest.raises(Exception):
        Risk(description="x", severity="low", raised_by_model_ids=())


def test_correction_rejects_defends_stance():
    """Correction only models revise/concede outcomes, never a defended position."""
    with pytest.raises(Exception):
        Correction(
            description="x",
            model_id="model-b",
            challenge_id="c1",
            stance="defends",  # type: ignore[arg-type]
            updated_position="y",
        )


# --- Debate Conclusion synthesis output parsing ------------------------------


def _conclusion_json(**overrides) -> str:
    payload = {
        "recommended_approach": "Use approach A.",
        "why_this_approach": "It survived every challenge.",
        "what_changed": "Model B conceded its throughput claim.",
        "remaining_risks": "None significant.",
        "next_steps": "Verify with a load test.",
        "is_conditional": False,
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_parse_conclusion_output_valid():
    conclusion = parse_conclusion_output(_conclusion_json())
    assert isinstance(conclusion, DebateConclusion)
    assert conclusion.recommended_approach == "Use approach A."
    assert conclusion.is_conditional is False


def test_parse_conclusion_output_is_conditional_true():
    conclusion = parse_conclusion_output(
        _conclusion_json(
            recommended_approach="Depends on expected traffic volume.",
            is_conditional=True,
        )
    )
    assert conclusion.is_conditional is True


def test_parse_conclusion_output_tolerant_of_code_fence_wrapping():
    raw = f"```json\n{_conclusion_json()}\n```"
    conclusion = parse_conclusion_output(raw)
    assert conclusion.recommended_approach == "Use approach A."


def test_parse_conclusion_output_rejects_non_json():
    with pytest.raises(DebateContractError):
        parse_conclusion_output("not json at all")


def test_parse_conclusion_output_rejects_blank_field():
    with pytest.raises(DebateContractError):
        parse_conclusion_output(_conclusion_json(next_steps="   "))


def test_debate_conclusion_rejects_unknown_field():
    with pytest.raises(Exception):
        DebateConclusion(
            recommended_approach="a",
            why_this_approach="b",
            what_changed="c",
            remaining_risks="d",
            next_steps="e",
            unexpected_field="f",  # type: ignore[call-arg]
        )


def test_debate_conclusion_is_conditional_defaults_false():
    conclusion = DebateConclusion(
        recommended_approach="a",
        why_this_approach="b",
        what_changed="c",
        remaining_risks="d",
        next_steps="e",
    )
    assert conclusion.is_conditional is False


# --- Hidden issue / follow-up question (Debate's actual purpose) ------------


def _base_conclusion_kwargs(**overrides) -> dict:
    kwargs = {
        "recommended_approach": "a",
        "why_this_approach": "b",
        "what_changed": "c",
        "remaining_risks": "d",
        "next_steps": "e",
    }
    kwargs.update(overrides)
    return kwargs


def test_hidden_issue_and_follow_up_default_to_null():
    """The common, honest case: a debate that found nothing hidden and
    warrants no follow-up — both pairs default to None without being set."""
    conclusion = DebateConclusion(**_base_conclusion_kwargs())
    assert conclusion.hidden_issue is None
    assert conclusion.why_it_matters is None
    assert conclusion.suggested_follow_up_question is None
    assert conclusion.follow_up_reason is None


def test_hidden_issue_pair_can_both_be_set():
    conclusion = DebateConclusion(
        **_base_conclusion_kwargs(
            hidden_issue="Nobody checked the write-volume assumption.",
            why_it_matters="If volume is high, the recommended design silently loses jobs.",
        )
    )
    assert conclusion.hidden_issue is not None
    assert conclusion.why_it_matters is not None


def test_hidden_issue_half_filled_pair_is_rejected():
    with pytest.raises(Exception):
        DebateConclusion(**_base_conclusion_kwargs(hidden_issue="Something was found."))
    with pytest.raises(Exception):
        DebateConclusion(**_base_conclusion_kwargs(why_it_matters="It matters because X."))


def test_follow_up_question_pair_can_both_be_set():
    conclusion = DebateConclusion(
        **_base_conclusion_kwargs(
            suggested_follow_up_question="What write throughput does this system actually need to support?",
            follow_up_reason="The debate never established this, and it decides which architecture is safe.",
        )
    )
    assert conclusion.suggested_follow_up_question is not None
    assert conclusion.follow_up_reason is not None


def test_follow_up_question_half_filled_pair_is_rejected():
    with pytest.raises(Exception):
        DebateConclusion(**_base_conclusion_kwargs(suggested_follow_up_question="A question?"))
    with pytest.raises(Exception):
        DebateConclusion(**_base_conclusion_kwargs(follow_up_reason="Because it matters."))


def test_hidden_issue_blank_string_normalizes_to_none_not_a_half_filled_pair():
    """A model returning "" instead of null for an unused field must not be
    treated as a filled value that then trips the pairing validator."""
    conclusion = DebateConclusion(
        **_base_conclusion_kwargs(hidden_issue="   ", why_it_matters="")
    )
    assert conclusion.hidden_issue is None
    assert conclusion.why_it_matters is None


def test_parse_conclusion_output_with_no_hidden_issue_or_follow_up():
    raw = _conclusion_json(
        hidden_issue=None,
        why_it_matters=None,
        suggested_follow_up_question=None,
        follow_up_reason=None,
    )
    conclusion = parse_conclusion_output(raw)
    assert conclusion.hidden_issue is None
    assert conclusion.suggested_follow_up_question is None


def test_parse_conclusion_output_with_a_genuine_hidden_issue_and_follow_up():
    raw = _conclusion_json(
        hidden_issue="The hybrid design assumes Redis never loses an enqueued job.",
        why_it_matters="If Redis restarts mid-write, a job can silently vanish with no retry.",
        suggested_follow_up_question="What happens to an enqueued job if Redis restarts before it's persisted to PostgreSQL?",
        follow_up_reason="This directly determines whether the hybrid design is actually safe to ship.",
    )
    conclusion = parse_conclusion_output(raw)
    assert conclusion.hidden_issue is not None
    assert "Redis" in conclusion.suggested_follow_up_question


def test_parse_conclusion_output_backward_compatible_with_pre_follow_up_shape():
    """A conclusion generated under the previous prompt version (before
    these fields existed) has no hidden_issue/follow_up keys at all — must
    still parse cleanly, defaulting to None, not error."""
    raw = json.dumps(
        {
            "recommended_approach": "Use approach A.",
            "why_this_approach": "It survived every challenge.",
            "what_changed": "Model B conceded its throughput claim.",
            "remaining_risks": "None significant.",
            "next_steps": "Verify with a load test.",
            "is_conditional": False,
        }
    )
    conclusion = parse_conclusion_output(raw)
    assert conclusion.hidden_issue is None
    assert conclusion.suggested_follow_up_question is None
