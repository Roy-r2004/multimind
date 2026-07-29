from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import ScrapingExecutionStatus
from app.services.scraping.execution_orchestrator import recover_scraping_executions


@pytest.mark.asyncio
async def test_recover_resets_stale_running_execution_before_enqueue():
    execution = SimpleNamespace(
        id="exec-1",
        status=ScrapingExecutionStatus.RUNNING,
        error_message="old",
        heartbeat_at=None,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [execution]

    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=None)

    session_factory = MagicMock(return_value=db)

    with (
        patch(
            "app.services.scraping.execution_orchestrator.AsyncSessionLocal",
            session_factory,
        ),
        patch(
            "app.services.scraping.execution_orchestrator._reset_running_work_to_queued",
            new_callable=AsyncMock,
        ) as reset_mock,
        patch(
            "app.services.scraping.execution_orchestrator.execution_service"
        ) as exec_service,
        patch(
            "app.services.scraping.execution_orchestrator.get_settings",
            return_value=SimpleNamespace(scraping_execution_stale_seconds=120),
        ),
    ):
        exec_service.emit_event = AsyncMock()
        exec_service.enqueue_execution = AsyncMock()
        await recover_scraping_executions({})

    reset_mock.assert_awaited_once()
    assert execution.status == ScrapingExecutionStatus.QUEUED
    assert execution.error_message is None
    exec_service.enqueue_execution.assert_awaited_once_with("exec-1")
