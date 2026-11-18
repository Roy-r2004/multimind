"""Smoke tests for the isolated Phase 2 memory-evaluation tooling.

All tests here run in dry-run mode only (``FakeDeterministicProvider``) —
zero real network calls, zero spend. They prove the harness mechanics work
(fixture determinism, DB writes through the real pipeline, checkpoint
sampling, scoring) and, specifically, that the spending guard actually
refuses calls once a cap is reached — they do NOT and cannot prove anything
about real memory quality, since no real model is ever called here.
"""

from __future__ import annotations

import pytest

from app.llm.catalog import compute_cost
from scripts.memory_eval.fixtures import (
    build_early_fact_recall_fixture,
    build_fixture,
    build_open_task_lifecycle_fixture,
    build_requirement_override_fixture,
    load_fixture,
    save_fixture,
)
from scripts.memory_eval.harness import EVAL_MODEL_ID, FakeDeterministicProvider, run_fixture
from scripts.memory_eval.scoring import FactOutcome, score_fact_against_memory
from scripts.memory_eval.spending_guard import BudgetExceededError, GuardedProvider, SpendingGuard


# ---------------------------------------------------------------------------
# Fixture determinism
# ---------------------------------------------------------------------------


def test_early_fact_recall_fixture_is_deterministic():
    a = build_early_fact_recall_fixture(20, "single_ai")
    b = build_early_fact_recall_fixture(20, "single_ai")
    assert a.to_dict() == b.to_dict()


def test_requirement_override_fixture_plants_both_overrides_before_probe():
    f = build_requirement_override_fixture(50, "council")
    fact = f.marker_facts[0]
    assert fact.expected_value == "25MB"
    assert fact.superseded_value == "100MB"
    assert fact.introduced_at_turn < fact.superseded_at_turn < f.num_exchanges
    probe_turns = [t for t in f.turns if t.assistant_answer == "__PROBE__"]
    assert len(probe_turns) == 1
    assert probe_turns[0].index == f.num_exchanges - 1


def test_open_task_lifecycle_fixture_resolves_before_probe():
    f = build_open_task_lifecycle_fixture(30, "single_ai")
    fact = f.marker_facts[0]
    assert fact.resolved_at_turn is not None
    assert fact.introduced_at_turn < fact.resolved_at_turn < f.num_exchanges


def test_fixture_save_and_load_round_trips_exactly(tmp_path, monkeypatch):
    import scripts.memory_eval.fixtures as fixtures_module

    monkeypatch.setattr(fixtures_module, "FIXTURES_DIR", tmp_path)
    fixture = build_fixture("early_fact_recall", 20, "single_ai")
    path = save_fixture(fixture)
    assert path.exists()
    reloaded = load_fixture("early_fact_recall", 20, "single_ai")
    assert reloaded.to_dict() == fixture.to_dict()


@pytest.mark.parametrize("scenario,min_len", [
    ("early_fact_recall", 6),
    ("requirement_override", 12),
    ("open_task_lifecycle", 10),
])
def test_scenario_rejects_too_short_length(scenario, min_len):
    with pytest.raises(ValueError):
        build_fixture(scenario, min_len - 1, "single_ai")


# ---------------------------------------------------------------------------
# Deterministic scoring
# ---------------------------------------------------------------------------


def test_scoring_detects_stale_value():
    fixture = build_requirement_override_fixture(20, "single_ai")
    fact = fixture.marker_facts[0]
    score = score_fact_against_memory(fact, "The max upload size is 25MB.")
    assert score.outcome == FactOutcome.STALE


def test_scoring_detects_contradiction():
    fixture = build_requirement_override_fixture(20, "single_ai")
    fact = fixture.marker_facts[0]
    score = score_fact_against_memory(fact, "Upload size was 25MB, now it's 100MB.")
    assert score.outcome == FactOutcome.CONTRADICTED


def test_scoring_detects_correct_current_value():
    fixture = build_requirement_override_fixture(20, "single_ai")
    fact = fixture.marker_facts[0]
    score = score_fact_against_memory(fact, "The current max upload size is 100MB.")
    assert score.outcome == FactOutcome.RETAINED_VERBATIM


def test_scoring_flags_dropped_for_human_review():
    fixture = build_requirement_override_fixture(20, "single_ai")
    fact = fixture.marker_facts[0]
    score = score_fact_against_memory(fact, "Nothing about upload sizes here.")
    assert score.outcome == FactOutcome.DROPPED


# ---------------------------------------------------------------------------
# Spending guard — proof it actually stops calls
# ---------------------------------------------------------------------------


def test_spending_guard_refuses_once_cap_reached():
    guard = SpendingGuard(max_budget_usd=0.0001, model_id=EVAL_MODEL_ID, cost_fn=compute_cost)
    # First reservation at a tiny token count might fit; keep reserving
    # until it is refused, proving the refusal path is reachable.
    refused = False
    for _ in range(50):
        try:
            reserved = guard.reserve(estimated_input_tokens=500, max_output_tokens=500)
            guard.record_actual(reserved_amount=reserved, actual_cost_usd=reserved)
        except BudgetExceededError as exc:
            refused = True
            assert exc.max_budget_usd == 0.0001
            break
    assert refused, "guard never refused a call under an unreachably tiny budget"
    assert guard.refused_count >= 1
    assert guard.spent_usd <= guard.max_budget_usd


def test_spending_guard_never_overspends_under_concurrent_style_reservations():
    per_call_cost = compute_cost(EVAL_MODEL_ID, 2000, 2048)
    assert per_call_cost > 0  # sanity: real gpt-4.1 pricing must be non-zero here
    # Budget for roughly 5 calls at this size, so the loop both accepts some
    # and is eventually refused within a bounded number of iterations.
    guard = SpendingGuard(max_budget_usd=per_call_cost * 5, model_id=EVAL_MODEL_ID, cost_fn=compute_cost)
    accepted = 0
    for _ in range(10_000):
        try:
            reserved = guard.reserve(estimated_input_tokens=2000, max_output_tokens=2048)
        except BudgetExceededError:
            break
        guard.record_actual(reserved_amount=reserved, actual_cost_usd=reserved)
        accepted += 1
    assert accepted > 0
    assert accepted < 10_000  # proves it did stop, not just never got refused
    assert guard.spent_usd <= guard.max_budget_usd + 1e-9


@pytest.mark.asyncio
async def test_guarded_provider_blocks_the_inner_call_when_over_budget():
    inner = FakeDeterministicProvider()
    guard = SpendingGuard(max_budget_usd=0.0, model_id=EVAL_MODEL_ID, cost_fn=compute_cost)
    guarded = GuardedProvider(inner, guard=guard)
    with pytest.raises(BudgetExceededError):
        await guarded.complete(system="x" * 100, user="y", model="openai/gpt-4.1", max_tokens=100)
    # The critical property: the inner (real-call-equivalent) provider was
    # never actually invoked once the guard refused.
    assert inner.calls == 0


@pytest.mark.asyncio
async def test_guarded_provider_allows_calls_within_budget_and_tracks_spend():
    inner = FakeDeterministicProvider()
    guard = SpendingGuard(max_budget_usd=10.0, model_id=EVAL_MODEL_ID, cost_fn=compute_cost)
    guarded = GuardedProvider(inner, guard=guard)
    response = await guarded.complete(system="hello", user="world", model="openai/gpt-4.1", max_tokens=100)
    assert inner.calls == 1
    assert guard.call_count == 1
    assert guard.spent_usd >= 0
    assert "DRY-RUN SYNTHETIC SUMMARY" in response.text


# ---------------------------------------------------------------------------
# Full dry-run harness execution — mechanics only, not a quality measurement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_harness_executes_end_to_end_and_produces_checkpoints():
    fixture = build_fixture("early_fact_recall", 20, "single_ai")
    guard = SpendingGuard(max_budget_usd=1000.0, model_id=EVAL_MODEL_ID, cost_fn=compute_cost)
    result = await run_fixture(fixture, guard=guard, real=False)

    assert result["real_provider"] is False
    assert result["scenario"] == "early_fact_recall"
    assert len(result["checkpoints"]) >= 2  # plant turn + final probe turn
    last = result["checkpoints"][-1]
    assert last["probe_question"] is not None
    assert last["probe_answer_text"] is not None
    assert "memory_scores" in last
    assert guard.call_count > 0
    assert guard.spent_usd >= 0


@pytest.mark.asyncio
async def test_dry_run_harness_single_ai_and_council_runs_are_distinguishable():
    guard_a = SpendingGuard(max_budget_usd=1000.0, model_id=EVAL_MODEL_ID, cost_fn=compute_cost)
    guard_b = SpendingGuard(max_budget_usd=1000.0, model_id=EVAL_MODEL_ID, cost_fn=compute_cost)
    fixture_single = build_fixture("early_fact_recall", 20, "single_ai")
    fixture_council = build_fixture("early_fact_recall", 20, "council")

    result_single = await run_fixture(fixture_single, guard=guard_a, real=False)
    result_council = await run_fixture(fixture_council, guard=guard_b, real=False)

    assert result_single["mode"] == "single_ai"
    assert result_council["mode"] == "council"


@pytest.mark.asyncio
async def test_dry_run_harness_stops_safely_when_budget_is_exhausted():
    """With a zero budget, every provider call the guard sees is refused.

    Importantly, this does NOT crash the run: Phase 1's own bounded-recovery
    logic (``_summarize_with_bounded_recovery``) already treats any provider
    exception — including ``BudgetExceededError``, which looks like any
    other provider failure from inside that unmodified code — as a
    transient failure and leaves existing memory untouched rather than
    propagating. The probe call (made directly by this harness, outside
    that retry logic) explicitly catches the same exception and reports
    "not evaluated" (``None``) rather than raising. Together this is the
    "stops safely" property: no crash, no overspend, no corrupted/partial
    memory — just an honest "nothing could be evaluated under this budget."
    """
    fixture = build_fixture("requirement_override", 20, "single_ai")
    guard = SpendingGuard(max_budget_usd=0.0, model_id=EVAL_MODEL_ID, cost_fn=compute_cost)

    result = await run_fixture(fixture, guard=guard, real=False)

    assert guard.spent_usd == 0.0
    assert guard.reserved_usd == 0.0
    assert guard.refused_count >= 1
    for checkpoint in result["checkpoints"]:
        # No merge could ever succeed under a zero budget.
        assert checkpoint["merges_performed_so_far"] == 0
        assert checkpoint["rolling_memory_text"] == ""
    final = result["checkpoints"][-1]
    assert final["probe_question"] is not None  # a probe was attempted
    assert final["probe_answer_text"] is None  # ...but correctly reported as not evaluated
