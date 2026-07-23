"""Country-aware source-discovery execution campaign persistence and orchestration API."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)
from app.db.models import (
    RehabilitationFacility,
    RehabilitationFacilityContact,
    ScrapingCoverageCell,
    ScrapingCoverageStatus,
    ScrapingEvent,
    ScrapingExecution,
    ScrapingExecutionAgent,
    ScrapingExecutionAgentStatus,
    ScrapingExecutionStatus,
    ScrapingRun,
    ScrapingRunStatus,
    ScrapingTask,
    ScrapingTaskStatus,
)
from app.schemas.api import (
    ScrapingCoverageCellResponse,
    ScrapingEventResponse,
    ScrapingExecutionAgentResponse,
    ScrapingExecutionCreate,
    ScrapingExecutionDetail,
    ScrapingExecutionSummary,
    ScrapingFacilitySummary,
    ScrapingTaskResponse,
)
from app.services.scraping.execution_outcome import execution_outcome_label
from app.services.scraping.scale_profile import MODE_FULL_CENSUS, SUPPORTED_EXECUTION_MODES

ACTIVE_EXECUTION_STATUSES = {
    ScrapingExecutionStatus.QUEUED,
    ScrapingExecutionStatus.RUNNING,
    ScrapingExecutionStatus.CANCEL_REQUESTED,
}
TERMINAL_EXECUTION_STATUSES = {
    ScrapingExecutionStatus.COMPLETED,
    ScrapingExecutionStatus.FAILED,
    ScrapingExecutionStatus.CANCELLED,
}
DELETABLE_EXECUTION_STATUSES = TERMINAL_EXECUTION_STATUSES
SUPPORTED_EXECUTION_TYPES = {
    "initial_full_country",
    "gap_focused",
    "failed_source_retry",
    "verification_only",
    "scheduled_refresh",
}
STARTABLE_EXECUTION_TYPES = {"initial_full_country"}
SUPPORTED_MODES = SUPPORTED_EXECUTION_MODES
ACTIVE_EXECUTION_MESSAGE = "An active source discovery execution already exists for this AI team plan."


class ScrapingExecutionService:
    async def create_execution(
        self,
        db: AsyncSession,
        auth: AuthContext,
        team_plan_id: str,
        data: ScrapingExecutionCreate,
    ) -> ScrapingExecutionSummary:
        if data.execution_type not in SUPPORTED_EXECUTION_TYPES:
            raise ValidationError("Unsupported execution type.")
        if data.execution_type not in STARTABLE_EXECUTION_TYPES:
            raise ValidationError("This execution type is not startable in this phase.")
        if data.mode not in SUPPORTED_MODES:
            raise ValidationError(
                "Unsupported scrape mode. Use 'real' for real source discovery (standard) or 'full_census'."
            )

        team_plan = await self._team_plan_row(db, auth, team_plan_id)
        if team_plan.status != ScrapingRunStatus.PLANNED:
            raise ConflictError("Only a planned AI team plan can start a source discovery execution.")
        if not team_plan.agents:
            raise ConflictError("This AI team plan has no planned agents.")
        if not team_plan.mission.country_code or not team_plan.mission.country_name:
            raise ConflictError("Set a mission country before starting a source discovery execution.")
        if team_plan.blueprint_id != team_plan.mission.active_blueprint_id:
            raise ConflictError("The AI team plan no longer matches the mission's active blueprint.")

        existing = await self._active_execution_for_team_plan(db, auth, team_plan.id)
        if existing is not None:
            self._raise_active_execution_conflict(existing)

        execution = ScrapingExecution(
            organization_id=auth.org_id,
            mission_id=team_plan.mission_id,
            blueprint_id=team_plan.blueprint_id,
            team_plan_id=team_plan.id,
            execution_type=data.execution_type,
            mode=data.mode,
            status=ScrapingExecutionStatus.QUEUED,
            country_code=team_plan.mission.country_code,
            country_name=team_plan.mission.country_name,
        )
        db.add(execution)
        try:
            await db.flush()
            for agent in sorted(team_plan.agents, key=lambda item: item.sequence):
                db.add(
                    ScrapingExecutionAgent(
                        execution_id=execution.id,
                        team_agent_id=agent.id,
                        status=ScrapingExecutionAgentStatus.WAITING,
                    )
                )
            await db.flush()
            mode_label = "Full census" if data.mode == MODE_FULL_CENSUS else "Standard"
            await self.emit_event(
                db,
                execution.id,
                "execution_queued",
                f"{mode_label} source discovery campaign queued.",
                metadata={"mode": data.mode or "real"},
            )
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            existing = await self._active_execution_for_team_plan(db, auth, team_plan.id)
            if existing is not None:
                self._raise_active_execution_conflict(existing)
            raise ConflictError(ACTIVE_EXECUTION_MESSAGE) from exc

        await self.enqueue_execution(execution.id)
        return self._summary(execution)

    async def list_executions(
        self, db: AsyncSession, auth: AuthContext, team_plan_id: str
    ) -> list[ScrapingExecutionSummary]:
        await self._team_plan_row(db, auth, team_plan_id)
        result = await db.execute(
            select(ScrapingExecution)
            .where(
                ScrapingExecution.team_plan_id == team_plan_id,
                ScrapingExecution.organization_id == auth.org_id,
            )
            .order_by(ScrapingExecution.created_at.desc())
        )
        return [self._summary(execution) for execution in result.scalars().all()]

    async def get_detail(
        self, db: AsyncSession, auth: AuthContext, execution_id: str
    ) -> ScrapingExecutionDetail:
        execution = await self._execution_row(db, auth, execution_id)
        agents_result = await db.execute(
            select(ScrapingExecutionAgent)
            .where(ScrapingExecutionAgent.execution_id == execution.id)
            .options(
                selectinload(ScrapingExecutionAgent.team_agent),
                selectinload(ScrapingExecutionAgent.tasks),
            )
            .order_by(ScrapingExecutionAgent.created_at)
        )
        agents = agents_result.scalars().all()
        return ScrapingExecutionDetail(
            execution=self._summary(execution),
            country_profile=execution.country_profile_json,
            agents=[self._agent_response(agent) for agent in agents],
            task_summary_counts=await self._count_by_status(db, ScrapingTask, execution.id),
            coverage_summary_counts=await self._count_by_status(
                db, ScrapingCoverageCell, execution.id
            ),
            recent_tasks=await self.list_tasks(db, auth, execution.id, limit=20),
            recent_events=await self.list_events(db, auth, execution.id, limit=80),
            can_cancel=execution.status in {
                ScrapingExecutionStatus.QUEUED,
                ScrapingExecutionStatus.RUNNING,
            },
            can_delete=execution.status in DELETABLE_EXECUTION_STATUSES,
            mock=False,
        )

    async def list_tasks(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        status: str | None = None,
        execution_agent_id: str | None = None,
        task_type: str | None = None,
        coverage_cell_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScrapingTaskResponse]:
        await self._execution_row(db, auth, execution_id)
        query = (
            select(ScrapingTask)
            .where(ScrapingTask.execution_id == execution_id)
            .options(
                selectinload(ScrapingTask.execution_agent).selectinload(
                    ScrapingExecutionAgent.team_agent
                ),
                selectinload(ScrapingTask.coverage_cell),
            )
            .order_by(ScrapingTask.created_at.desc())
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 500))
        )
        if status:
            query = query.where(ScrapingTask.status == ScrapingTaskStatus(status))
        if execution_agent_id:
            query = query.where(ScrapingTask.execution_agent_id == execution_agent_id)
        if task_type:
            query = query.where(ScrapingTask.task_type == task_type)
        if coverage_cell_id:
            query = query.where(ScrapingTask.coverage_cell_id == coverage_cell_id)
        result = await db.execute(query)
        return [self._task_response(task) for task in result.scalars().all()]

    async def list_coverage(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        status: str | None = None,
        region: str | None = None,
        language: str | None = None,
        source_category: str | None = None,
        execution_agent_id: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[ScrapingCoverageCellResponse]:
        await self._execution_row(db, auth, execution_id)
        query = (
            select(ScrapingCoverageCell)
            .where(ScrapingCoverageCell.execution_id == execution_id)
            .options(
                selectinload(ScrapingCoverageCell.assigned_execution_agent).selectinload(
                    ScrapingExecutionAgent.team_agent
                )
            )
            .order_by(
                ScrapingCoverageCell.region_name,
                ScrapingCoverageCell.language_name,
                ScrapingCoverageCell.source_category,
            )
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 1000))
        )
        if status:
            query = query.where(ScrapingCoverageCell.status == ScrapingCoverageStatus(status))
        if region:
            query = query.where(ScrapingCoverageCell.region_name == region)
        if language:
            query = query.where(ScrapingCoverageCell.language_name == language)
        if source_category:
            query = query.where(ScrapingCoverageCell.source_category == source_category)
        if execution_agent_id:
            query = query.where(
                ScrapingCoverageCell.assigned_execution_agent_id == execution_agent_id
            )
        result = await db.execute(query)
        return [self._coverage_response(cell) for cell in result.scalars().all()]

    async def list_events(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        after_sequence: int | None = None,
        limit: int = 200,
        execution_agent_id: str | None = None,
        event_type: str | None = None,
    ) -> list[ScrapingEventResponse]:
        await self._execution_row(db, auth, execution_id)
        query = (
            select(ScrapingEvent)
            .where(ScrapingEvent.execution_id == execution_id)
            .order_by(ScrapingEvent.sequence_number)
            .limit(min(max(limit, 1), 1000))
        )
        if after_sequence is not None:
            query = query.where(ScrapingEvent.sequence_number > after_sequence)
        if execution_agent_id:
            query = query.where(ScrapingEvent.execution_agent_id == execution_agent_id)
        if event_type:
            query = query.where(ScrapingEvent.event_type == event_type)
        result = await db.execute(query)
        return [self._event_response(event) for event in result.scalars().all()]

    async def list_facilities(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ScrapingFacilitySummary]:
        await self._execution_row(db, auth, execution_id)
        result = await db.execute(
            select(RehabilitationFacility)
            .where(
                RehabilitationFacility.execution_id == execution_id,
                RehabilitationFacility.organization_id == auth.org_id,
            )
            .options(
                selectinload(RehabilitationFacility.contacts),
                selectinload(RehabilitationFacility.source_links),
            )
            .order_by(RehabilitationFacility.stable_key)
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 500))
        )
        return [self._facility_response(facility) for facility in result.scalars().all()]

    async def cancel_execution(
        self, db: AsyncSession, auth: AuthContext, execution_id: str
    ) -> ScrapingExecutionSummary:
        execution = await self._execution_row(db, auth, execution_id)
        now = datetime.now(UTC)
        if execution.status == ScrapingExecutionStatus.QUEUED:
            execution.status = ScrapingExecutionStatus.CANCELLED
            execution.cancel_requested_at = now
            execution.completed_at = now
            await self._cancel_pending_children(db, execution.id)
            await self.emit_event(
                db,
                execution.id,
                "execution_cancelled",
                "Queued source discovery execution was cancelled before work began.",
            )
        elif execution.status == ScrapingExecutionStatus.RUNNING:
            execution.status = ScrapingExecutionStatus.CANCEL_REQUESTED
            execution.cancel_requested_at = now
            await self.emit_event(
                db,
                execution.id,
                "execution_cancel_requested",
                "Source discovery execution cancellation requested.",
            )
        elif execution.status == ScrapingExecutionStatus.CANCEL_REQUESTED:
            pass
        else:
            raise ConflictError("This execution is already terminal.")
        await db.commit()
        return self._summary(execution)

    async def delete_execution(self, db: AsyncSession, auth: AuthContext, execution_id: str) -> None:
        execution = await self._execution_row(db, auth, execution_id)
        if execution.status not in DELETABLE_EXECUTION_STATUSES:
            raise ConflictError("Active source discovery executions cannot be deleted.")
        await db.delete(execution)
        await db.commit()

    async def emit_event(
        self,
        db: AsyncSession,
        execution_id: str,
        event_type: str,
        message: str,
        *,
        execution_agent_id: str | None = None,
        task_id: str | None = None,
        coverage_cell_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ScrapingEvent:
        result = await db.execute(
            update(ScrapingExecution)
            .where(ScrapingExecution.id == execution_id)
            .values(last_event_sequence=ScrapingExecution.last_event_sequence + 1)
            .returning(ScrapingExecution.last_event_sequence)
        )
        sequence_number = result.scalar_one()
        event = ScrapingEvent(
            execution_id=execution_id,
            execution_agent_id=execution_agent_id,
            task_id=task_id,
            coverage_cell_id=coverage_cell_id,
            sequence_number=sequence_number,
            event_type=event_type,
            message=message,
            metadata_json=metadata or {},
        )
        db.add(event)
        await db.flush()
        await self._publish_event(event)
        return event

    async def enqueue_execution(self, execution_id: str) -> None:
        settings = get_settings()
        inline = (
            settings.scraping_inline_execution
            if settings.scraping_inline_execution is not None
            else settings.environment == "development"
        )
        queued_on_redis = False
        if not inline:
            try:
                redis = await create_pool(_redis_settings())
                await redis.enqueue_job(
                    "run_scraping_execution",
                    execution_id,
                    _job_id=f"scraping-execution:{execution_id}",
                )
                await redis.close()
                queued_on_redis = True
            except Exception:
                logger.warning(
                    "scraping_enqueue_redis_failed execution_id=%s; falling back to inline",
                    execution_id,
                    exc_info=True,
                )
        if inline or not queued_on_redis:
            asyncio.create_task(_run_execution_inline(execution_id))

    async def _publish_event(self, event: ScrapingEvent) -> None:
        try:
            redis = await create_pool(_redis_settings())
            await redis.publish(
                f"scraping:executions:{event.execution_id}:events",
                json.dumps(self._event_response(event).model_dump(mode="json")),
            )
            await redis.close()
        except Exception:
            return

    async def _team_plan_row(
        self, db: AsyncSession, auth: AuthContext, team_plan_id: str
    ) -> ScrapingRun:
        result = await db.execute(
            select(ScrapingRun)
            .where(ScrapingRun.id == team_plan_id, ScrapingRun.organization_id == auth.org_id)
            .options(
                selectinload(ScrapingRun.mission),
                selectinload(ScrapingRun.blueprint),
                selectinload(ScrapingRun.agents),
            )
        )
        team_plan = result.scalar_one_or_none()
        if team_plan is None:
            raise NotFoundError("ScrapingRun", team_plan_id)
        return team_plan

    async def _execution_row(
        self, db: AsyncSession, auth: AuthContext, execution_id: str
    ) -> ScrapingExecution:
        result = await db.execute(
            select(ScrapingExecution).where(
                ScrapingExecution.id == execution_id,
                ScrapingExecution.organization_id == auth.org_id,
            )
        )
        execution = result.scalar_one_or_none()
        if execution is None:
            raise NotFoundError("ScrapingExecution", execution_id)
        return execution

    async def _active_execution_for_team_plan(
        self, db: AsyncSession, auth: AuthContext, team_plan_id: str
    ) -> ScrapingExecution | None:
        result = await db.execute(
            select(ScrapingExecution).where(
                ScrapingExecution.team_plan_id == team_plan_id,
                ScrapingExecution.organization_id == auth.org_id,
                ScrapingExecution.status.in_(list(ACTIVE_EXECUTION_STATUSES)),
            )
        )
        return result.scalar_one_or_none()

    async def _count_by_status(
        self,
        db: AsyncSession,
        model: type[ScrapingTask] | type[ScrapingCoverageCell],
        execution_id: str,
    ) -> dict[str, int]:
        result = await db.execute(
            select(model.status, func.count(model.id))
            .where(model.execution_id == execution_id)
            .group_by(model.status)
        )
        return {status.value: count for status, count in result.all()}

    async def _cancel_pending_children(self, db: AsyncSession, execution_id: str) -> None:
        await db.execute(
            update(ScrapingTask)
            .where(
                ScrapingTask.execution_id == execution_id,
                ScrapingTask.status.in_([ScrapingTaskStatus.QUEUED, ScrapingTaskStatus.BLOCKED]),
            )
            .values(status=ScrapingTaskStatus.CANCELLED, completed_at=datetime.now(UTC))
        )
        await db.execute(
            update(ScrapingCoverageCell)
            .where(
                ScrapingCoverageCell.execution_id == execution_id,
                ScrapingCoverageCell.status.in_(
                    [ScrapingCoverageStatus.NOT_STARTED, ScrapingCoverageStatus.QUEUED]
                ),
            )
            .values(status=ScrapingCoverageStatus.CANCELLED, completed_at=datetime.now(UTC))
        )
        await db.execute(
            update(ScrapingExecutionAgent)
            .where(
                ScrapingExecutionAgent.execution_id == execution_id,
                ScrapingExecutionAgent.status.in_(
                    [
                        ScrapingExecutionAgentStatus.WAITING,
                        ScrapingExecutionAgentStatus.QUEUED,
                    ]
                ),
            )
            .values(status=ScrapingExecutionAgentStatus.CANCELLED, completed_at=datetime.now(UTC))
        )

    def _raise_active_execution_conflict(self, execution: ScrapingExecution) -> None:
        raise ConflictError(
            ACTIVE_EXECUTION_MESSAGE,
            details={
                "message": ACTIVE_EXECUTION_MESSAGE,
                "existing_execution_id": execution.id,
                "existing_execution_status": execution.status.value,
            },
        )

    def _summary(self, execution: ScrapingExecution) -> ScrapingExecutionSummary:
        return ScrapingExecutionSummary(
            id=execution.id,
            organization_id=execution.organization_id,
            mission_id=execution.mission_id,
            blueprint_id=execution.blueprint_id,
            team_plan_id=execution.team_plan_id,
            execution_type=execution.execution_type,
            mode=execution.mode,
            status=execution.status.value,
            status_label=execution_outcome_label(execution.status, execution.coverage_debt),
            country_code=execution.country_code,
            country_name=execution.country_name,
            started_at=execution.started_at,
            completed_at=execution.completed_at,
            cancel_requested_at=execution.cancel_requested_at,
            heartbeat_at=execution.heartbeat_at,
            error_message=execution.error_message,
            sources_discovered=execution.sources_discovered,
            documents_found=execution.documents_found,
            records_extracted=execution.records_extracted,
            records_verified=execution.records_verified,
            duplicates_detected=execution.duplicates_detected,
            blocked_sources=execution.blocked_sources,
            coverage_debt=execution.coverage_debt,
            created_at=execution.created_at,
            updated_at=execution.updated_at,
        )

    def _agent_response(self, agent: ScrapingExecutionAgent) -> ScrapingExecutionAgentResponse:
        current_task = next(
            (task for task in agent.tasks if task.id == agent.current_task_id),
            None,
        )
        return ScrapingExecutionAgentResponse(
            id=agent.id,
            execution_id=agent.execution_id,
            team_agent_id=agent.team_agent_id,
            planned_agent_name=agent.team_agent.name,
            planned_agent_role=agent.team_agent.role,
            model_id=agent.team_agent.model_id,
            status=agent.status.value,
            current_task_id=agent.current_task_id,
            current_task_title=current_task.title if current_task else None,
            current_action=agent.current_action,
            started_at=agent.started_at,
            completed_at=agent.completed_at,
            error_message=agent.error_message,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )

    def _coverage_response(self, cell: ScrapingCoverageCell) -> ScrapingCoverageCellResponse:
        assigned_agent = cell.assigned_execution_agent
        return ScrapingCoverageCellResponse(
            id=cell.id,
            execution_id=cell.execution_id,
            region_code=cell.region_code,
            region_name=cell.region_name,
            language_code=cell.language_code,
            language_name=cell.language_name,
            source_category=cell.source_category,
            status=cell.status.value,
            assigned_execution_agent_id=cell.assigned_execution_agent_id,
            assigned_agent_name=assigned_agent.team_agent.name if assigned_agent else None,
            result_count=cell.result_count,
            reason=cell.reason,
            metadata_json=cell.metadata_json,
            started_at=cell.started_at,
            completed_at=cell.completed_at,
            created_at=cell.created_at,
            updated_at=cell.updated_at,
        )

    def _task_response(self, task: ScrapingTask) -> ScrapingTaskResponse:
        coverage_label = None
        if task.coverage_cell:
            coverage_label = (
                f"{task.coverage_cell.region_name} x {task.coverage_cell.language_name} x "
                f"{task.coverage_cell.source_category}"
            )
        return ScrapingTaskResponse(
            id=task.id,
            execution_id=task.execution_id,
            execution_agent_id=task.execution_agent_id,
            agent_name=task.execution_agent.team_agent.name if task.execution_agent else None,
            coverage_cell_id=task.coverage_cell_id,
            coverage_label=coverage_label,
            parent_task_id=task.parent_task_id,
            task_type=task.task_type,
            title=task.title,
            status=task.status.value,
            priority=task.priority,
            attempt_count=task.attempt_count,
            max_attempts=task.max_attempts,
            current_action=task.current_action,
            input_json=task.input_json,
            output_json=task.output_json,
            dependency_task_ids_json=task.dependency_task_ids_json,
            claimed_at=task.claimed_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
            error_message=task.error_message,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    def _facility_response(self, facility: RehabilitationFacility) -> ScrapingFacilitySummary:
        contact = _primary_contact(facility.contacts)
        return ScrapingFacilitySummary(
            id=facility.id,
            execution_id=facility.execution_id,
            stable_key=facility.stable_key,
            canonical_name=facility.canonical_name,
            country_code=facility.country_code,
            country_name=facility.country_name,
            primary_region=facility.primary_region,
            primary_city=facility.primary_city,
            facility_type=facility.facility_type,
            primary_website=facility.primary_website,
            primary_contact=contact.value if contact else None,
            verification_status=facility.verification_status,
            confidence_score=float(facility.confidence_score),
            human_review_status=facility.human_review_status,
            is_mock=facility.is_mock,
            source_count=len(facility.source_links),
            created_at=facility.created_at,
            updated_at=facility.updated_at,
        )

    def _event_response(self, event: ScrapingEvent) -> ScrapingEventResponse:
        return ScrapingEventResponse(
            id=event.id,
            execution_id=event.execution_id,
            execution_agent_id=event.execution_agent_id,
            task_id=event.task_id,
            coverage_cell_id=event.coverage_cell_id,
            sequence_number=event.sequence_number,
            event_type=event.event_type,
            message=event.message,
            metadata_json=event.metadata_json,
            created_at=event.created_at,
        )


def _redis_settings() -> RedisSettings:
    parsed = urlparse(get_settings().redis_url)
    return RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=int((parsed.path or "/0").lstrip("/") or "0"),
        password=parsed.password,
    )


async def _run_execution_inline(execution_id: str) -> None:
    """Run a scrape inside the API process when the ARQ worker is unavailable."""
    from app.services.scraping.execution_orchestrator import run_scraping_execution

    try:
        await run_scraping_execution({}, execution_id)
    except Exception:
        logger.exception("scraping_inline_execution_failed execution_id=%s", execution_id)


execution_service = ScrapingExecutionService()


def _primary_contact(
    contacts: list[RehabilitationFacilityContact],
) -> RehabilitationFacilityContact | None:
    normal_contacts = [
        contact
        for contact in contacts
        if contact.contact_type in {"phone", "hotline", "whatsapp", "email", "website", "booking_url", "other"}
    ]
    primary = [contact for contact in normal_contacts if contact.is_primary]
    return (primary or normal_contacts or [None])[0]
