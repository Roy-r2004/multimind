"""Guards that keep a census from looping forever without reaching a terminal state."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import ScrapingExecutionStatus
from app.services.scraping.execution_orchestrator import (
    MAX_EXECUTION_ATTEMPTS,
    SourceDiscoveryExecutionOrchestrator,
    _heartbeat_loop,
    recover_scraping_executions,
)


def _async_session_factory(db):
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=db)


def _result_with(items):
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


@pytest.mark.asyncio
async def test_recover_completes_execution_once_restart_budget_is_exhausted():
    execution = SimpleNamespace(
        id="exec-loop",
        status=ScrapingExecutionStatus.RUNNING,
        error_message="stalled",
        heartbeat_at=None,
        completed_at=None,
    )
    started_events = [f"evt-{i}" for i in range(MAX_EXECUTION_ATTEMPTS)]

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _result_with([execution]),  # stale execution scan
            _result_with(started_events),  # execution_started count
            _result_with([]),  # cancel leftover tasks
            _result_with([]),  # complete leftover agents
        ]
    )
    db.commit = AsyncMock()

    with (
        patch(
            "app.services.scraping.execution_orchestrator.AsyncSessionLocal",
            _async_session_factory(db),
        ),
        patch(
            "app.services.scraping.execution_orchestrator._reset_running_work_to_queued",
            new_callable=AsyncMock,
        ) as reset_mock,
        patch("app.services.scraping.execution_orchestrator.execution_service") as exec_service,
        patch(
            "app.services.scraping.execution_orchestrator.get_settings",
            return_value=SimpleNamespace(scraping_execution_stale_seconds=900),
        ),
    ):
        exec_service.emit_event = AsyncMock()
        exec_service.enqueue_execution = AsyncMock()
        await recover_scraping_executions({})

        emitted = exec_service.emit_event.await_args.args
        assert emitted[2] == "execution_completed_after_restart_budget"
        # The whole point: stop retrying, do not put it back on the queue.
        exec_service.enqueue_execution.assert_not_awaited()

    assert execution.status == ScrapingExecutionStatus.COMPLETED
    assert execution.completed_at is not None
    reset_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_recover_still_requeues_while_budget_remains():
    execution = SimpleNamespace(
        id="exec-ok",
        status=ScrapingExecutionStatus.RUNNING,
        error_message=None,
        heartbeat_at=None,
        completed_at=None,
    )

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _result_with([execution]),
            _result_with(["evt-1"]),  # only one prior attempt
        ]
    )
    db.commit = AsyncMock()

    with (
        patch(
            "app.services.scraping.execution_orchestrator.AsyncSessionLocal",
            _async_session_factory(db),
        ),
        patch(
            "app.services.scraping.execution_orchestrator._reset_running_work_to_queued",
            new_callable=AsyncMock,
        ),
        patch("app.services.scraping.execution_orchestrator.execution_service") as exec_service,
        patch(
            "app.services.scraping.execution_orchestrator.get_settings",
            return_value=SimpleNamespace(scraping_execution_stale_seconds=900),
        ),
    ):
        exec_service.emit_event = AsyncMock()
        exec_service.enqueue_execution = AsyncMock()
        await recover_scraping_executions({})

        exec_service.enqueue_execution.assert_awaited_once_with("exec-ok")

    assert execution.status == ScrapingExecutionStatus.QUEUED


async def _run_heartbeat_until(beats_wanted, beat_outcomes=None):
    """Drive _heartbeat_loop until it has beaten `beats_wanted` times, then cancel."""
    import asyncio

    reached = asyncio.Event()
    calls = []
    outcomes = list(beat_outcomes or [])

    async def fake_touch(_db, _execution_id):
        calls.append(1)
        if len(calls) >= beats_wanted:
            reached.set()
        if outcomes:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome

    db = AsyncMock()
    with (
        patch(
            "app.services.scraping.execution_orchestrator.AsyncSessionLocal",
            _async_session_factory(db),
        ),
        patch("app.services.scraping.execution_orchestrator.execution_service") as exec_service,
    ):
        exec_service.touch_heartbeat = AsyncMock(side_effect=fake_touch)
        task = asyncio.create_task(_heartbeat_loop("exec-1", interval=0.001))
        try:
            await asyncio.wait_for(reached.wait(), timeout=5)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    return len(calls)


@pytest.mark.asyncio
async def test_heartbeat_loop_beats_repeatedly_until_cancelled():
    assert await _run_heartbeat_until(3) >= 3


@pytest.mark.asyncio
async def test_heartbeat_loop_survives_a_failing_beat():
    # A transient DB failure must not kill the heartbeat and strand the run.
    beats = await _run_heartbeat_until(3, beat_outcomes=[RuntimeError("db blip")])
    assert beats >= 3


@pytest.mark.asyncio
async def test_phase_already_completed_detects_prior_completion_event():
    db = AsyncMock()
    found = MagicMock()
    found.scalar_one_or_none.return_value = "evt-1"
    missing = MagicMock()
    missing.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(side_effect=[found, missing])

    orchestrator = SourceDiscoveryExecutionOrchestrator(db)

    assert await orchestrator._phase_already_completed("exec-1", "facility_extraction_phase_completed")
    assert not await orchestrator._phase_already_completed(
        "exec-1", "facility_publication_phase_completed"
    )
