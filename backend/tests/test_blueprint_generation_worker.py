"""Blueprint generation worker cancellation and terminal-state coverage."""

import asyncio

import pytest
from test_blueprint_workflow_integration import mission_and_blueprint

from app.db.models import ScrapingBlueprintStatus
from app.services.scraping import blueprint_generation_orchestrator as orchestrator
from app.services.scraping.blueprint_provider import BlueprintProviderError


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


@pytest.mark.asyncio
async def test_provider_failure_persists_safe_stage_diagnostics(db, auth, monkeypatch):
    _, blueprint = await mission_and_blueprint(db, auth, ScrapingBlueprintStatus.QUEUED)
    await db.commit()

    class FailingProvider:
        def __init__(self, _settings) -> None:
            pass

        async def generate_blueprint(self, **_kwargs):
            raise BlueprintProviderError(
                "invalid_model",
                "OpenRouter blueprint generation failed.",
                stage="structuring",
                model="openai/gpt-4.1-mini",
                http_status=400,
                provider_error_code="invalid_model",
                provider_error_type=None,
                provider_message="No model found for gpt-4.1-mini",
                retryable=False,
            )

    monkeypatch.setattr(orchestrator, "AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(orchestrator, "OpenRouterBlueprintProvider", FailingProvider)

    await orchestrator.run_blueprint_generation({}, blueprint.id)

    refreshed = await db.get(type(blueprint), blueprint.id)
    assert refreshed is not None
    assert refreshed.status == ScrapingBlueprintStatus.FAILED
    assert refreshed.generation_error == (
        "Blueprint generation failed during structuring. Review configuration and retry."
    )
    assert refreshed.provider_execution_metadata == {
        "failure": {
            "blueprint_id": blueprint.id,
            "stage": "structuring",
            "model": "openai/gpt-4.1-mini",
            "http_status": 400,
            "provider_error_code": "invalid_model",
            "provider_error_type": None,
            "provider_message": "No model found for gpt-4.1-mini",
            "retryable": False,
            "category": "invalid_model",
        }
    }
    assert refreshed.structured_blueprint is None


@pytest.mark.asyncio
async def test_commit_failure_marks_running_blueprint_failed(db, auth, monkeypatch):
    _, blueprint = await mission_and_blueprint(db, auth, ScrapingBlueprintStatus.QUEUED)
    await db.commit()

    class SuccessThenCommitFails:
        def __init__(self, _settings) -> None:
            pass

        async def generate_blueprint(self, **_kwargs):
            from test_country_blueprint_foundation import valid_structured_blueprint_v2

            from app.schemas.api import CountryMaximumCoverageStructuredBlueprintV2
            from app.services.scraping.blueprint_provider import BlueprintProviderResult

            return BlueprintProviderResult(
                human_readable_blueprint="Research",
                structured_blueprint=CountryMaximumCoverageStructuredBlueprintV2.model_validate(
                    valid_structured_blueprint_v2()
                ),
                citations=[],
                provider="openrouter",
                model_id="openai/gpt-5.5",
            )

    commit_calls = {"n": 0}
    original_commit = db.commit

    async def flaky_commit():
        commit_calls["n"] += 1
        # First commit marks RUNNING; second (ready_for_review) fails;
        # third commit terminalizes FAILED after rollback.
        if commit_calls["n"] == 2:
            from sqlalchemy.exc import SQLAlchemyError

            raise SQLAlchemyError("value too long for type character varying(10)")
        await original_commit()

    monkeypatch.setattr(orchestrator, "AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(orchestrator, "OpenRouterBlueprintProvider", SuccessThenCommitFails)
    monkeypatch.setattr(db, "commit", flaky_commit)

    with pytest.raises(Exception, match="value too long"):
        await orchestrator.run_blueprint_generation({}, blueprint.id)

    refreshed = await db.get(type(blueprint), blueprint.id)
    assert refreshed is not None
    assert refreshed.status == ScrapingBlueprintStatus.FAILED
    assert "saving results" in (refreshed.generation_error or "")
    assert refreshed.provider_execution_metadata["failure"]["stage"] == "persist"
    assert refreshed.provider_execution_metadata["failure"]["category"] == "database"


@pytest.mark.asyncio
async def test_structuring_failure_preserves_research_without_partial_structured_data(
    db, auth, monkeypatch
):
    _, blueprint = await mission_and_blueprint(db, auth, ScrapingBlueprintStatus.QUEUED)
    await db.commit()

    class StructuringFailureProvider:
        def __init__(self, _settings) -> None:
            pass

        async def generate_blueprint(self, **_kwargs):
            raise BlueprintProviderError(
                "structured_output",
                "OpenRouter returned invalid structured output.",
                stage="structuring",
                model="openai/gpt-4.1-mini",
                retryable=False,
                provider_message="country_dossier.country_iso3: Field required",
                research_text="Preserved Stage 1 research",
                citations=[
                    {
                        "url": "https://registry.example/a",
                        "title": "Registry",
                        "source_type": "openrouter_annotation",
                    }
                ],
            )

    monkeypatch.setattr(orchestrator, "AsyncSessionLocal", lambda: db)
    monkeypatch.setattr(orchestrator, "OpenRouterBlueprintProvider", StructuringFailureProvider)

    await orchestrator.run_blueprint_generation({}, blueprint.id)

    refreshed = await db.get(type(blueprint), blueprint.id)
    assert refreshed is not None
    assert refreshed.status == ScrapingBlueprintStatus.FAILED
    assert refreshed.human_readable_blueprint == "Preserved Stage 1 research"
    assert refreshed.citations == [
        {
            "url": "https://registry.example/a",
            "title": "Registry",
            "source_type": "openrouter_annotation",
        }
    ]
    assert refreshed.structured_blueprint is None
    assert refreshed.provider_execution_metadata["failure"]["stage"] == "structuring"
    assert "Preserved Stage 1 research" not in str(refreshed.provider_execution_metadata)
