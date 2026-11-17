"""Dedicated ARQ worker for durable chat-memory rebuilds.

This process consumes ONLY the chat-memory queue
(``settings.chat_memory_worker_queue_name``, default ``"chat_memory"``) and
shares no queue with scraping/maps, Playbooks, Documents, or Debate Mode.

What runs here
---------------
Only the delete/restore/regenerate "rebuild memory from scratch" path —
``process_chat_memory_rebuild_job`` — and its recovery sweep,
``recover_pending_chat_memory_rebuilds``. The ordinary per-new-turn
incremental merge (at most one newly-compactable turn per completed turn)
stays exactly what it was before: an in-process ``asyncio`` background task
started from ``app.services.chat_service._run_chat_memory_update_best_effort``.
That path was never unbounded inside an HTTP request handler, so it did not
need moving to a durable queue — only the "wipe everything, redo everything"
rebuild triggered by invalidation did.

Job-signature convention
-------------------------
``process_chat_memory_rebuild_job`` takes only primitive ids (``chat_id``,
``org_id``, ``project_id``) — never a SQLAlchemy ORM instance, AuthContext,
database session, or request object — matching the same convention already
used by ``app.debate.worker``/``app.services.debate_service``. Both job
functions are defined in ``app.services.chat_memory_service`` (not here),
since they need the same persistence helpers
(``claim_chat_for_rebuild``/``release_chat_rebuild_claim``/
``merge_expired_turns``) that the invalidate-triggered caller uses; this
module only wires them onto their own dedicated worker/queue.

Retry and recovery behavior
-----------------------------
``max_tries=3`` on the rebuild job: unlike a Debate session (where a partial
retry could resume mid-way through a stateless multi-round engine that
cannot safely resume), a chat-memory merge pass is turn-by-turn and each
successful merge commits its own watermark advance immediately — ARQ
re-delivering the same job after a transient failure (and
``claim_chat_for_rebuild`` admitting it, since the first attempt's claim
either never landed or already expired) simply continues from wherever the
watermark actually got to. The periodic recovery sweep,
``recover_pending_chat_memory_rebuilds``, additionally catches any chat
whose rebuild request was never re-enqueued at all (Redis was down when
``invalidate_memory`` ran) or whose claim went stale from a worker that
crashed/was killed outright (ARQ retries alone would not see those).
"""

from __future__ import annotations

from typing import ClassVar
from urllib.parse import urlparse

from arq import cron
from arq.connections import RedisSettings
from arq.worker import func

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import AsyncSessionLocal
from app.services.chat_memory_service import (
    process_chat_memory_rebuild_job,
    recover_pending_chat_memory_rebuilds,
)

logger = get_logger(__name__)

CHAT_MEMORY_WORKER_NAME = "chat_memory"


def chat_memory_redis_settings() -> RedisSettings:
    parsed = urlparse(get_settings().redis_url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or "0"),
        password=parsed.password,
    )


def chat_memory_queue_name() -> str:
    return get_settings().chat_memory_worker_queue_name


async def startup(ctx: dict) -> None:
    setup_logging()
    logger.info("chat_memory_worker_starting", worker=CHAT_MEMORY_WORKER_NAME)

    redis = ctx.get("redis")
    if redis is not None:
        await redis.ping()
    ctx["session_factory"] = AsyncSessionLocal

    # Catch up on anything a previous process left pending before this one
    # takes its first job — exactly like the documents/debate workers' own
    # startup recovery calls, just scoped to this worker's own state.
    recovered = await recover_pending_chat_memory_rebuilds(ctx)

    logger.info(
        "chat_memory_worker_ready",
        queue=chat_memory_queue_name(),
        concurrency=get_settings().chat_memory_worker_concurrency,
        recovered_on_startup=recovered,
    )


async def shutdown(ctx: dict) -> None:
    ctx.pop("session_factory", None)
    logger.info("chat_memory_worker_stopping", worker=CHAT_MEMORY_WORKER_NAME)


class WorkerSettings:
    functions: ClassVar[list] = [
        func(process_chat_memory_rebuild_job, max_tries=3),
        func(recover_pending_chat_memory_rebuilds, max_tries=1),
    ]
    queue_name = get_settings().chat_memory_worker_queue_name
    cron_jobs: ClassVar[list] = [
        # Rebuild jobs are bounded to a few dozen turns each
        # (chat_memory_rebuild_max_merges_per_job) and re-enqueue their own
        # continuation, so a 5-minute sweep cadence is ample to catch a
        # dropped enqueue or a crashed worker's stale claim without adding
        # meaningful DB load.
        cron(recover_pending_chat_memory_rebuilds, minute=set(range(0, 60, 5))),
    ]
    redis_settings = chat_memory_redis_settings()
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = get_settings().chat_memory_worker_concurrency
    job_timeout = get_settings().chat_memory_worker_job_timeout_seconds
    keep_result = get_settings().chat_memory_worker_keep_result_seconds
    health_check_interval = 30
    max_tries = 3
