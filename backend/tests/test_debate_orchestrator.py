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
CONCLUSION_MARKER = "CONCLUSION SYNTHESIS"


@dataclass
class ScriptedProvider:
    """Maps a provider_model slug to a canned response or an exception, keyed
    separately per round since the same model is often called in both
    (reviewing others in Round 2, then responding to its own challenges in
    Round 3) with a different expected canned reply each time."""

    round2_by_slug: dict[str, object] = field(default_factory=dict)
    round3_by_slug: dict[str, object] = field(default_factory=dict)
    conclusion_by_slug: dict[str, object] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)  # (round, slug)

    async def complete(self, *, system, user, model, max_tokens):
        if CONCLUSION_MARKER in system:
            round_name = "conclusion"
            table = self.conclusion_by_slug
        elif ROUND3_MARKER in system:
            round_name = "round3"
            table = self.round3_by_slug
        else:
            round_name = "round2"
            table = self.round2_by_slug
        self.calls.append((round_name, model))
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


def _conclusion_response(
    *,
    recommended_approach: str = "Approach X",
    why_this_approach: str = "It held up best under challenge.",
    what_changed: str = "Model B conceded a point.",
    remaining_risks: str = "None significant.",
    next_steps: str = "Verify the data source independently.",
    is_conditional: bool = False,
) -> LLMResponse:
    return LLMResponse(
        text=json.dumps(
            {
                "recommended_approach": recommended_approach,
                "why_this_approach": why_this_approach,
                "what_changed": what_changed,
                "remaining_risks": remaining_risks,
                "next_steps": next_steps,
                "is_conditional": is_conditional,
            }
        ),
        tokens_input=20,
        tokens_output=12,
        cost_usd=0.003,
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


# --- Regression: a bare asyncio.TimeoutError() must never look like success -


def test_bare_timeout_error_produces_a_non_empty_error_message():
    """asyncio.wait_for raises a bare TimeoutError() whose str() is "" — a
    real production debate hit this exact case (Claude Opus 4.1 timing out
    in Round 2) and the empty string was later treated as falsy ("no error")
    by debate_service.py's `if error:` persistence check, silently
    recording a failed call as COMPLETED. The orchestrator must never hand
    persistence an empty-but-non-None error string for this to be safe."""
    participants = _participants(MODEL_A, MODEL_B, MODEL_C)
    config = DebateRunConfig(user_message="Q?", participants=participants)
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": TimeoutError(),  # bare — no message, matches asyncio.wait_for's
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
            "openai/gpt-5-nano": _challenge_response(MODEL_C, []),
        }
    )
    result, _events = _run(config, provider)
    by_model = {r.model_id: r for r in result.challenge_results}
    assert not by_model[MODEL_A].outcome.succeeded
    assert by_model[MODEL_A].outcome.error  # truthy: never "" for a real failure
    assert by_model[MODEL_A].outcome.error is not None
    assert result.all_calls_failed is False
    assert any(not r.outcome.succeeded for r in result.challenge_results)


# --- Debate timeout reliability: a genuinely slow model, cut off cleanly ----


@dataclass
class SlowThenScriptedProvider(ScriptedProvider):
    """Like ScriptedProvider, but one model_id's call actually sleeps past
    the configured per-call timeout instead of raising immediately — this
    exercises the real asyncio.wait_for timeout path end-to-end (not a
    manually-raised bare TimeoutError), the way the real Claude Opus 4.1
    Round-2 call in the first live debate actually behaved."""

    slow_model: str = ""
    slow_seconds: float = 0.0

    async def complete(self, *, system, user, model, max_tokens):
        if model == self.slow_model:
            await asyncio.sleep(self.slow_seconds)
        return await super().complete(system=system, user=user, model=model, max_tokens=max_tokens)


def test_slow_model_is_cut_off_by_the_configured_per_call_timeout_with_a_readable_message():
    participants = _participants(MODEL_A, MODEL_B, MODEL_C)
    # Explicit, non-default per_call_timeout_seconds — proves the value is
    # genuinely configurable per run, not hardcoded.
    config = DebateRunConfig(user_message="Q?", participants=participants, per_call_timeout_seconds=0.05)
    provider = SlowThenScriptedProvider(
        slow_model="openai/gpt-4.1",
        slow_seconds=1.0,
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(MODEL_A, []),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
            "openai/gpt-5-nano": _challenge_response(MODEL_C, []),
        },
    )
    result, _events = _run(config, provider)
    by_model = {r.model_id: r for r in result.challenge_results}
    slow = by_model[MODEL_A]
    assert not slow.outcome.succeeded
    assert slow.outcome.error == "model did not respond within 0s"
    assert by_model[MODEL_B].outcome.succeeded
    assert by_model[MODEL_C].outcome.succeeded


def test_default_per_call_and_overall_timeout_values_are_coherent():
    """Documents the actual configured relationship rather than asserting a
    specific number in isolation: two per-call timeouts back to back must
    still fit inside the overall deadline with real margin (the first live
    debate's 45s/180s pair left only ~30s; this checks the replacement
    pair does better, not just that it changed)."""
    from app.llm.debate_orchestrator import (
        DEBATE_DEFAULT_OVERALL_DEADLINE_SECONDS,
        DEBATE_DEFAULT_PER_CALL_TIMEOUT_SECONDS,
    )

    worst_case_two_rounds = DEBATE_DEFAULT_PER_CALL_TIMEOUT_SECONDS * 2
    margin = DEBATE_DEFAULT_OVERALL_DEADLINE_SECONDS - worst_case_two_rounds
    assert margin >= 60.0


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


# --- Debate Conclusion synthesis (one optional extra call) -------------------


def test_synthesis_is_skipped_entirely_when_no_synthesis_model_configured():
    """Default behavior (and every pre-existing test above): omitting
    synthesis_model_id must not add any extra provider call."""
    participants = _participants(MODEL_A, MODEL_B)
    config = DebateRunConfig(user_message="Q?", participants=participants)
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(MODEL_A, []),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
        }
    )
    result, events = _run(config, provider)
    assert result.conclusion is None
    assert result.conclusion_error is None
    assert result.conclusion_skipped_reason is None
    assert [c for c in provider.calls if c[0] == "conclusion"] == []
    assert "debate_conclusion_started" not in [e for e, _ in events]


def test_synthesis_runs_exactly_once_after_a_successful_debate():
    participants = _participants(MODEL_A, MODEL_B, MODEL_C)
    config = DebateRunConfig(
        user_message="Q?", participants=participants, synthesis_model_id=MODEL_C
    )
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(
                MODEL_A,
                [{"challenge_id": "c1", "target_model_id": MODEL_B, "issue": "x", "reasoning": "y", "severity": "low"}],
            ),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
            "openai/gpt-5-nano": _challenge_response(MODEL_C, []),
        },
        round3_by_slug={
            "openai/gpt-4.1-mini": _response_response(
                MODEL_B,
                [{"challenge_id": "c1", "challenger_model_id": MODEL_A, "stance": "concedes", "explanation": "ok", "updated_position": "fixed"}],
            ),
        },
        conclusion_by_slug={"openai/gpt-5-nano": _conclusion_response()},
    )
    result, events = _run(config, provider)
    assert result.conclusion is not None
    assert result.conclusion.recommended_approach == "Approach X"
    assert result.conclusion_error is None
    assert result.conclusion_outcome is not None
    assert result.conclusion_outcome.tokens_input == 20
    conclusion_calls = [c for c in provider.calls if c[0] == "conclusion"]
    assert conclusion_calls == [("conclusion", "openai/gpt-5-nano")]  # exactly one
    event_names = [e for e, _ in events]
    assert "debate_conclusion_started" in event_names
    assert "debate_conclusion_completed" in event_names
    assert "debate_conclusion_failed" not in event_names


def test_synthesis_is_skipped_not_failed_when_every_round_call_failed():
    """Distinguishes 'nothing to synthesize from' (skipped) from an actual
    synthesis call failing (failed) — see DebateConclusionStatus.SKIPPED."""
    participants = _participants(MODEL_A, MODEL_B)
    config = DebateRunConfig(
        user_message="Q?", participants=participants, synthesis_model_id=MODEL_A
    )
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": RuntimeError("provider down"),
            "openai/gpt-4.1-mini": RuntimeError("provider down"),
        }
    )
    result, _events = _run(config, provider)
    assert result.all_calls_failed is True
    assert result.conclusion is None
    assert result.conclusion_error is None
    assert result.conclusion_skipped_reason == "no successful debate output to synthesize from"
    assert [c for c in provider.calls if c[0] == "conclusion"] == []


def test_synthesis_failure_does_not_touch_challenge_or_response_results():
    participants = _participants(MODEL_A, MODEL_B)
    config = DebateRunConfig(
        user_message="Q?", participants=participants, synthesis_model_id=MODEL_A
    )
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(MODEL_A, []),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
        },
        conclusion_by_slug={"openai/gpt-4.1": LLMResponse(text="not json", tokens_input=5, tokens_output=5, cost_usd=0.0001)},
    )
    result, events = _run(config, provider)
    assert result.conclusion is None
    assert result.conclusion_error is not None
    assert result.conclusion_skipped_reason is None
    # Round 2/3 outcomes are completely untouched by the synthesis failure.
    assert all(r.outcome.succeeded for r in result.challenge_results)
    assert "debate_conclusion_failed" in [e for e, _ in events]


def test_synthesis_still_runs_when_round3_was_skipped_by_the_deadline():
    """Round 3 can be skipped entirely (deadline already expired after
    Round 2) while Round 2's own data is still usable for synthesis."""
    participants = _participants(MODEL_A, MODEL_B)
    config = DebateRunConfig(
        user_message="Q?",
        participants=participants,
        synthesis_model_id=MODEL_A,
        overall_deadline_seconds=0.0,
    )
    provider = ScriptedProvider(
        round2_by_slug={
            "openai/gpt-4.1": _challenge_response(MODEL_A, []),
            "openai/gpt-4.1-mini": _challenge_response(MODEL_B, []),
        },
        conclusion_by_slug={"openai/gpt-4.1": _conclusion_response()},
    )
    result, events = _run(config, provider)
    assert result.response_results == []
    assert "debate_round3_skipped" in [e for e, _ in events]
    assert result.conclusion is not None


def test_conclusion_prompt_faithfully_represents_concessions_and_conflicting_views():
    """Contract-level check that the prompt the model actually receives
    carries the real exchange content (not a lossy paraphrase) and
    instructs the model to avoid forcing a consensus — judgment quality
    itself needs a real model and is out of scope for a mocked test."""
    from app.llm.debate_prompts import ConclusionExchange, PanelistAnswer, build_conclusion_prompt

    prompt = build_conclusion_prompt(
        user_message="Which approach is best?",
        participants=[
            PanelistAnswer(model_id=MODEL_A, model_name="Model A", text="Answer A"),
            PanelistAnswer(model_id=MODEL_B, model_name="Model B", text="Answer B"),
        ],
        exchanges=[
            ConclusionExchange(
                challenge_id="c1",
                challenger_model_id=MODEL_A,
                challenger_model_name="Model A",
                target_model_id=MODEL_B,
                target_model_name="Model B",
                issue="Model B overstated guarantee X",
                reasoning="No evidence for X under failure Y",
                severity="high",
                response_stance="defends",
                response_explanation="X still holds because Z",
            )
        ],
        verdict_reference_text="Original verdict text",
    )
    assert "Model B overstated guarantee X" in prompt
    assert "No evidence for X under failure Y" in prompt
    assert "X still holds because Z" in prompt
    assert "is_conditional" in prompt
    assert "do not" in prompt.lower() or "not treat" in prompt.lower()
    assert "Original verdict text" in prompt
    assert "unquestionable truth" in prompt.lower()


# --- Conclusion prompt conciseness polish ------------------------------------


def test_conclusion_prompt_carries_the_word_budget_and_anti_repetition_instructions():
    """A real debate's first conclusion was accurate but needlessly long and
    repetitive. This locks in the fix at the prompt-template level — the
    model actually receives a concrete word target and an explicit
    instruction not to restate the same point across fields."""
    from app.llm.debate_prompts import PanelistAnswer, build_conclusion_prompt

    prompt = build_conclusion_prompt(
        user_message="Q?",
        participants=[PanelistAnswer(model_id=MODEL_A, model_name="Model A", text="Answer A")],
        exchanges=[],
    )
    lower = prompt.lower()
    assert "200" in prompt and "300" in prompt  # the concrete word target
    assert "words" in lower
    assert "restate" in lower or "repeat" in lower
    assert "different ground" in lower or "not list every challenge" in lower or "minor" in lower
    assert "sacrifice" in lower  # explicit: never drop something important just to stay short


def test_conclusion_max_tokens_was_tightened_from_the_original_generous_ceiling():
    from app.llm.debate_orchestrator import DEBATE_CONCLUSION_MAX_TOKENS

    # Loose enough for 2-3x the ~300-word target on a genuinely complex
    # question, tight enough to not leave room for the old rambling length.
    assert 800 <= DEBATE_CONCLUSION_MAX_TOKENS <= 1500


def test_parse_conclusion_output_accepts_a_concise_representative_response():
    """A short, non-repetitive, representative conclusion must still parse
    cleanly against the unchanged JSON contract — conciseness is a prompting
    change, not a contract change."""
    from app.llm.debate_contracts import parse_conclusion_output

    raw = json.dumps(
        {
            "recommended_approach": "Use the hybrid architecture with an outbox relay.",
            "why_this_approach": "It held up after Model A's challenge forced a safer design.",
            "what_changed": "Model B conceded its dual-write example was unsafe and revised it.",
            "remaining_risks": "None significant remain unresolved.",
            "next_steps": "Load-test the outbox relay before shipping.",
            "is_conditional": False,
        }
    )
    conclusion = parse_conclusion_output(raw)
    assert conclusion.is_conditional is False
    assert len(conclusion.recommended_approach.split()) < 15


def test_parse_conclusion_output_preserves_a_conditional_recommendation_concisely():
    """Genuinely conflicting evidence must still produce is_conditional=true
    with the real options stated, even in a short response."""
    from app.llm.debate_contracts import parse_conclusion_output

    raw = json.dumps(
        {
            "recommended_approach": "Depends on expected write volume: PostgreSQL-only below ~500 writes/sec, hybrid above it.",
            "why_this_approach": "Both sides defended their position; neither challenge was conceded.",
            "what_changed": "Nothing meaningful changed.",
            "remaining_risks": "The actual write volume this system will see was never established.",
            "next_steps": "Measure real traffic, then pick the matching approach above.",
            "is_conditional": True,
        }
    )
    conclusion = parse_conclusion_output(raw)
    assert conclusion.is_conditional is True
    assert "depends" in conclusion.recommended_approach.lower()
