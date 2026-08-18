"""Playbook Phase 3: dedicated ARQ worker infrastructure."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlparse

import pytest
from arq.constants import default_queue_name as scraping_default_queue_name
from arq.worker import create_worker

from app.core.config import get_settings
from app.playbooks.worker import (
    PLAYBOOK_WORKER_NAME,
    WorkerSettings,
    playbook_queue_name,
    playbook_redis_settings,
    playbook_worker_ping,
    shutdown,
    startup,
)
from app.scraping.worker import WorkerSettings as ScrapingWorkerSettings

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (
    REPO_ROOT / "docker-compose.yml",
    REPO_ROOT / "docker-compose.prod.yml",
)
ENV_EXAMPLE = REPO_ROOT / ".env.example"

SCRAPING_JOB_MARKERS = (
    "run_scraping_execution",
    "run_facility_ai_cleanup_job",
    "recover_scraping_executions",
    "run_maps_census_job",
    "recover_maps_census_runs",
    "refresh_maps_census_websites_job",
    "run_maps_census_enrichment_job",
    "run_maps_enrichment_batch_job",
    "run_maps_keep_drop_job",
    "auto_refresh_maps_census_websites",
)


def _registered_names(settings_cls) -> set[str]:
    return set(create_worker(settings_cls).functions)


def _compose_service(path: Path, service_name: str) -> str:
    text = path.read_text(encoding="utf-8")
    marker = f"  {service_name}:"
    start = text.find(marker)
    assert start >= 0, f"{service_name} missing from {path.name}"
    rest = text[start + len(marker) :]
    next_service = rest.find("\n  ")
    # Find the next top-level service (two-space indent + name + colon) after a blank line pattern.
    end = len(rest)
    for index, line in enumerate(rest.splitlines(keepends=True)):
        if index == 0:
            continue
        if line.startswith("  ") and not line.startswith("    ") and line.strip().endswith(":"):
            # next service or volumes
            consumed = sum(len(item) for item in rest.splitlines(keepends=True)[:index])
            end = consumed
            break
    return marker + rest[:end]


@pytest.mark.asyncio
async def test_playbook_worker_module_imports_and_settings():
    settings = get_settings()
    worker = create_worker(WorkerSettings)
    parsed = urlparse(settings.redis_url)

    assert WorkerSettings is not None
    assert callable(playbook_worker_ping)
    assert worker.queue_name == settings.playbook_worker_queue_name == "playbooks"
    assert worker.queue_name == playbook_queue_name()
    assert worker.max_jobs == settings.playbook_worker_concurrency == 1
    assert worker.job_timeout_s == settings.playbook_worker_job_timeout_seconds == 3600
    assert worker.keep_result_s == settings.playbook_worker_keep_result_seconds == 3600
    assert worker.health_check_key == f"{worker.queue_name}:health-check"
    assert "playbook_worker_ping" in worker.functions
    assert "generate_playbook_job" in worker.functions
    redis = playbook_redis_settings()
    assert redis.host == (parsed.hostname or "localhost")
    assert redis.port == (parsed.port or 6379)
    ping_fn = worker.functions["playbook_worker_ping"]
    assert ping_fn.max_tries == 1
    generate_fn = worker.functions["generate_playbook_job"]
    assert generate_fn.max_tries == 1


def test_queue_isolation_from_scraping_and_maps():
    playbook_names = _registered_names(WorkerSettings)
    scraping_names = _registered_names(ScrapingWorkerSettings)
    scraping_worker = create_worker(ScrapingWorkerSettings)

    assert playbook_queue_name() != scraping_default_queue_name
    assert WorkerSettings.queue_name != scraping_worker.queue_name
    assert scraping_worker.queue_name == scraping_default_queue_name
    assert playbook_names == {"playbook_worker_ping", "generate_playbook_job"}
    assert "playbook_worker_ping" not in scraping_names
    assert "generate_playbook_job" not in scraping_names
    for marker in SCRAPING_JOB_MARKERS:
        assert marker not in playbook_names
        assert any(marker in name for name in scraping_names), marker


@pytest.mark.asyncio
async def test_diagnostic_ping_is_deterministic_and_side_effect_free(
    monkeypatch: pytest.MonkeyPatch,
):
    db_write = Mock(side_effect=AssertionError("diagnostic job must not touch the database"))
    reconstruct = AsyncMock(side_effect=AssertionError("diagnostic job must not reconstruct transcripts"))
    retrieve = AsyncMock(side_effect=AssertionError("diagnostic job must not run retrieval"))
    vision = AsyncMock(side_effect=AssertionError("diagnostic job must not call vision"))
    provider = Mock(side_effect=AssertionError("diagnostic job must not call models"))

    monkeypatch.setattr("app.db.session.AsyncSessionLocal", db_write)
    monkeypatch.setattr(
        "app.services.playbook_source_service.playbook_source_service.assemble_all_transcripts",
        reconstruct,
        raising=False,
    )
    monkeypatch.setattr(
        "app.services.brain_knowledge_service.brain_knowledge_service.retrieve",
        retrieve,
    )
    monkeypatch.setattr("app.services.chat_vision.ensure_image_context_for_turn", vision)
    monkeypatch.setattr("app.llm.providers.get_provider_registry", provider)

    result = await playbook_worker_ping({"redis": object()})
    assert result == {"worker": PLAYBOOK_WORKER_NAME, "status": "ok"}
    db_write.assert_not_called()
    reconstruct.assert_not_called()
    retrieve.assert_not_called()
    vision.assert_not_called()
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_diagnostic_ping_fails_on_invalid_context():
    with pytest.raises(RuntimeError, match="invalid"):
        await playbook_worker_ping(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_startup_and_shutdown_lifecycle_without_leaking_connections():
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    first = {"redis": redis}
    await startup(first)
    assert first["session_factory"] is not None
    redis.ping.assert_awaited_once()
    await shutdown(first)
    assert "session_factory" not in first

    second = {"redis": redis}
    await startup(second)
    await shutdown(second)
    assert redis.ping.await_count == 2
    assert "session_factory" not in second


@pytest.mark.asyncio
async def test_startup_fails_clearly_without_redis():
    with pytest.raises(RuntimeError, match="Redis connection is missing"):
        await startup({})


@pytest.mark.asyncio
async def test_startup_fails_clearly_when_redis_ping_fails():
    redis = AsyncMock()
    redis.ping = AsyncMock(side_effect=ConnectionError("redis refused connection"))
    with pytest.raises(ConnectionError, match="redis refused connection"):
        await startup({"redis": redis})


def test_compose_and_env_documentation():
    env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "PLAYBOOK_WORKER_QUEUE_NAME=playbooks" in env_text
    assert "PLAYBOOK_WORKER_CONCURRENCY=1" in env_text
    assert "PLAYBOOK_WORKER_JOB_TIMEOUT_SECONDS=3600" in env_text
    assert "PLAYBOOK_WORKER_KEEP_RESULT_SECONDS=3600" in env_text
    assert "PLAYBOOK_EXTRACTION_MODEL_ID=gpt-4.1" in env_text
    assert "PLAYBOOK_CORE_SUMMARY_MAX_CHARS=4000" in env_text

    for path in COMPOSE_FILES:
        block = _compose_service(path, "playbook-worker")
        scraping = _compose_service(path, "scraping-worker")
        assert "app.playbooks.worker.WorkerSettings" in block
        assert "command:" in block
        assert "ports:" not in block
        assert "postgres:" in block
        assert "redis:" in block
        assert "arq" in block
        assert "--check" in block
        assert "app.playbooks.worker.WorkerSettings" in block
        assert "app.scraping.worker.WorkerSettings" in scraping
        assert "app.playbooks.worker.WorkerSettings" not in scraping
        assert "app.scraping.worker.WorkerSettings" not in block
