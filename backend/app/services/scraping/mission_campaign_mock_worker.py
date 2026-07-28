"""Mission-campaign worker (historical filename; active v2 path included).

Schema-v2 campaigns run Step 3B then real Phase 4 discovery orchestration.
Legacy / non-v2 executions keep the deterministic mock STAGES loop only.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import selectinload

from app.db.models import ScrapingBlueprintStatus, ScrapingExecution, ScrapingExecutionStatus
from app.db.session import AsyncSessionLocal
from app.schemas.scraping_clarification import ClarificationStatus
from app.schemas.scraping_execution_plan import (
    SUPPORTED_EXECUTION_PLAN_SCHEMA_VERSIONS,
    parse_frozen_execution_plan,
    supports_deterministic_query_generation,
)
from app.services.scraping.blueprint_execution_plan_service import sha256_hex
from app.services.scraping.clarification_orchestrator import clarification_orchestrator
from app.services.scraping.execution_service import execution_service
from app.services.scraping.query_generation_service import query_generation_service
from app.services.scraping.source_discovery_execution_service import (
    source_discovery_execution_service,
)

STAGES = (
    ("discovery", "Discovery checkpoint", "Gemini Deep Research"),
    ("verification", "Verification checkpoint", "GPT-5.5 Deep Research"),
    ("citation_checking", "Citation-checking checkpoint", "Perplexity"),
    ("database_cleaning", "Database-cleaning checkpoint", "Claude"),
)


async def run_mission_campaign_mock(ctx: dict, execution_id: str) -> None:
    """Persist lifecycle checkpoints only; never make calls or generate facilities."""
    del ctx
    async with AsyncSessionLocal() as db:
        execution = await _load_execution(db, execution_id)
        if (
            execution is None
            or execution.execution_type != "mission_campaign"
            or execution.mode != "mock"
            or execution.execution_origin != "mission_campaign_mock"
        ):
            return
        if execution.status == ScrapingExecutionStatus.CANCEL_REQUESTED:
            await _finish_cancelled(db, execution)
            return
        if execution.status == ScrapingExecutionStatus.PAUSE_REQUESTED:
            # Acknowledge a pause request left pending after worker stop/restart.
            if _cancel_supersedes_pause(execution):
                await _finish_cancelled(db, execution)
            else:
                execution.status = ScrapingExecutionStatus.PAUSED
                execution.paused_at = execution.paused_at or datetime.now(UTC)
                execution.completed_at = None
                await execution_service.emit_event(
                    db,
                    execution.id,
                    "execution_paused",
                    "Mission campaign paused at a safe checkpoint.",
                )
                await db.commit()
            return
        if execution.status in {
            ScrapingExecutionStatus.CANCELLED,
            ScrapingExecutionStatus.COMPLETED,
            ScrapingExecutionStatus.FAILED,
        }:
            return
        # Human-review pause must not continue into later stages on duplicate delivery.
        if (
            execution.status == ScrapingExecutionStatus.PAUSED
            and execution.clarification_status
            == ClarificationStatus.REQUIRES_HUMAN_REVIEW.value
        ):
            return
        if execution.status == ScrapingExecutionStatus.PAUSED:
            # Non-review pauses resume only via the resume API (re-queue to QUEUED).
            return
        if execution.status != ScrapingExecutionStatus.QUEUED:
            return
        if not await _validate_campaign_provenance(db, execution):
            return
        claimed = await db.execute(
            update(ScrapingExecution)
            .where(
                ScrapingExecution.id == execution.id,
                ScrapingExecution.status == ScrapingExecutionStatus.QUEUED,
            )
            .values(
                status=ScrapingExecutionStatus.RUNNING,
                started_at=execution.started_at or datetime.now(UTC),
                heartbeat_at=datetime.now(UTC),
            )
        )
        if claimed.rowcount != 1:
            await db.rollback()
            return
        await db.refresh(execution)
        if execution.clarification_status == ClarificationStatus.REQUIRES_HUMAN_REVIEW.value:
            execution.status = ScrapingExecutionStatus.PAUSED
            execution.paused_at = execution.paused_at or datetime.now(UTC)
            await db.commit()
            return
        snapshot_version = (
            execution.blueprint_version_snapshot
            if execution.blueprint_version_snapshot is not None
            else (execution.blueprint.version if execution.blueprint is not None else None)
        )
        execution.country_profile_json = {
            "phase": "mission_campaign",
            "provenance": "local_deterministic_mock",
            "blueprint_id": execution.blueprint_id,
            "blueprint_version": snapshot_version,
            "blueprint_version_snapshot": execution.blueprint_version_snapshot,
            "execution_plan_schema_version": execution.execution_plan_schema_version,
            "execution_plan_hash": execution.execution_plan_hash,
            "external_calls": False,
            "facility_generation": False,
        }
        await execution_service.emit_event(
            db,
            execution.id,
            "mission_campaign_started",
            "Deterministic mock mission campaign started.",
            metadata={"mode": "mock", "provenance": "local_deterministic_mock"},
        )
        await db.commit()

        await db.refresh(execution)
        if await _pause_or_cancel(db, execution):
            return

        # Step 2 clarification phase for campaigns that own a frozen plan.
        if execution.frozen_execution_plan_json is not None:
            try:
                phase = await clarification_orchestrator.run(
                    db, execution, check_interrupt=_pause_or_cancel
                )
            except Exception:
                await db.refresh(execution)
                if execution.clarification_status != ClarificationStatus.FAILED.value:
                    execution.clarification_status = ClarificationStatus.FAILED.value
                    execution.status = ScrapingExecutionStatus.FAILED
                    execution.completed_at = datetime.now(UTC)
                    execution.error_message = "Clarification phase failed."
                    await execution_service.emit_event(
                        db,
                        execution.id,
                        "clarification_failed",
                        "Typed clarification phase failed.",
                        metadata={"error_code": "provider"},
                    )
                    await db.commit()
                return
            await db.refresh(execution)
            if not phase.continue_campaign:
                return
            if await _pause_or_cancel(db, execution):
                return

        # Step 3B: persistent deterministic query jobs for v2 plan-backed campaigns only.
        # Historical v1/null executions keep mock-stage compatibility and never fall back
        # into the legacy LLM query planner from this worker.
        if supports_deterministic_query_generation(execution.execution_plan_schema_version):
            if await _pause_or_cancel(db, execution):
                return
            generation = await query_generation_service.generate_for_execution(
                db, execution, discovery_round=1, check_interrupt=_pause_or_cancel
            )
            if generation.status == "interrupted":
                # Pause/cancel already acknowledged inside check_interrupt.
                return
            if generation.status != "ok":
                execution.status = ScrapingExecutionStatus.FAILED
                execution.completed_at = datetime.now(UTC)
                execution.error_message = "Deterministic query generation failed."
                execution.current_stage = "query_generation"
                execution.current_stage_label = "Query generation"
                await execution_service.emit_event(
                    db,
                    execution.id,
                    "query_generation_failed",
                    "Deterministic query generation failed.",
                    metadata={
                        "status": generation.status,
                        "blocked_code": generation.blocked_code,
                        "error_code": generation.error_code,
                        "discovery_round": generation.discovery_round,
                        "generated_count": generation.generated_count,
                        "existing_count": generation.existing_count,
                        "total_count": generation.total_count,
                        "expected_raw_count": generation.expected_raw_count,
                    },
                )
                await db.commit()
                return
            execution.current_stage = "query_generation"
            execution.current_stage_label = "Query generation"
            execution.latest_message = "Deterministic query jobs prepared."
            await execution_service.emit_event(
                db,
                execution.id,
                "query_generation_completed",
                "Deterministic query jobs prepared.",
                metadata={
                    "discovery_round": generation.discovery_round,
                    "generated_count": generation.generated_count,
                    "existing_count": generation.existing_count,
                    "total_count": generation.total_count,
                    "expected_raw_count": generation.expected_raw_count,
                },
            )
            await db.commit()
            if await _pause_or_cancel(db, execution):
                return

            # Schema-v2: real Phase 4 discovery — never mock STAGES / later phases.
            await _run_phase4_web_discovery(execution)
            return

        for index, (stage, label, provider) in enumerate(STAGES, start=1):
            if await _pause_or_cancel(db, execution):
                return
            execution.current_stage = stage
            execution.current_stage_label = label
            execution.current_provider = provider
            execution.current_model = None
            execution.progress_percent = index * 20
            execution.latest_message = f"{label} completed."
            await execution_service.emit_event(
                db,
                execution.id,
                "stage_completed",
                f"{label} completed with deterministic mock behavior.",
                metadata={
                    "stage": stage,
                    "provider": provider,
                    "provenance": "local_deterministic_mock",
                    "mock": True,
                    "external_calls": False,
                    "facility_generation": False,
                },
            )
            await db.commit()
        execution.heartbeat_at = datetime.now(UTC)
        if await _pause_or_cancel(db, execution):
            return
        execution.status = ScrapingExecutionStatus.COMPLETED
        execution.completed_at = datetime.now(UTC)
        execution.progress_percent = 100
        execution.latest_message = "Campaign completed."
        await execution_service.emit_event(
            db,
            execution.id,
            "mission_campaign_completed",
            "Deterministic mock mission campaign completed.",
            metadata={
                "mode": "mock",
                "provenance": "local_deterministic_mock",
                "external_calls": False,
                "facility_generation": False,
            },
        )
        await db.commit()


async def _run_phase4_web_discovery(execution: ScrapingExecution) -> None:
    """Invoke the dedicated Phase 4 orchestration service for schema-v2 campaigns.

    Uses the same execution ID. Orchestration owns pause/cancel/completion events.
    Does not call SourceDiscoveryService.discover, the LLM planner, or mock STAGES.
    """
    await source_discovery_execution_service.run_discovery_work_slice(
        execution.organization_id,
        execution.id,
    )


async def _validate_campaign_provenance(db, execution: ScrapingExecution) -> bool:
    """Validate campaign-owned frozen data, with legacy fallback for pre-026 rows."""
    has_step1 = (
        execution.frozen_execution_plan_json is not None
        or execution.blueprint_snapshot_json is not None
        or execution.execution_plan_hash is not None
    )
    if has_step1:
        return await _validate_step1_provenance(db, execution)
    return await _validate_legacy_provenance(db, execution)


async def _validate_step1_provenance(db, execution: ScrapingExecution) -> bool:
    if (
        execution.blueprint_snapshot_json is None
        or execution.frozen_execution_plan_json is None
        or not execution.execution_plan_schema_version
        or not execution.execution_plan_hash
        or execution.blueprint_version_snapshot is None
    ):
        await _fail_provenance(
            db,
            execution,
            "Campaign frozen execution plan provenance is incomplete.",
        )
        return False
    # Worker-readable provenance only. Do not treat membership here as Step-3
    # deterministic-query-generation capability — that requires schema "2" via
    # supports_deterministic_query_generation() independently of this set.
    if execution.execution_plan_schema_version not in SUPPORTED_EXECUTION_PLAN_SCHEMA_VERSIONS:
        await _fail_provenance(
            db,
            execution,
            "Campaign frozen execution plan schema version is unsupported.",
        )
        return False
    try:
        plan = parse_frozen_execution_plan(execution.frozen_execution_plan_json)
    except Exception:
        await _fail_provenance(
            db,
            execution,
            "Campaign frozen execution plan is invalid.",
        )
        return False
    if plan.blueprint_id != execution.blueprint_id:
        await _fail_provenance(
            db,
            execution,
            "Campaign frozen execution plan blueprint id does not match provenance.",
        )
        return False
    if plan.blueprint_version != execution.blueprint_version_snapshot:
        await _fail_provenance(
            db,
            execution,
            "Campaign frozen execution plan blueprint version does not match provenance.",
        )
        return False
    recomputed = sha256_hex(plan.model_dump(mode="json"))
    if recomputed != execution.execution_plan_hash:
        await _fail_provenance(
            db,
            execution,
            "Campaign frozen execution plan hash does not match stored provenance.",
        )
        return False
    return True


async def _validate_legacy_provenance(db, execution: ScrapingExecution) -> bool:
    blueprint = execution.blueprint
    if (
        blueprint is None
        or blueprint.status != ScrapingBlueprintStatus.APPROVED
        or execution.blueprint_version_snapshot != blueprint.version
    ):
        await _fail_provenance(
            db,
            execution,
            "Campaign blueprint provenance no longer matches its snapshot.",
        )
        return False
    return True


async def _fail_provenance(db, execution: ScrapingExecution, message: str) -> None:
    execution.status = ScrapingExecutionStatus.FAILED
    execution.completed_at = datetime.now(UTC)
    execution.error_message = message
    await execution_service.emit_event(
        db,
        execution.id,
        "mission_campaign_failed",
        "Mission campaign failed provenance validation before execution.",
        metadata={"provenance": "local_deterministic_mock", "external_calls": False},
    )
    await db.commit()


async def _load_execution(db, execution_id: str) -> ScrapingExecution | None:
    result = await db.execute(
        select(ScrapingExecution)
        .where(ScrapingExecution.id == execution_id)
        .options(selectinload(ScrapingExecution.blueprint))
    )
    return result.scalar_one_or_none()


def _cancel_supersedes_pause(execution: ScrapingExecution) -> bool:
    """Cancel wins when both request timestamps exist (or status is already cancel_requested)."""
    if execution.status == ScrapingExecutionStatus.CANCEL_REQUESTED:
        return True
    if execution.cancel_requested_at is None:
        return False
    if execution.pause_requested_at is None:
        return True
    return execution.cancel_requested_at >= execution.pause_requested_at


async def _pause_or_cancel(db, execution: ScrapingExecution) -> bool:
    await db.refresh(execution)
    if execution.status == ScrapingExecutionStatus.CANCEL_REQUESTED or (
        execution.status == ScrapingExecutionStatus.PAUSE_REQUESTED
        and _cancel_supersedes_pause(execution)
    ):
        await _finish_cancelled(db, execution)
        return True
    if execution.status != ScrapingExecutionStatus.PAUSE_REQUESTED:
        return False
    # Paused is non-terminal: never set completed_at.
    execution.status = ScrapingExecutionStatus.PAUSED
    execution.paused_at = datetime.now(UTC)
    execution.completed_at = None
    await execution_service.emit_event(
        db, execution.id, "execution_paused", "Mission campaign paused at a safe checkpoint."
    )
    await db.commit()
    return True


async def _finish_cancelled(db, execution: ScrapingExecution) -> None:
    await execution_service._cancel_pending_children(db, execution.id)
    execution.status = ScrapingExecutionStatus.CANCELLED
    execution.completed_at = datetime.now(UTC)
    await execution_service.emit_event(
        db, execution.id, "execution_cancelled", "Mission campaign cancelled."
    )
    await db.commit()
