"""Tests for Maps census quota/cost tracking.

``MapsQuotaTracker.add_cost`` and the additive merge are pure unit tests.
The context-local "active tracker" mechanism is what lets a single provider
hook (see app/llm/providers.py) attribute real OpenRouter cost to whichever
Maps job is currently running, without threading a tracker through every
classifier/planner/enrichment call site — those tests exercise that
propagation directly, including across asyncio.gather (concurrent judge()
calls in the keep/drop pass being the motivating case).
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.dependencies import AuthContext
from app.db.models import MapsCensusRun, MapsCensusStatus
from app.services.scraping.maps_quota_tracker import (
    MapsQuotaTracker,
    merge_quota_metrics,
    record_llm_cost,
    reset_active_tracker,
    set_active_tracker,
)


def test_add_cost_accumulates_and_ignores_invalid_values():
    tracker = MapsQuotaTracker()
    tracker.add_cost(0.002)
    tracker.add_cost(0.0031)
    tracker.add_cost(None)
    tracker.add_cost(0.0)
    tracker.add_cost(-1.0)
    tracker.add_cost("not-a-number")  # type: ignore[arg-type]
    assert tracker.metrics.total_cost_usd == pytest.approx(0.0051)


def test_record_llm_cost_is_a_noop_without_an_active_tracker():
    # No tracker set in this test's context — must not raise or affect anything.
    record_llm_cost(0.01)


def test_record_llm_cost_feeds_the_currently_active_tracker():
    tracker = MapsQuotaTracker()
    token = set_active_tracker(tracker)
    try:
        record_llm_cost(0.01)
        record_llm_cost(0.02)
    finally:
        reset_active_tracker(token)
    assert tracker.metrics.total_cost_usd == pytest.approx(0.03)

    # After reset, further cost must not leak back into the old tracker.
    record_llm_cost(0.05)
    assert tracker.metrics.total_cost_usd == pytest.approx(0.03)


@pytest.mark.asyncio
async def test_record_llm_cost_propagates_into_concurrent_gathered_tasks():
    """The keep/drop pass judges a batch via asyncio.gather — each judge()
    coroutine becomes its own Task. Tasks created while a tracker is active
    must still see it, matching how classify_place_keep_drop's LLM calls need
    to be attributed to the run that queued them."""
    tracker = MapsQuotaTracker()
    token = set_active_tracker(tracker)
    try:

        async def fake_llm_call(cost: float) -> None:
            await asyncio.sleep(0)
            record_llm_cost(cost)

        await asyncio.gather(*(fake_llm_call(c) for c in (0.01, 0.02, 0.03)))
    finally:
        reset_active_tracker(token)
    assert tracker.metrics.total_cost_usd == pytest.approx(0.06)


def test_provider_layer_hook_feeds_the_active_tracker(monkeypatch):
    """app/llm/providers.py's OpenRouterProvider records real usage.cost via
    this exact hook after every completion — verifies the integration seam
    without making a real HTTP call."""
    from app.llm.providers import _record_maps_quota_cost

    tracker = MapsQuotaTracker()
    token = set_active_tracker(tracker)
    try:
        _record_maps_quota_cost(0.0042)
    finally:
        reset_active_tracker(token)
    assert tracker.metrics.total_cost_usd == pytest.approx(0.0042)


def test_provider_layer_hook_never_raises_even_if_recording_fails(monkeypatch):
    """Cost bookkeeping must never break an actual chat/brain/lessons/maps
    LLM call — a failure here is swallowed, not propagated."""
    import app.services.scraping.maps_quota_tracker as quota_module
    from app.llm.providers import _record_maps_quota_cost

    def _boom(_cost_usd):
        raise RuntimeError("boom")

    monkeypatch.setattr(quota_module, "record_llm_cost", _boom)
    _record_maps_quota_cost(0.01)  # must not raise


@pytest.mark.asyncio
async def test_merge_quota_metrics_additively_accumulates_cost_across_calls(db, auth: AuthContext):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="BA",
        country_name="Bosnia and Herzegovina",
        status=MapsCensusStatus.RUNNING,
    )
    db.add(run)
    await db.commit()

    # merge_quota_metrics opens its own session via session_factory(); reuse
    # the test's db session through a trivial async-context wrapper.
    class _Factory:
        def __call__(self):
            return _NoOpAsyncContext(db)

    first_tracker = MapsQuotaTracker()
    first_tracker.add_classifier_call(5)
    first_tracker.add_cost(0.10)
    await merge_quota_metrics(_Factory(), run_id=run.id, tracker=first_tracker)

    second_tracker = MapsQuotaTracker()
    second_tracker.add_classifier_call(3)
    second_tracker.add_cost(0.025)
    await merge_quota_metrics(_Factory(), run_id=run.id, tracker=second_tracker)

    await db.refresh(run)
    assert run.quota_metrics["classifier_calls"] == 8
    assert run.quota_metrics["total_cost_usd"] == pytest.approx(0.125)


class _NoOpAsyncContext:
    """Wraps an already-open AsyncSession as an async context manager, since
    merge_quota_metrics expects a session_factory it can call + enter."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False
