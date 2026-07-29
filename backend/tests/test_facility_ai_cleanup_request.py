"""API path to apply AI cleanup after a census already finished."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import ConflictError
from app.db.models import ScrapingExecutionStatus
from app.services.scraping.execution_service import ScrapingExecutionService


def _auth():
    return SimpleNamespace(org_id="org-1")


@pytest.mark.asyncio
async def test_request_facility_ai_cleanup_enqueues_for_completed_execution():
    service = ScrapingExecutionService()
    execution = SimpleNamespace(
        id="exec-1",
        status=ScrapingExecutionStatus.COMPLETED,
    )
    count_result = MagicMock()
    count_result.scalar_one.return_value = 361

    db = AsyncMock()
    db.execute = AsyncMock(return_value=count_result)
    db.commit = AsyncMock()

    with (
        patch.object(service, "_execution_row", new=AsyncMock(return_value=execution)),
        patch.object(service, "emit_event", new=AsyncMock()) as emit,
        patch.object(service, "enqueue_facility_ai_cleanup", new=AsyncMock()) as enqueue,
        patch.object(service, "_summary", return_value=SimpleNamespace(id="exec-1")),
    ):
        result = await service.request_facility_ai_cleanup(db, _auth(), "exec-1")

    assert result.id == "exec-1"
    emit.assert_awaited_once()
    assert emit.await_args.args[2] == "facility_ai_cleanup_requested"
    enqueue.assert_awaited_once_with("exec-1")


@pytest.mark.asyncio
async def test_request_facility_ai_cleanup_rejects_empty_roster():
    service = ScrapingExecutionService()
    execution = SimpleNamespace(id="exec-1", status=ScrapingExecutionStatus.COMPLETED)
    count_result = MagicMock()
    count_result.scalar_one.return_value = 0
    db = AsyncMock()
    db.execute = AsyncMock(return_value=count_result)

    with (
        patch.object(service, "_execution_row", new=AsyncMock(return_value=execution)),
        pytest.raises(ConflictError, match="no published facilities"),
    ):
        await service.request_facility_ai_cleanup(db, _auth(), "exec-1")


@pytest.mark.asyncio
async def test_request_facility_ai_cleanup_rejects_running_execution():
    service = ScrapingExecutionService()
    execution = SimpleNamespace(id="exec-1", status=ScrapingExecutionStatus.RUNNING)
    count_result = MagicMock()
    count_result.scalar_one.return_value = 10
    db = AsyncMock()
    db.execute = AsyncMock(return_value=count_result)

    with (
        patch.object(service, "_execution_row", new=AsyncMock(return_value=execution)),
        pytest.raises(ConflictError, match="still running"),
    ):
        await service.request_facility_ai_cleanup(db, _auth(), "exec-1")
