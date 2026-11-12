"""Dedicated ARQ worker for AI Debate Mode execution.

This process consumes ONLY the Debate queue (``settings.debate_worker_queue_name``,
default ``"debate"``) and must not share the scraping/maps queue (ARQ default
``arq:queue``, which ``app.scraping.worker`` also leaves unset) or the Playbook/
Document queues. Debate Mode ships no job functions on any other worker —
``app.scraping.worker`` has none of its own imports from this package, and this
module imports nothing from ``app.services.scraping.*``.

Job-signature convention
-------------------------
``run_debate_session_job`` and ``recover_debate_sessions`` take only a primitive
``session_id`` (or nothing, for the recovery cron) — never a SQLAlchemy ORM
instance, AuthContext, database session, or request object. Both are defined in
``app.services.debate_service`` (not here) since they need the same persistence
helpers the HTTP-request-scoped "start" path uses; this module only wires them
onto their own dedicated worker/queue.

Retry behavior
---------------
``max_tries=1`` on both registered functions: a failed debate run must never be
silently retried by ARQ itself, since that could re-issue paid LLM calls the
user never asked to repeat. A failed/stuck session is instead recovered to a
terminal ``FAILED`` state by ``recover_debate_sessions`` (see its docstring) so
a human decides whether to start a fresh, explicitly-counted attempt.
"""

from __future__ import annotations

from urllib.parse import urlparse

from arq import cron
from arq.connections import RedisSettings
from arq.worker import func

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.services.debate_service import recover_debate_sessions, run_debate_session_job

logger = get_logger(__name__)

DEBATE_WORKER_NAME = "debate"


def debate_redis_settings() -> RedisSettings:
    parsed = urlparse(get_settings().redis_url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or "0"),
        password=parsed.password,
    )


def debate_queue_name() -> str:
    return get_settings().debate_worker_queue_name


async def startup(ctx: dict) -> None:
    setup_logging()
    logger.info("debate_worker_starting", worker=DEBATE_WORKER_NAME)
    print("debate-worker: starting AI Debate Mode worker", flush=True)

    redis = ctx.get("redis")
    if redis is None:
        raise RuntimeError("Debate worker Redis connection is missing")
    await redis.ping()

    # Catch up on anything left RUNNING by a previous process before this one
    # takes its first job, exactly like the scraping worker's own startup
    # recovery call — just scoped to Debate's own queue/state, not shared.
    await recover_debate_sessions(ctx)

    logger.info(
        "debate_worker_ready",
        queue=debate_queue_name(),
        concurrency=get_settings().debate_worker_concurrency,
    )
    print("debate-worker: ready", flush=True)


async def shutdown(ctx: dict) -> None:
    del ctx
    logger.info("debate_worker_stopping", worker=DEBATE_WORKER_NAME)
    print("debate-worker: shutdown complete", flush=True)


class WorkerSettings:
    functions = [
        func(run_debate_session_job, max_tries=1),
        func(recover_debate_sessions, max_tries=1),
    ]
    queue_name = get_settings().debate_worker_queue_name
    cron_jobs = [
        # Debate runs are short (<=180s); a minute's cadence is ample to catch
        # one stuck past debate_execution_stale_seconds without adding DB load.
        cron(recover_debate_sessions, second={10, 40}, run_at_startup=False),
    ]
    redis_settings = debate_redis_settings()
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = get_settings().debate_worker_concurrency
    job_timeout = get_settings().debate_worker_job_timeout_seconds
    keep_result = get_settings().debate_worker_keep_result_seconds
    health_check_interval = 30
    max_tries = 1
