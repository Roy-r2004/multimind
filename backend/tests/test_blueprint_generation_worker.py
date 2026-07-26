"""Blueprint generation worker cancellation and terminal-state coverage."""

import asyncio

import pytest
from test_blueprint_workflow_integration import mission_and_blueprint

from app.db.models import ScrapingBlueprintStatus
from app.services.scraping import blueprint_generation_orchestrator as orchestrator


@pytest.mark.asyncio
async def test_cancelled_generation_is_terminalized_and_reraises(db, auth, monkeypatch):
    _, blueprint = await mission_and_blueprint(db, auth, ScrapingBlueprintStatus.QUEUED)
    await db.commit()

    class CancelledProvider:
        def __init__(self, _settings) -> None:
            pass

        async def generate_blueprint(self, **_kwargs):
            raise asyncio.CancelledError()

    monkeypatch.setattr(orchestrator, "AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(orchestrator, "OpenRouterBlueprintProvider", CancelledProvider)

    with pytest.raises(asyncio.CancelledError):
        await orchestrator.run_blueprint_generation({}, blueprint.id)

    refreshed = await db.get(type(blueprint), blueprint.id)
    assert refreshed is not None
    assert refreshed.status == ScrapingBlueprintStatus.FAILED
    assert refreshed.failed_at is not None
    assert refreshed.completed_at == refreshed.failed_at
    assert refreshed.generation_error == "Blueprint generation was cancelled. Retry the request."


@pytest.mark.asyncio
async def test_terminal_generation_is_idempotently_ignored(db, auth, monkeypatch):
    _, blueprint = await mission_and_blueprint(db, auth, ScrapingBlueprintStatus.READY_FOR_REVIEW)
    await db.commit()

    class ProviderMustNotRun:
        def __init__(self, _settings) -> None:
            raise AssertionError("terminal blueprint should not invoke a provider")

    monkeypatch.setattr(orchestrator, "AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(orchestrator, "OpenRouterBlueprintProvider", ProviderMustNotRun)

    await orchestrator.run_blueprint_generation({}, blueprint.id)

    refreshed = await db.get(type(blueprint), blueprint.id)
    assert refreshed is not None
    assert refreshed.status == ScrapingBlueprintStatus.READY_FOR_REVIEW
