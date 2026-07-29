"""ARQ worker entrypoint for scraping execution campaigns."""

from __future__ import annotations

from urllib.parse import urlparse

from arq.connections import RedisSettings

from app.core.config import get_settings
from app.services.scraping.execution_orchestrator import (
    recover_scraping_executions,
    run_scraping_execution,
)
from app.services.scraping.mission_campaign_mock_worker import run_mission_campaign_mock
from app.services.scraping.facility_package_worker import run_facility_package_pipeline
from app.services.scraping.blueprint_generation_orchestrator import (
    recover_blueprint_generations,
    run_blueprint_generation,
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
    await recover_blueprint_generations(ctx)


async def shutdown(ctx: dict) -> None:
    print("scraping-worker: shutdown complete", flush=True)


class WorkerSettings:
    functions = [
        run_scraping_execution,
        run_mission_campaign_mock,
        run_blueprint_generation,
        run_facility_package_pipeline,
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = _redis_settings()
    max_jobs = get_settings().scraping_worker_concurrency
    job_timeout = get_settings().scraping_worker_job_timeout_seconds
