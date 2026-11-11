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
    DebateContractError,
    DebateFindings,
    Risk,
    Uncertainty,
    UnresolvedDisagreement,
    parse_challenge_output,
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
