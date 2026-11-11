"""Debate Mode now runs on its OWN dedicated ARQ worker/queue (app.debate.worker),
fully independent of the scraping worker (app.scraping.worker). This file proves
that isolation structurally, rather than by trusting the module docstrings:

- Debate jobs are enqueued onto the dedicated queue, never the scraping worker's.
- The scraping worker's own registrations (functions, cron jobs, concurrency,
  timeout) are byte-identical to before Debate Mode ever existed.
- The debate worker consumes only Debate's own two job functions.
- Duplicate job delivery for the same session still can't execute twice, now
  exercised through the dedicated worker's own job entrypoint.

Cancellation and startup-recovery correctness themselves are already covered
exhaustively in test_debate_api.py (test_cancel_running_session_is_persisted_and_idempotent,
test_recover_debate_sessions_fails_only_sessions_stale_past_threshold, etc.) —
this file does not duplicate that; it only proves those same functions are now
wired onto the right (and only the right) worker.
"""

import subprocess
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.dependencies import AuthContext
from app.core.config import get_settings
from app.db.models import DebateSessionStatus
from app.debate.worker import WorkerSettings as DebateWorkerSettings
from app.scraping.worker import WorkerSettings as ScrapingWorkerSettings
from app.services import debate_service
from tests.test_debate_service import MODEL_A, MODEL_B, _make_turn

REPO_ROOT = Path(__file__).resolve().parents[2]
# The merge commit immediately preceding Phase 1's first Debate commit — the
# scraping worker existed, fully configured for scraping only, long before
# Debate Mode. Any drift from this baseline (other than nothing: Debate's
# Phase-4 registrations were added and then fully reverted) is a regression.
BASELINE_REF = "edc4333"


def _git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], cwd=REPO_ROOT, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return (REPO_ROOT / ".git").exists()


# --- Scraping worker is untouched by Debate Mode ever having existed --------


@pytest.mark.skipif(not _git_available(), reason="git is unavailable in this environment")
def test_scraping_worker_source_is_byte_identical_to_the_pre_debate_baseline():
    result = subprocess.run(
        ["git", "diff", "--stat", BASELINE_REF, "--", "backend/app/scraping/worker.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "", (
        f"app/scraping/worker.py has drifted from the pre-Debate-Mode baseline "
        f"({BASELINE_REF}):\n{result.stdout}"
    )


def test_scraping_worker_has_no_debate_functions_or_imports():
    import app.scraping.worker as scraping_worker_module

    names = {
        getattr(f, "__name__", None) or getattr(getattr(f, "coroutine", None), "__name__", "")
        for f in ScrapingWorkerSettings.functions
    }
    assert "run_debate_session_job" not in names
    assert "recover_debate_sessions" not in names
    assert not hasattr(scraping_worker_module, "run_debate_session_job")
    assert not hasattr(scraping_worker_module, "recover_debate_sessions")
    # No explicit queue_name override — stays on ARQ's default queue, same as
    # every other pre-existing scraping job, and distinct from the Debate
    # worker's "debate" queue checked below.
    assert not hasattr(ScrapingWorkerSettings, "queue_name")


def test_scraping_worker_concurrency_and_timeout_are_unaffected_by_debate_mode():
    settings = get_settings()
    assert ScrapingWorkerSettings.max_jobs == settings.scraping_worker_concurrency
    assert ScrapingWorkerSettings.job_timeout == max(
        int(settings.scraping_worker_job_timeout_seconds or 0), 21600
    )


# --- Debate worker consumes only Debate's own queue/functions ----------------


def test_debate_worker_queue_is_dedicated_and_distinct_from_scraping():
    settings = get_settings()
    assert DebateWorkerSettings.queue_name == settings.debate_worker_queue_name
    assert DebateWorkerSettings.queue_name == "debate"
    # The scraping worker has no queue_name at all (ARQ default "arq:queue"),
    # so the two workers can never poll the same queue.
    assert not hasattr(ScrapingWorkerSettings, "queue_name")


def test_debate_worker_registers_only_debate_functions():
    names = {
        getattr(f, "__name__", None) or getattr(getattr(f, "coroutine", None), "__name__", "")
        for f in DebateWorkerSettings.functions
    }
    assert names == {"run_debate_session_job", "recover_debate_sessions"}


def test_debate_worker_concurrency_and_timeout_are_its_own_settings():
    settings = get_settings()
    assert DebateWorkerSettings.max_jobs == settings.debate_worker_concurrency == 2
    assert DebateWorkerSettings.job_timeout == settings.debate_worker_job_timeout_seconds
    # max_tries=1 everywhere: a failed debate run is never silently retried by
    # ARQ itself (that could re-issue paid LLM calls nobody asked to repeat).
    assert DebateWorkerSettings.max_tries == 1
    for f in DebateWorkerSettings.functions:
        assert getattr(f, "max_tries", None) == 1


# --- Queue-targeted enqueue --------------------------------------------------


async def test_enqueue_debate_execution_targets_the_dedicated_queue_not_the_default(
    monkeypatch: pytest.MonkeyPatch,
):
    settings = get_settings()
    monkeypatch.setattr(settings, "debate_inline_execution", False)
    enqueued: dict = {}

    class _FakeRedis:
        async def enqueue_job(self, name, *args, **kwargs):
            enqueued["name"] = name
            enqueued["queue_name"] = kwargs.get("_queue_name")

        async def close(self):
            return None

    async def fake_create_pool(*args, **kwargs):
        return _FakeRedis()

    import arq

    monkeypatch.setattr(arq, "create_pool", fake_create_pool)

    await debate_service.enqueue_debate_execution("session-123")

    assert enqueued["name"] == debate_service.DEBATE_ARQ_JOB_NAME
    assert enqueued["queue_name"] == settings.debate_worker_queue_name
    assert enqueued["queue_name"] != "arq:queue"


async def test_enqueue_debate_execution_requires_dedicated_worker_raises_instead_of_falling_back_inline(
    monkeypatch: pytest.MonkeyPatch,
):
    """With debate_inline_execution explicitly False (as the Docker test
    environment sets it), a broken queue must surface loudly, never fall
    back to running the paid LLM calls inline unannounced."""
    settings = get_settings()
    monkeypatch.setattr(settings, "debate_inline_execution", False)

    import arq

    async def broken_create_pool(*args, **kwargs):
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(arq, "create_pool", broken_create_pool)

    fallback_called = {"value": False}

    async def fake_standalone(session_id: str) -> None:
        fallback_called["value"] = True

    monkeypatch.setattr(debate_service, "_run_debate_session_standalone", fake_standalone)

    with pytest.raises(ConnectionError):
        await debate_service.enqueue_debate_execution("session-456")
    assert fallback_called["value"] is False


async def test_enqueue_debate_execution_still_falls_back_inline_when_unset(
    monkeypatch: pytest.MonkeyPatch,
):
    """The default (None) behavior — auto-detect with a silent fallback —
    is preserved for plain pytest/dev-shell convenience; only an EXPLICIT
    `false` opts into the stricter Docker-test behavior above."""
    settings = get_settings()
    monkeypatch.setattr(settings, "debate_inline_execution", None)
    monkeypatch.setattr(settings, "environment", "production")

    import arq

    async def broken_create_pool(*args, **kwargs):
        raise ConnectionError("redis unreachable")

    monkeypatch.setattr(arq, "create_pool", broken_create_pool)

    fallback_called = {"value": False}

    async def fake_standalone(session_id: str) -> None:
        fallback_called["value"] = True

    monkeypatch.setattr(debate_service, "_run_debate_session_standalone", fake_standalone)

    import asyncio as _asyncio

    await debate_service.enqueue_debate_execution("session-789")
    # The inline path is a fire-and-forget asyncio.create_task; yield once so
    # it actually runs before asserting on its side effect.
    await _asyncio.sleep(0)
    assert fallback_called["value"] is True


# --- Duplicate job delivery through the dedicated worker's own entrypoint ----


async def test_run_debate_session_job_duplicate_delivery_executes_only_once(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    """Exercises the real `_claim_execution_slot` gate inside
    `execute_debate_session`, reached through the dedicated worker's own
    `run_debate_session_job` entrypoint — not a mocked stand-in for it.
    Only the orchestrator's provider-calling engine is faked, so a scripted
    duplicate ARQ delivery can't make two real LLM calls."""
    from app.llm.debate_orchestrator import DebateRunResult

    turn = await _make_turn(db, auth, model_ids=[MODEL_A, MODEL_B])
    session, _, claimed = await debate_service.start_debate_session(db, auth, turn.id)
    await db.commit()
    assert claimed is True

    factory = async_sessionmaker(bind=db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(debate_service, "AsyncSessionLocal", factory)

    async def _noop_publish(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(debate_service, "publish_debate_event", _noop_publish)

    run_count = {"value": 0}

    class _FakeOrchestrator:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, *_args, **_kwargs) -> DebateRunResult:
            run_count["value"] += 1
            return DebateRunResult()

    monkeypatch.setattr(debate_service, "DebateOrchestrator", _FakeOrchestrator)

    import asyncio as _asyncio

    # ARQ's job runner calls the registered function with a worker ctx dict
    # as the first positional argument.
    await _asyncio.gather(
        debate_service.run_debate_session_job({}, session.id),
        debate_service.run_debate_session_job({}, session.id),
    )
    assert run_count["value"] == 1
