"""ARQ worker entrypoint for scraping execution campaigns."""

from __future__ import annotations

from urllib.parse import urlparse

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.services.scraping.execution_orchestrator import (
    recover_scraping_executions,
    run_scraping_execution,
)


def _redis_settings() -> RedisSettings:
    parsed = urlparse(get_settings().redis_url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or "0"),
        password=parsed.password,
    )


async def startup(ctx: dict) -> None:
    print("scraping-worker: starting country-aware mock execution worker", flush=True)
    await recover_scraping_executions(ctx)


async def shutdown(ctx: dict) -> None:
    print("scraping-worker: shutdown complete", flush=True)


class WorkerSettings:
    functions = [run_scraping_execution]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()
    max_jobs = get_settings().scraping_worker_concurrency
    job_timeout = get_settings().scraping_worker_job_timeout_seconds
