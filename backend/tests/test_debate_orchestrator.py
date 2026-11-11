"""Phase 3 coverage: the AI Debate execution engine (app.llm.debate_orchestrator).

Pure engine tests — no database. Mocked LLM providers only, mirroring how
test_ultimate_council_fallbacks.py exercises app.llm.orchestrator with a
SimpleNamespace provider instead of a real OpenRouter call.
"""

import asyncio
import json
from dataclasses import dataclass, field

import pytest

from app.llm.debate_orchestrator import (
    DebateOrchestrator,
    DebateParticipant,
    DebateRunConfig,
    DebateRunResult,
    reduce_findings,
)
from app.llm.providers import LLMResponse

# Catalog entries that exist in app.llm.catalog.MODEL_CATALOG, so get_model()
# resolves real provider/provider_model values without needing a fake catalog.
MODEL_A = "gpt-4.1"
MODEL_B = "gpt-4.1-mini"
MODEL_C = "gpt-5-nano"

ROUND2_MARKER = "PEER REVIEW (ROUND 2)"
ROUND3_MARKER = "RESPONSE TO CHALLENGES (ROUND 3)"


@dataclass
class ScriptedProvider:
    """Maps a provider_model slug to a canned response or an exception, keyed
    separately per round since the same model is often called in both
    (reviewing others in Round 2, then responding to its own challenges in
    Round 3) with a different expected canned reply each time."""

    round2_by_slug: dict[str, object] = field(default_factory=dict)
    round3_by_slug: dict[str, object] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)  # (round, slug)

    async def complete(self, *, system, user, model, max_tokens):
        is_round3 = ROUND3_MARKER in system
        round_name = "round3" if is_round3 else "round2"
        self.calls.append((round_name, model))
        table = self.round3_by_slug if is_round3 else self.round2_by_slug
        outcome = table.get(model)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@dataclass
class ScriptedRegistry:
    provider: ScriptedProvider

    def get_provider(self, _name: str) -> ScriptedProvider:
        return self.provider


def _challenge_response(author: str, challenges: list[dict]) -> LLMResponse:
    return LLMResponse(
        text=json.dumps({"author_model_id": author, "challenges": challenges}),
        tokens_input=10,
        tokens_output=5,
        cost_usd=0.001,
    )


def _response_response(author: str, responses: list[dict]) -> LLMResponse:
    return LLMResponse(
        text=json.dumps({"author_model_id": author, "responses": responses}),
        tokens_input=8,
        tokens_output=4,
        cost_usd=0.0008,
    )


def _participants(*model_ids: str) -> list[DebateParticipant]:
    return [DebateParticipant(model_id=m, model_name=m, answer_text=f"Answer by {m}") for m in model_ids]


def _run(config: DebateRunConfig, provider: ScriptedProvider):
    orchestrator = DebateOrchestrator(providers=ScriptedRegistry(provider))
    events: list[tuple[str, dict]] = []

    async def on_event(event, data):
        events.append((event, data))

    return asyncio.run(orchestrator.run(config, on_event=on_event)), events


# --- 1. Successful three-model peer review ----------------------------------


def test_successful_three_model_peer_review():
    participants = _participants(MODEL_A, MODEL_B, MODEL_C)
    config = DebateRunConfig(user_message="Q?", participants=participants)
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(
                MODEL_A, [{"challenge_id": "c1", "target_model_id": MODEL_B, "issue": "x", "reasoning": "y", "severity": "low"}]
            ),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
            "openai/gpt-5-nano": _challenge_response(MODEL_C, []),
        },
        round3_by_slug={
            "openai/gpt-4.1-mini": _response_response(
                MODEL_B,
                [{"challenge_id": "c1", "challenger_model_id": MODEL_A, "stance": "defends", "explanation": "ok"}],
            ),
        },
    )
    result, events = _run(config, provider)
    assert len(result.challenge_results) == 3
    assert all(r.outcome.succeeded for r in result.challenge_results)
    assert [e for e, _ in events].count("debate_challenge_completed") == 3


# --- 19. No unbounded fan-out: exactly N calls for Round 2, never N^2 -------


def test_round_2_is_exactly_n_calls_never_pairwise():
    participants = _participants(MODEL_A, MODEL_B, MODEL_C)
    config = DebateRunConfig(user_message="Q?", participants=participants)
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(MODEL_A, []),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
            "openai/gpt-5-nano": _challenge_response(MODEL_C, []),
        }
    )
    _run(config, provider)
    assert len(provider.calls) == 3  # one call per participant, not 3*2 pairs


# --- 2. Successful rebuttal round (Round 3) ---------------------------------


def test_successful_rebuttal_round():
    participants = _participants(MODEL_A, MODEL_B)
    config = DebateRunConfig(user_message="Q?", participants=participants)
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(
                MODEL_A, [{"challenge_id": "c1", "target_model_id": MODEL_B, "issue": "x", "reasoning": "y", "severity": "medium"}]
            ),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
        },
        round3_by_slug={
            "openai/gpt-4.1-mini": _response_response(
                MODEL_B,
                [
                    {
                        "challenge_id": "c1",
                        "challenger_model_id": MODEL_A,
                        "stance": "concedes",
                        "explanation": "fair",
                        "updated_position": "corrected",
                    }
                ],
            ),
        },
    )
    result, events = _run(config, provider)
    assert len(result.response_results) == 1
    assert result.response_results[0].model_id == MODEL_B
    assert result.response_results[0].outcome.succeeded
    assert result.response_results[0].response_set.responses[0].stance == "concedes"
    assert "debate_response_completed" in [e for e, _ in events]


# --- 3. Two-model debate -----------------------------------------------------


def test_two_model_debate_runs_end_to_end():
    participants = _participants(MODEL_A, MODEL_B)
    config = DebateRunConfig(user_message="Q?", participants=participants)
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(MODEL_A, []),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
        }
    )
    result, _ = _run(config, provider)
    assert len(result.challenge_results) == 2
    assert result.response_results == []  # no challenges -> no Round 3 calls


def test_debate_requires_at_least_two_participants():
    with pytest.raises(ValueError):
        DebateRunConfig(user_message="Q?", participants=_participants(MODEL_A))


# --- 4. Zero-challenge debate -------------------------------------------------


def test_zero_challenge_debate_skips_round_3_entirely():
    participants = _participants(MODEL_A, MODEL_B, MODEL_C)
    config = DebateRunConfig(user_message="Q?", participants=participants)
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(MODEL_A, []),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
            "openai/gpt-5-nano": _challenge_response(MODEL_C, []),
        }
    )
    result, events = _run(config, provider)
    assert result.response_results == []
    assert [e for e, _ in events].count("debate_response_completed") == 0
    # Nobody challenged anyone, but that is explicitly NOT "agreement" — the
    # current contracts have no way for a model to state agreement, so it
    # must never be synthesized from an absence of challenge.
    assert result.findings.agreements == ()
    assert sorted(result.findings.unchallenged_model_ids) == [MODEL_A, MODEL_B, MODEL_C]


# --- 5. One model fails during Round 2 --------------------------------------


def test_one_model_failing_round_2_does_not_block_others():
    participants = _participants(MODEL_A, MODEL_B, MODEL_C)
    config = DebateRunConfig(user_message="Q?", participants=participants)
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": RuntimeError("provider 503"),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
            "openai/gpt-5-nano": _challenge_response(MODEL_C, []),
        }
    )
    result, events = _run(config, provider)
    by_model = {r.model_id: r for r in result.challenge_results}
    assert not by_model[MODEL_A].outcome.succeeded
    assert "provider 503" in by_model[MODEL_A].outcome.error
    assert by_model[MODEL_B].outcome.succeeded
    assert by_model[MODEL_C].outcome.succeeded
    assert "debate_challenge_failed" in [e for e, _ in events]


# --- 6. One model fails during Round 3 --------------------------------------


def test_one_model_failing_round_3_does_not_block_others():
    participants = _participants(MODEL_A, MODEL_B, MODEL_C)
    config = DebateRunConfig(user_message="Q?", participants=participants)
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(
                MODEL_A,
                [
                    {"challenge_id": "c1", "target_model_id": MODEL_B, "issue": "x", "reasoning": "y", "severity": "low"},
                    {"challenge_id": "c2", "target_model_id": MODEL_C, "issue": "x", "reasoning": "y", "severity": "low"},
                ],
            ),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
            "openai/gpt-5-nano": _challenge_response(MODEL_C, []),
        },
        round3_by_slug={
            "openai/gpt-4.1-mini": RuntimeError("timeout"),
            "openai/gpt-5-nano": _response_response(
                MODEL_C,
                [{"challenge_id": "c2", "challenger_model_id": MODEL_A, "stance": "defends", "explanation": "ok"}],
            ),
        },
    )
    result, _ = _run(config, provider)
    by_model = {r.model_id: r for r in result.response_results}
    assert not by_model[MODEL_B].outcome.succeeded
    assert by_model[MODEL_C].outcome.succeeded


# --- 7. Malformed LLM JSON ----------------------------------------------------


def test_malformed_json_becomes_a_per_model_failure_not_a_crash():
    participants = _participants(MODEL_A, MODEL_B)
    config = DebateRunConfig(user_message="Q?", participants=participants)
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": LLMResponse(text="not json at all", tokens_input=5, tokens_output=5, cost_usd=0.0001),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
        }
    )
    result, _ = _run(config, provider)
    by_model = {r.model_id: r for r in result.challenge_results}
    assert by_model[MODEL_A].challenge_set is None
    assert by_model[MODEL_A].outcome.error is not None
    # Usage is still recorded even though validation failed (a real call was made).
    assert by_model[MODEL_A].outcome.tokens_input == 5
    assert by_model[MODEL_B].outcome.succeeded


# --- 8. Invalid challenge/model references -----------------------------------


def test_challenge_targeting_unknown_model_is_rejected_as_invalid_output():
    participants = _participants(MODEL_A, MODEL_B)
    config = DebateRunConfig(user_message="Q?", participants=participants)
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(
                MODEL_A, [{"challenge_id": "c1", "target_model_id": "not-a-participant", "issue": "x", "reasoning": "y", "severity": "low"}]
            ),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
        }
    )
    result, _ = _run(config, provider)
    by_model = {r.model_id: r for r in result.challenge_results}
    assert by_model[MODEL_A].challenge_set is None
    assert "unknown target_model_id" in by_model[MODEL_A].outcome.error


# --- 9. Duplicate challenge IDs across different authors --------------------


def test_duplicate_challenge_ids_across_authors_are_disambiguated_by_composite_key():
    participants = _participants(MODEL_A, MODEL_B, MODEL_C)
    config = DebateRunConfig(user_message="Q?", participants=participants)
    # Both A and C independently raise a challenge literally called "c1"
    # against B. Composite identity (challenger, challenge_id) must keep them
    # distinct through Round 3 grouping and findings reduction.
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(
                MODEL_A, [{"challenge_id": "c1", "target_model_id": MODEL_B, "issue": "issue from A", "reasoning": "y", "severity": "low"}]
            ),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
            "openai/gpt-5-nano": _challenge_response(
                MODEL_C, [{"challenge_id": "c1", "target_model_id": MODEL_B, "issue": "issue from C", "reasoning": "y", "severity": "low"}]
            ),
        },
        round3_by_slug={
            "openai/gpt-4.1-mini": _response_response(
                MODEL_B,
                [
                    {"challenge_id": "c1", "challenger_model_id": MODEL_A, "stance": "defends", "explanation": "re A"},
                    {"challenge_id": "c1", "challenger_model_id": MODEL_C, "stance": "concedes", "explanation": "re C", "updated_position": "fixed"},
                ],
            ),
        },
    )
    result, _ = _run(config, provider)
    response_set = result.response_results[0].response_set
    assert len(response_set.responses) == 2
    findings = result.findings
    assert len(findings.unresolved_disagreements) == 1  # the "defends" one, from A
    assert len(findings.corrections) == 1  # the "concedes" one, from C
    assert findings.unresolved_disagreements[0].challenger_model_id == MODEL_A
    assert findings.corrections[0].challenge_id == "c1"


# --- 16/17 (engine-level): never reads/writes Turn/ModelAnswer/Verdict -----


def test_orchestrator_has_no_runtime_binding_to_protected_models_or_db_session():
    """Checks actual module attributes/globals, not a brittle text search —
    the module's own docstrings legitimately mention Turn/ModelAnswer/Verdict
    in prose explaining what it must NOT touch."""
    import app.llm.debate_orchestrator as module

    for forbidden_name in ("Turn", "ModelAnswer", "Verdict", "AsyncSession", "ChatService"):
        assert not hasattr(module, forbidden_name), f"debate_orchestrator must not import {forbidden_name}"


# --- 20. Deterministic findings ----------------------------------------------


def test_findings_distinguish_claim_criticism_concession_and_uncertainty():
    participants = _participants(MODEL_A, MODEL_B, MODEL_C)
    config = DebateRunConfig(user_message="Q?", participants=participants)
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(
                MODEL_A,
                [
                    {
                        "challenge_id": "c1",
                        "target_model_id": MODEL_B,
                        "issue": "risky assumption",
                        "reasoning": "y",
                        "severity": "high",
                        "uncertainty": "not fully sure",
                    }
                ],
            ),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
            "openai/gpt-5-nano": _challenge_response(MODEL_C, []),
        },
        round3_by_slug={
            "openai/gpt-4.1-mini": _response_response(
                MODEL_B,
                [
                    {
                        "challenge_id": "c1",
                        "challenger_model_id": MODEL_A,
                        "stance": "revises",
                        "explanation": "good point",
                        "updated_position": "revised position",
                    }
                ],
            ),
        },
    )
    result, _ = _run(config, provider)
    findings = result.findings
    assert len(findings.corrections) == 1
    assert findings.corrections[0].stance == "revises"
    assert findings.corrections[0].updated_position == "revised position"
    assert len(findings.risks) == 1  # severity == high, regardless of resolution
    assert len(findings.uncertainties) == 1
    assert findings.uncertainties[0].description == "not fully sure"
    # "Agreement" is never synthesized from an absence of challenge — the
    # current contracts can't express it, so it's always empty. "Unchallenged"
    # is the honest, separate category: A raised a challenge but was never
    # challenged itself, and C was never challenged either, so both appear
    # here; only B was actually challenged, so B is the one model excluded.
    assert findings.agreements == ()
    assert sorted(findings.unchallenged_model_ids) == [MODEL_A, MODEL_C]


def test_reduce_findings_on_empty_result_is_empty():
    findings = reduce_findings(DebateRunResult())
    assert findings.agreements == ()
    assert findings.unchallenged_model_ids == ()
    assert findings.unresolved_disagreements == ()
    assert findings.corrections == ()
    assert findings.risks == ()
    assert findings.uncertainties == ()
