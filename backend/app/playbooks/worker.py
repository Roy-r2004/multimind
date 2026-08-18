"""Dedicated ARQ worker for Playbook generation jobs.

This process consumes the Playbook queue and must not share the scraping/maps
queue (ARQ default ``arq:queue``).

Job-signature convention
------------------------
Enqueue primitive identifiers only::

    playbook_id, run_id, org_id, user_id

Do not pass SQLAlchemy ORM instances, AuthContext, database sessions, request
objects, access tokens, or complete transcript objects through Redis.

Jobs reconstruct ownership from persisted IDs and verify the Playbook belongs
to the given user and organization. Duplicate ARQ deliveries are ignored after
the run is claimed (``queued`` → ``processing``).

Enqueue onto this worker with ``_queue_name`` equal to
``settings.playbook_worker_queue_name`` (default ``playbooks``), or create the
ARQ pool with that ``default_queue_name``. The scraping worker uses the ARQ
default queue ``arq:queue`` and will not see Playbook jobs.

Retry behavior
--------------
Worker default ``max_tries`` is 5 (ARQ default). Registered Playbook jobs set
``max_tries=1`` so a failed run is not retried by ARQ. Failed runs are marked
``failed`` in a recovery transaction; a later ``POST /playbooks/me/generate``
may create a new run.
"""

from __future__ import annotations

from urllib.parse import urlparse

from arq.connections import RedisSettings
from arq.worker import func

from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import AsyncSessionLocal

logger = get_logger(__name__)

PLAYBOOK_WORKER_NAME = "playbooks"


def playbook_redis_settings() -> RedisSettings:
    parsed = urlparse(get_settings().redis_url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or "0"),
        password=parsed.password,
    )


def playbook_queue_name() -> str:
    return get_settings().playbook_worker_queue_name


async def playbook_worker_ping(ctx: dict) -> dict[str, str]:
    """Diagnostic job: proves the Playbook queue can run a registered function.

    No database writes, model calls, transcript reconstruction, or network I/O.
    """
    if not isinstance(ctx, dict):
        raise RuntimeError("Playbook worker context is invalid")
    logger.info("playbook_worker_ping", worker=PLAYBOOK_WORKER_NAME)
    return {"worker": PLAYBOOK_WORKER_NAME, "status": "ok"}


async def generate_playbook_job(
    ctx: dict,
    playbook_id: str,
    run_id: str,
    org_id: str,
    user_id: str,
) -> dict:
    """Run first full Playbook generation for a previously queued run."""
    from app.services.playbook_generation_service import playbook_generation_service

    if not isinstance(ctx, dict):
        raise RuntimeError("Playbook worker context is invalid")
    session_factory = ctx.get("session_factory") or AsyncSessionLocal
    try:
        async with session_factory() as db:
            from app.db.models import PLAYBOOK_RUN_KIND_INCREMENTAL, PlaybookRun

            run = await db.get(PlaybookRun, run_id)
            execute = (
                playbook_generation_service.execute_incremental_generation
                if run is not None and run.kind == PLAYBOOK_RUN_KIND_INCREMENTAL
                else playbook_generation_service.execute_full_generation
            )
            return await execute(
                db,
                playbook_id=playbook_id,
                run_id=run_id,
                org_id=org_id,
                user_id=user_id,
            )
    except Exception:
        logger.exception("generate_playbook_job_failed", run_id=run_id)
        async with session_factory() as db:
            await playbook_generation_service.mark_run_failed(
                db, run_id, "Playbook generation failed.", playbook_id=playbook_id
            )
        return {"status": "failed", "run_id": run_id, "skipped": False}


async def startup(ctx: dict) -> None:
    setup_logging()
    logger.info("Playbook worker starting")
    print("playbook-worker: starting", flush=True)

    redis = ctx.get("redis")
    if redis is None:
        raise RuntimeError("Playbook worker Redis connection is missing")
    await redis.ping()

    ctx["session_factory"] = AsyncSessionLocal
    logger.info(
        "Playbook worker ready",
        queue=playbook_queue_name(),
        concurrency=get_settings().playbook_worker_concurrency,
    )
    print("playbook-worker: ready", flush=True)


async def shutdown(ctx: dict) -> None:
    logger.info("Playbook worker stopping")
    print("playbook-worker: stopping", flush=True)
    ctx.pop("session_factory", None)
    print("playbook-worker: shutdown complete", flush=True)


class WorkerSettings:
    functions = [
        func(playbook_worker_ping, max_tries=1),
        func(generate_playbook_job, max_tries=1),
    ]
    queue_name = get_settings().playbook_worker_queue_name
    redis_settings = playbook_redis_settings()
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = get_settings().playbook_worker_concurrency
    job_timeout = get_settings().playbook_worker_job_timeout_seconds
    keep_result = get_settings().playbook_worker_keep_result_seconds
    health_check_interval = 30
    max_tries = 5
