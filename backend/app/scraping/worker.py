"""ARQ worker entrypoint for scraping execution campaigns."""

from __future__ import annotations

from urllib.parse import urlparse

from arq import cron
from arq.connections import RedisSettings

from app.core.config import get_settings
from app.services.scraping.execution_orchestrator import (
    recover_scraping_executions,
    run_facility_ai_cleanup_job,
    run_scraping_execution,
)
from app.services.scraping.maps_census_service import (
    auto_refresh_maps_census_websites,
    recover_maps_census_runs,
    refresh_maps_census_websites_job,
    run_maps_census_job,
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
    print("scraping-worker: starting country-aware source discovery worker", flush=True)
    await recover_scraping_executions(ctx)


async def shutdown(ctx: dict) -> None:
    print("scraping-worker: shutdown complete", flush=True)


# Full-census runs need multi-hour slices. Never inherit a short Coolify/env override.
_MIN_JOB_TIMEOUT_SECONDS = 21600


class WorkerSettings:
    functions = [
        run_scraping_execution,
        run_facility_ai_cleanup_job,
        recover_scraping_executions,
        run_maps_census_job,
        recover_maps_census_runs,
        refresh_maps_census_websites_job,
    ]
    cron_jobs = [
        # Reclaim zombie "running" executions if the worker/job died mid-flight.
        cron(recover_scraping_executions, second={0, 30}, run_at_startup=False),
        cron(recover_maps_census_runs, second={15, 45}, run_at_startup=False),
        # Backfill missing official websites on completed Maps census runs — fully
        # automatic, replaces needing to click "Find missing websites" manually.
        cron(auto_refresh_maps_census_websites, minute={0, 30}, run_at_startup=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()
    max_jobs = get_settings().scraping_worker_concurrency
    job_timeout = max(
        int(get_settings().scraping_worker_job_timeout_seconds or 0),
        _MIN_JOB_TIMEOUT_SECONDS,
    )
