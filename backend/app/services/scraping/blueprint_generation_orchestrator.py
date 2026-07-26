"""ARQ task for asynchronous OpenRouter country-blueprint generation."""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import ScrapingBlueprint, ScrapingBlueprintStatus
from app.db.session import AsyncSessionLocal
from app.schemas.api import CountryMaximumCoverageStructuredBlueprint
from app.services.scraping.blueprint_provider import (
    BlueprintProviderError,
    OpenRouterBlueprintProvider,
)
from app.services.scraping.blueprint_state_service import blueprint_state_service

logger = get_logger(__name__)


async def run_blueprint_generation(_ctx: dict, blueprint_id: str) -> None:
    async with AsyncSessionLocal() as db:
        blueprint = (
            await db.execute(
                select(ScrapingBlueprint)
                .where(ScrapingBlueprint.id == blueprint_id)
                .options(selectinload(ScrapingBlueprint.mission))
            )
        ).scalar_one_or_none()
        if blueprint is None or blueprint.status != ScrapingBlueprintStatus.QUEUED:
            return
        blueprint_state_service.require_transition(blueprint.status, ScrapingBlueprintStatus.RUNNING)
        blueprint_state_service.transition(blueprint, ScrapingBlueprintStatus.RUNNING)
        blueprint.started_at = datetime.now(UTC)
        await db.commit()
        try:
            result = await OpenRouterBlueprintProvider(get_settings()).generate_blueprint(
                mission=blueprint.mission,
                rendered_prompt=blueprint.rendered_prompt_snapshot or "",
                structured_output_schema=CountryMaximumCoverageStructuredBlueprint,
            )
            blueprint.structured_blueprint = result.structured_blueprint.model_dump(mode="json")
            blueprint.human_readable_blueprint = result.human_readable_blueprint
            blueprint.citations = result.citations
            blueprint.provider = result.provider
            blueprint.provider_model_id = result.model_id
            blueprint.provider_operation_id = result.operation_id
            blueprint.provider_execution_metadata = result.execution_metadata
            blueprint.completed_at = result.completed_at or datetime.now(UTC)
            blueprint_state_service.require_transition(
                ScrapingBlueprintStatus.RUNNING, ScrapingBlueprintStatus.READY_FOR_REVIEW
            )
            blueprint_state_service.transition(
                blueprint, ScrapingBlueprintStatus.READY_FOR_REVIEW
            )
        except asyncio.CancelledError:
            # There is no provider-owned operation to resume or poll, so a
            # cancelled job must leave a terminal state rather than RUNNING.
            blueprint_state_service.transition(blueprint, ScrapingBlueprintStatus.FAILED)
            blueprint.failed_at = datetime.now(UTC)
            blueprint.completed_at = blueprint.failed_at
            blueprint.generation_error = "Blueprint generation was cancelled. Retry the request."
            await db.commit()
            raise
        except BlueprintProviderError as exc:
            diagnostics = {"blueprint_id": blueprint_id, **exc.safe_diagnostics()}
            logger.warning("blueprint_generation_failed", **diagnostics)
            blueprint_state_service.transition(blueprint, ScrapingBlueprintStatus.FAILED)
            blueprint.failed_at = datetime.now(UTC)
            blueprint.completed_at = blueprint.failed_at
            blueprint.generation_error = (
                f"Blueprint generation failed during {exc.stage or 'provider processing'}. "
                "Review configuration and retry."
            )
            # Preserve Stage 1 research when Stage 2 fails; never store invalid structured data.
            if exc.research_text:
                blueprint.human_readable_blueprint = exc.research_text
            if exc.citations is not None:
                blueprint.citations = exc.citations
            blueprint.structured_blueprint = None
            blueprint.provider_execution_metadata = {"failure": diagnostics}
        except (RuntimeError, ValueError):
            logger.warning(
                "blueprint_generation_failed",
                blueprint_id=blueprint_id,
                stage="unknown",
                model=None,
                http_status=None,
                provider_error_code=None,
                provider_error_type=None,
                provider_message=None,
                retryable=False,
            )
            blueprint_state_service.transition(blueprint, ScrapingBlueprintStatus.FAILED)
            blueprint.failed_at = datetime.now(UTC)
            blueprint.completed_at = blueprint.failed_at
            blueprint.generation_error = "Blueprint generation failed. Review configuration and retry."
            blueprint.provider_execution_metadata = {
                "failure": {
                    "blueprint_id": blueprint_id,
                    "stage": "unknown",
                    "model": None,
                    "http_status": None,
                    "provider_error_code": None,
                    "provider_error_type": None,
                    "provider_message": None,
                    "retryable": False,
                    "category": "unknown",
                }
            }
        try:
            await db.commit()
        except SQLAlchemyError as commit_exc:
            await db.rollback()
            logger.exception(
                "blueprint_generation_commit_failed",
                blueprint_id=blueprint_id,
                error_type=type(commit_exc).__name__,
            )
            await _mark_failed_after_commit_error(db, blueprint_id, commit_exc)
            raise


async def _mark_failed_after_commit_error(
    db: AsyncSession, blueprint_id: str, commit_exc: Exception
) -> None:
    """Best-effort terminalization after a failed persist; never hides the original error."""
    try:
        blueprint = await db.get(ScrapingBlueprint, blueprint_id)
        if blueprint is None or blueprint.status != ScrapingBlueprintStatus.RUNNING:
            return
        blueprint_state_service.transition(blueprint, ScrapingBlueprintStatus.FAILED)
        blueprint.failed_at = datetime.now(UTC)
        blueprint.completed_at = blueprint.failed_at
        blueprint.structured_blueprint = None
        blueprint.generation_error = (
            "Blueprint generation failed while saving results. "
            "Review database configuration and retry."
        )
        blueprint.provider_execution_metadata = {
            "failure": {
                "blueprint_id": blueprint_id,
                "stage": "persist",
                "model": None,
                "http_status": None,
                "provider_error_code": type(commit_exc).__name__,
                "provider_error_type": "database_commit_error",
                "provider_message": "Database commit failed while saving the generated blueprint.",
                "retryable": False,
                "category": "database",
            }
        }
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(
            "blueprint_generation_failed_terminalization_failed",
            blueprint_id=blueprint_id,
        )


async def recover_blueprint_generations(_ctx: dict) -> None:
    """Queued work is recovered by normal ARQ retry/enqueue behavior in Phase 1B."""
    return
