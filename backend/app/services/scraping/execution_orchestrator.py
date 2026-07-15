"""Persistent mock execution campaign worker logic."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.models import (
    ScrapingCoverageCell,
    ScrapingCoverageStatus,
    ScrapingEvent,
    ScrapingExecution,
    ScrapingExecutionAgent,
    ScrapingExecutionAgentStatus,
    ScrapingExecutionStatus,
    ScrapingRun,
    ScrapingTask,
    ScrapingTaskStatus,
)
from app.db.session import AsyncSessionLocal
from app.services.scraping.execution_service import execution_service, sleep_mock_delay
from app.services.scraping.mock_tools import (
    MockBrowserFetchTool,
    MockCountryProfileProvider,
    MockCoverageAuditTool,
    MockDocumentParserTool,
    MockEntityResolutionTool,
    MockHttpFetchTool,
    MockRecordExtractorTool,
    MockSearchTool,
    MockSocialDiscoveryTool,
    MockSourceDiscoveryTool,
    MockVerificationTool,
)

logger = logging.getLogger(__name__)

TASK_TYPES = [
    "build_country_profile",
    "create_coverage_matrix",
    "generate_queries",
    "discover_sources",
    "inspect_source",
    "process_document",
    "extract_records",
    "resolve_duplicates",
    "verify_records",
    "audit_coverage",
    "create_gap_tasks",
]


class ExecutionCancelled(Exception):
    pass


async def run_scraping_execution(ctx: dict, execution_id: str) -> None:
    logger.info(
        "scraping_execution_job_entered",
        extra={"execution_id": execution_id},
    )
    async with AsyncSessionLocal() as db:
        await MockExecutionOrchestrator(db).run(execution_id)


async def recover_scraping_executions(ctx: dict) -> None:
    async with AsyncSessionLocal() as db:
        threshold = datetime.now(UTC) - timedelta(
            seconds=get_settings().scraping_execution_stale_seconds
        )
        result = await db.execute(
            select(ScrapingExecution).where(
                (ScrapingExecution.status == ScrapingExecutionStatus.QUEUED)
                | (
                    (ScrapingExecution.status == ScrapingExecutionStatus.RUNNING)
                    & (
                        (ScrapingExecution.heartbeat_at.is_(None))
                        | (ScrapingExecution.heartbeat_at < threshold)
                    )
                )
            )
        )
        for execution in result.scalars().all():
            execution.status = ScrapingExecutionStatus.QUEUED
            await execution_service.enqueue_execution(execution.id)
        await db.commit()


class MockExecutionOrchestrator:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.current_stage = "not_started"
        self.coverage_region_count = 0
        self.coverage_language_count = 0
        self.coverage_source_category_count = 0
        self.attempted_coverage_cell_count = 0
        self.profile_provider = MockCountryProfileProvider()
        self.search_tool = MockSearchTool()
        self.discovery_tool = MockSourceDiscoveryTool()
        self.http_tool = MockHttpFetchTool()
        self.browser_tool = MockBrowserFetchTool()
        self.document_tool = MockDocumentParserTool()
        self.social_tool = MockSocialDiscoveryTool()
        self.extractor_tool = MockRecordExtractorTool()
        self.resolution_tool = MockEntityResolutionTool()
        self.verification_tool = MockVerificationTool()
        self.audit_tool = MockCoverageAuditTool()

    async def run(self, execution_id: str) -> None:
        safe_execution_id = execution_id
        self.current_stage = "load_execution"
        self._log("orchestrator_entered", execution_id=execution_id)
        execution = await self._load_execution(execution_id)
        if execution is None:
            self._log("execution_skipped", execution_id=execution_id, reason="execution_not_found")
            return
        self._log(
            "execution_loaded",
            execution_id=execution.id,
            status=execution.status.value,
            country_code=execution.country_code,
        )
        self.current_stage = "validate_execution"
        if execution.status in {
            ScrapingExecutionStatus.COMPLETED,
            ScrapingExecutionStatus.FAILED,
            ScrapingExecutionStatus.CANCELLED,
        }:
            self._log(
                "execution_skipped",
                execution_id=execution.id,
                reason="execution_already_terminal",
                status=execution.status.value,
            )
            return
        if execution.status == ScrapingExecutionStatus.CANCEL_REQUESTED:
            self._log(
                "execution_skipped",
                execution_id=execution.id,
                reason="execution_cancel_requested",
            )
            await self._finish_cancelled(execution)
            return
        if execution.status != ScrapingExecutionStatus.QUEUED:
            self._log(
                "execution_skipped",
                execution_id=execution.id,
                reason="execution_not_queued",
                status=execution.status.value,
            )
            return

        if execution.blueprint is None:
            self._log(
                "execution_invalid",
                execution_id=execution.id,
                reason="missing_blueprint",
            )
            await self._mark_failed_safely(execution.id, "missing_blueprint")
            return
        planned_agent_count = len(execution.team_plan.agents) if execution.team_plan else 0
        if planned_agent_count == 0:
            self._log(
                "execution_invalid",
                execution_id=execution.id,
                reason="no_planned_agents",
            )
            await self._mark_failed_safely(execution.id, "no_planned_agents")
            return
        self.current_stage = "load_agents"
        execution_agents = await self._execution_agents(execution.id)
        self._log(
            "execution_agents_loaded",
            execution_id=execution.id,
            execution_agent_count=len(execution_agents),
            planned_agent_count=planned_agent_count,
        )
        if not execution_agents:
            self._log(
                "execution_invalid",
                execution_id=execution.id,
                reason="no_execution_agents",
            )
            await self._mark_failed_safely(execution.id, "no_execution_agents")
            return

        self.current_stage = "claim_execution"
        self._log("execution_claim_attempted", execution_id=execution.id)
        if not await self._claim_execution(execution):
            self._log(
                "execution_skipped",
                execution_id=execution.id,
                reason="execution_claim_failed",
            )
            return
        self._log("execution_claim_succeeded", execution_id=execution.id)
        await execution_service.emit_event(
            self.db, execution.id, "execution_started", "Mock execution campaign started."
        )
        await self.db.commit()

        try:
            await self._ensure_profile_matrix_and_tasks(execution, execution_agents)
            await self._process_tasks(execution)
            await self._refresh_metrics(execution)
            if execution.status == ScrapingExecutionStatus.CANCEL_REQUESTED:
                await self._finish_cancelled(execution)
                return
            self.current_stage = "complete_execution"
            execution.status = ScrapingExecutionStatus.COMPLETED
            execution.completed_at = datetime.now(UTC)
            await execution_service.emit_event(
                self.db,
                execution.id,
                "execution_completed",
                "Mock execution completed with persisted coverage and task state.",
            )
            await self.db.commit()
            self._log(
                "orchestrator_completed",
                execution_id=execution.id,
                terminal_status=execution.status.value,
                sources_discovered=execution.sources_discovered,
                coverage_debt=execution.coverage_debt,
            )
        except ExecutionCancelled:
            self._log(
                "execution_skipped",
                execution_id=safe_execution_id,
                reason="execution_cancelled",
            )
            return
        except Exception as exc:
            failure_category = type(exc).__name__
            logger.exception(
                (
                    "scraping_execution_failed execution_id=%s stage=%s "
                    "exception_type=%s region_count=%s language_count=%s "
                    "source_category_count=%s attempted_coverage_cell_count=%s"
                ),
                safe_execution_id,
                self.current_stage,
                failure_category,
                self.coverage_region_count,
                self.coverage_language_count,
                self.coverage_source_category_count,
                self.attempted_coverage_cell_count,
                extra={
                    "scraping_execution_event": "orchestrator_failed",
                    "execution_id": safe_execution_id,
                    "stage": self.current_stage,
                    "failure_category": failure_category,
                    "region_count": self.coverage_region_count,
                    "language_count": self.coverage_language_count,
                    "source_category_count": self.coverage_source_category_count,
                    "attempted_coverage_cell_count": self.attempted_coverage_cell_count,
                },
            )
            await self._mark_failed_safely(safe_execution_id, failure_category)

    async def _ensure_profile_matrix_and_tasks(
        self,
        execution: ScrapingExecution,
        execution_agents: list[ScrapingExecutionAgent],
    ) -> None:
        await self._check_cancelled(execution)
        if execution.country_profile_json is None:
            self.current_stage = "build_country_profile"
            profile = self.profile_provider.build_profile(execution, execution.blueprint)
            execution.country_profile_json = profile.as_json()
            self.current_stage = "persist_country_profile"
            await execution_service.emit_event(
                self.db,
                execution.id,
                "task_completed",
                "Country profile snapshot created from the approved blueprint.",
                metadata={"task_type": "build_country_profile"},
            )
            await self.db.commit()
            self._log(
                "country_profile_created",
                execution_id=execution.id,
                region_count=len(profile.administrative_regions),
                language_count=len(profile.languages),
                source_category_count=len(profile.source_categories),
            )

        coverage_count = await self._coverage_count(execution.id)
        if coverage_count == 0:
            self.current_stage = "normalize_coverage_dimensions"
            profile_json = execution.country_profile_json or {}
            regions, languages, categories = self._coverage_dimensions(
                profile_json,
                execution.country_code,
                execution.country_name,
            )
            self.coverage_region_count = len(regions)
            self.coverage_language_count = len(languages)
            self.coverage_source_category_count = len(categories)
            self.attempted_coverage_cell_count = len(regions) * len(languages) * len(categories)
            coverage_cells: list[ScrapingCoverageCell] = []
            self.current_stage = "construct_coverage_cells"
            for region in regions:
                for language in languages:
                    for category in categories:
                        assigned = self._assign_agent(execution_agents, category)
                        coverage_cells.append(
                            ScrapingCoverageCell(
                                execution_id=execution.id,
                                region_code=region.get("code"),
                                region_name=region["name"],
                                language_code=language.get("code"),
                                language_name=language["name"],
                                source_category=category,
                                status=ScrapingCoverageStatus.NOT_STARTED,
                                assigned_execution_agent_id=assigned.id,
                                metadata_json={"mock": True},
                            )
                        )
            self.db.add_all(coverage_cells)
            self.current_stage = "flush_coverage_cells"
            await self.db.flush()
            await execution_service.emit_event(
                self.db,
                execution.id,
                "task_completed",
                "Coverage matrix created and persisted before processing.",
                metadata={"task_type": "create_coverage_matrix"},
            )
            await self.db.commit()
            coverage_count = await self._coverage_count(execution.id)
            self._log(
                "coverage_cells_created",
                execution_id=execution.id,
                coverage_cell_count=coverage_count,
                region_count=len(regions),
                language_count=len(languages),
                source_category_count=len(categories),
            )

        task_count = await self._task_count(execution.id)
        if task_count == 0:
            self.current_stage = "create_initial_tasks"
            cells = await self._coverage_cells(execution.id)
            for index, cell in enumerate(cells):
                self.db.add(
                    ScrapingTask(
                        execution_id=execution.id,
                        execution_agent_id=cell.assigned_execution_agent_id,
                        coverage_cell_id=cell.id,
                        task_type="discover_sources",
                        title=(
                            f"Discover {cell.region_name} x {cell.language_name} x "
                            f"{cell.source_category}"
                        ),
                        status=ScrapingTaskStatus.QUEUED,
                        priority=100 + index,
                        input_json={
                            "mock": True,
                            "country_code": execution.country_code,
                            "region_name": cell.region_name,
                            "language_name": cell.language_name,
                            "source_category": cell.source_category,
                        },
                        output_json={},
                        dependency_task_ids_json=[],
                    )
                )
            self.current_stage = "flush_initial_tasks"
            await self.db.flush()
            await execution_service.emit_event(
                self.db,
                execution.id,
                "task_queued",
                "Discovery tasks created from the coverage matrix.",
                metadata={"task_type": "discover_sources"},
            )
            await self.db.commit()
            self._log(
                "tasks_created",
                execution_id=execution.id,
                task_count=await self._task_count(execution.id),
                coverage_cell_count=len(cells),
            )

    async def _process_tasks(self, execution: ScrapingExecution) -> None:
        tasks = await self._queued_tasks(execution.id)
        for task in tasks:
            execution = await self._load_execution(execution.id)
            await self._check_cancelled(execution)
            agent = task.execution_agent
            agent.status = ScrapingExecutionAgentStatus.RUNNING
            agent.current_task_id = task.id
            agent.current_action = "Running deterministic mock discovery"
            agent.started_at = agent.started_at or datetime.now(UTC)
            task.status = ScrapingTaskStatus.RUNNING
            task.started_at = datetime.now(UTC)
            task.current_action = "Generating mock queries"
            if task.coverage_cell:
                task.coverage_cell.status = ScrapingCoverageStatus.IN_PROGRESS
                task.coverage_cell.started_at = datetime.now(UTC)
            await execution_service.emit_event(
                self.db,
                execution.id,
                "task_started",
                f"{agent.team_agent.name} started {task.title}.",
                execution_agent_id=agent.id,
                task_id=task.id,
                coverage_cell_id=task.coverage_cell_id,
            )
            await self.db.commit()
            await sleep_mock_delay()

            output = self.search_tool.run(task)
            output.update(self.discovery_tool.run(task))
            output.update(self.http_tool.run(task))
            output.update(self.browser_tool.run(task))
            output.update(self.document_tool.run(task))
            output.update(self.social_tool.run(task))
            output.update(self.extractor_tool.run(task))
            output.update(self.resolution_tool.run(task))
            output.update(self.verification_tool.run(task))
            output.update(self.audit_tool.run(task))
            task.output_json = output
            await execution_service.emit_event(
                self.db,
                execution.id,
                "source_discovered",
                f"Mock discovery produced {len(output.get('sources', []))} candidate sources.",
                execution_agent_id=agent.id,
                task_id=task.id,
                coverage_cell_id=task.coverage_cell_id,
            )

            task.status = ScrapingTaskStatus.COMPLETED
            task.completed_at = datetime.now(UTC)
            task.current_action = None
            if task.coverage_cell:
                self._complete_cell(task.coverage_cell, task)
            agent.status = ScrapingExecutionAgentStatus.COMPLETED
            agent.current_task_id = None
            agent.current_action = None
            agent.completed_at = datetime.now(UTC)
            execution.heartbeat_at = datetime.now(UTC)
            await self._refresh_metrics(execution)
            await execution_service.emit_event(
                self.db,
                execution.id,
                "task_completed",
                f"{task.title} completed with mock persisted output.",
                execution_agent_id=agent.id,
                task_id=task.id,
                coverage_cell_id=task.coverage_cell_id,
            )
            await self.db.commit()

        await self._create_gap_audit_task(execution)

    async def _create_gap_audit_task(self, execution: ScrapingExecution) -> None:
        debt = await self._coverage_debt(execution.id)
        execution.coverage_debt = debt
        if debt <= 0:
            self._log(
                "gap_audit_skipped",
                execution_id=execution.id,
                reason="no_coverage_debt",
            )
            return
        agents = await self._execution_agents(execution.id)
        agent = self._assign_agent(agents, "audit_coverage")
        exists_result = await self.db.execute(
            select(ScrapingTask.id).where(
                ScrapingTask.execution_id == execution.id,
                ScrapingTask.task_type == "audit_coverage",
            )
        )
        if exists_result.scalar_one_or_none():
            self._log(
                "gap_audit_skipped",
                execution_id=execution.id,
                reason="audit_task_already_exists",
            )
            return
        self.db.add(
            ScrapingTask(
                execution_id=execution.id,
                execution_agent_id=agent.id,
                task_type="audit_coverage",
                title="Audit remaining mock coverage debt",
                status=ScrapingTaskStatus.COMPLETED,
                priority=1000,
                input_json={"mock": True, "coverage_debt": debt},
                output_json={"mock": True, "gap_tasks_created": debt},
                dependency_task_ids_json=[],
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )
        await execution_service.emit_event(
            self.db,
            execution.id,
            "coverage_gap_detected",
            f"Coverage audit detected {debt} incomplete mock coverage cells.",
            execution_agent_id=agent.id,
        )
        await self.db.commit()

    def _complete_cell(self, cell: ScrapingCoverageCell, task: ScrapingTask) -> None:
        output = task.output_json or {}
        selector = (sum(ord(char) for char in cell.id) + len(cell.source_category)) % 7
        result_count = int(output.get("records_extracted") or 0)
        cell.result_count = result_count
        cell.completed_at = datetime.now(UTC)
        if selector == 0:
            cell.status = ScrapingCoverageStatus.BLOCKED
            cell.reason = "Mock source blocked for demonstration."
        elif selector == 1:
            cell.status = ScrapingCoverageStatus.HUMAN_REVIEW_REQUIRED
            cell.reason = "Mock ambiguity requires human review."
        elif selector == 2:
            cell.status = ScrapingCoverageStatus.COVERED_NO_RESULTS
            cell.result_count = 0
            cell.reason = "Mock coverage completed with no results."
        elif selector == 3:
            cell.status = ScrapingCoverageStatus.PARTIALLY_COVERED
            cell.reason = "Mock coverage is incomplete."
        else:
            cell.status = ScrapingCoverageStatus.COVERED
            cell.reason = None

    async def _refresh_metrics(self, execution: ScrapingExecution) -> None:
        result = await self.db.execute(
            select(ScrapingTask).where(
                ScrapingTask.execution_id == execution.id,
                ScrapingTask.status == ScrapingTaskStatus.COMPLETED,
            )
        )
        tasks = result.scalars().all()
        execution.sources_discovered = sum(
            len((task.output_json or {}).get("sources", [])) for task in tasks
        )
        execution.documents_found = sum(
            int((task.output_json or {}).get("documents_found") or 0) for task in tasks
        )
        execution.records_extracted = sum(
            int((task.output_json or {}).get("records_extracted") or 0) for task in tasks
        )
        execution.records_verified = sum(
            int((task.output_json or {}).get("records_verified") or 0) for task in tasks
        )
        execution.duplicates_detected = sum(
            int((task.output_json or {}).get("duplicates_detected") or 0) for task in tasks
        )
        execution.blocked_sources = await self._cell_status_count(
            execution.id, ScrapingCoverageStatus.BLOCKED
        )
        execution.coverage_debt = await self._coverage_debt(execution.id)

    async def _finish_cancelled(self, execution: ScrapingExecution) -> None:
        await execution_service._cancel_pending_children(self.db, execution.id)
        execution.status = ScrapingExecutionStatus.CANCELLED
        execution.completed_at = datetime.now(UTC)
        await execution_service.emit_event(
            self.db, execution.id, "execution_cancelled", "Mock execution cancelled."
        )
        await self.db.commit()
        self._log(
            "terminal_status_written",
            execution_id=execution.id,
            terminal_status=execution.status.value,
        )

    async def _check_cancelled(self, execution: ScrapingExecution) -> None:
        await self.db.refresh(execution)
        if execution.status == ScrapingExecutionStatus.CANCEL_REQUESTED:
            await self._finish_cancelled(execution)
            raise ExecutionCancelled()

    async def _mark_failed_safely(self, execution_id: str, failure_category: str) -> None:
        try:
            await self.db.rollback()
            execution = await self._load_execution(execution_id)
            if execution is None:
                self._log(
                    "failure_state_skipped",
                    execution_id=execution_id,
                    reason="execution_not_found",
                    failure_category=failure_category,
                )
                return
            if execution.status in {
                ScrapingExecutionStatus.COMPLETED,
                ScrapingExecutionStatus.FAILED,
                ScrapingExecutionStatus.CANCELLED,
            }:
                self._log(
                    "failure_state_skipped",
                    execution_id=execution_id,
                    reason="execution_already_terminal",
                    status=execution.status.value,
                    failure_category=failure_category,
                )
                return
            execution.status = ScrapingExecutionStatus.FAILED
            execution.error_message = "Mock execution failed."
            execution.completed_at = datetime.now(UTC)
            existing_event = await self.db.execute(
                select(ScrapingEvent.id).where(
                    ScrapingEvent.execution_id == execution_id,
                    ScrapingEvent.event_type == "execution_failed",
                )
            )
            if existing_event.scalar_one_or_none() is None:
                await execution_service.emit_event(
                    self.db,
                    execution_id,
                    "execution_failed",
                    "Mock execution failed.",
                )
            await self.db.commit()
            self._log(
                "terminal_status_written",
                execution_id=execution_id,
                terminal_status=ScrapingExecutionStatus.FAILED.value,
                failure_category=failure_category,
            )
        except Exception:
            try:
                await self.db.rollback()
            finally:
                self._log(
                    "failure_state_persist_failed",
                    execution_id=execution_id,
                    failure_category=failure_category,
                    level="error",
                )
            raise RuntimeError("Failed to persist scraping execution failure state.") from None

    async def _claim_execution(self, execution: ScrapingExecution) -> bool:
        now = datetime.now(UTC)
        result = await self.db.execute(
            update(ScrapingExecution)
            .where(
                ScrapingExecution.id == execution.id,
                ScrapingExecution.status == ScrapingExecutionStatus.QUEUED,
            )
            .values(
                status=ScrapingExecutionStatus.RUNNING,
                started_at=execution.started_at or now,
                heartbeat_at=now,
            )
        )
        if result.rowcount != 1:
            await self.db.rollback()
            return False
        execution.status = ScrapingExecutionStatus.RUNNING
        execution.started_at = execution.started_at or now
        execution.heartbeat_at = now
        await self.db.flush()
        return True

    async def _load_execution(self, execution_id: str) -> ScrapingExecution | None:
        result = await self.db.execute(
            select(ScrapingExecution)
            .where(ScrapingExecution.id == execution_id)
            .options(
                selectinload(ScrapingExecution.blueprint),
                selectinload(ScrapingExecution.team_plan).selectinload(ScrapingRun.agents),
            )
        )
        return result.scalar_one_or_none()

    async def _execution_agents(self, execution_id: str) -> list[ScrapingExecutionAgent]:
        result = await self.db.execute(
            select(ScrapingExecutionAgent)
            .where(ScrapingExecutionAgent.execution_id == execution_id)
            .options(selectinload(ScrapingExecutionAgent.team_agent))
            .order_by(ScrapingExecutionAgent.created_at)
        )
        return list(result.scalars().all())

    async def _coverage_cells(self, execution_id: str) -> list[ScrapingCoverageCell]:
        result = await self.db.execute(
            select(ScrapingCoverageCell)
            .where(ScrapingCoverageCell.execution_id == execution_id)
            .order_by(
                ScrapingCoverageCell.region_name,
                ScrapingCoverageCell.language_name,
                ScrapingCoverageCell.source_category,
            )
        )
        return list(result.scalars().all())

    async def _queued_tasks(self, execution_id: str) -> list[ScrapingTask]:
        result = await self.db.execute(
            select(ScrapingTask)
            .where(
                ScrapingTask.execution_id == execution_id,
                ScrapingTask.status == ScrapingTaskStatus.QUEUED,
            )
            .options(
                selectinload(ScrapingTask.execution_agent).selectinload(
                    ScrapingExecutionAgent.team_agent
                ),
                selectinload(ScrapingTask.coverage_cell),
            )
            .order_by(ScrapingTask.priority, ScrapingTask.created_at)
        )
        return list(result.scalars().all())

    async def _coverage_count(self, execution_id: str) -> int:
        result = await self.db.execute(
            select(ScrapingCoverageCell.id).where(ScrapingCoverageCell.execution_id == execution_id)
        )
        return len(result.scalars().all())

    async def _task_count(self, execution_id: str) -> int:
        result = await self.db.execute(
            select(ScrapingTask.id).where(ScrapingTask.execution_id == execution_id)
        )
        return len(result.scalars().all())

    async def _cell_status_count(
        self, execution_id: str, status: ScrapingCoverageStatus
    ) -> int:
        result = await self.db.execute(
            select(ScrapingCoverageCell.id).where(
                ScrapingCoverageCell.execution_id == execution_id,
                ScrapingCoverageCell.status == status,
            )
        )
        return len(result.scalars().all())

    async def _coverage_debt(self, execution_id: str) -> int:
        result = await self.db.execute(
            select(ScrapingCoverageCell.id).where(
                ScrapingCoverageCell.execution_id == execution_id,
                ScrapingCoverageCell.status.in_(
                    [
                        ScrapingCoverageStatus.NOT_STARTED,
                        ScrapingCoverageStatus.PARTIALLY_COVERED,
                        ScrapingCoverageStatus.BLOCKED,
                        ScrapingCoverageStatus.HUMAN_REVIEW_REQUIRED,
                        ScrapingCoverageStatus.FAILED,
                    ]
                ),
            )
        )
        return len(result.scalars().all())

    def _coverage_dimensions(
        self,
        profile_json: dict,
        country_code: str,
        country_name: str,
    ) -> tuple[list[dict[str, str | None]], list[dict[str, str | None]], list[str]]:
        raw_regions = profile_json.get("administrative_regions")
        raw_languages = profile_json.get("languages")
        raw_categories = profile_json.get("source_categories")

        regions: list[dict[str, str | None]] = []
        seen_regions: set[str] = set()
        for raw_region in raw_regions if isinstance(raw_regions, list) else []:
            if isinstance(raw_region, dict):
                name = self._clean_text(raw_region.get("name"))
                code = self._clean_text(raw_region.get("code"))
            else:
                name = self._clean_text(raw_region)
                code = None
            if not name:
                continue
            identity = name.casefold()
            if identity in seen_regions:
                continue
            seen_regions.add(identity)
            regions.append(
                {
                    "code": self._coverage_region_code(code or name, country_code),
                    "name": name,
                }
            )
        if not regions:
            regions.append(
                {
                    "code": self._coverage_region_code(country_name, country_code),
                    "name": country_name.strip() or country_code,
                }
            )

        languages: list[dict[str, str | None]] = []
        seen_languages: set[tuple[str, str]] = set()
        for raw_language in raw_languages if isinstance(raw_languages, list) else []:
            if isinstance(raw_language, dict):
                name = self._clean_text(raw_language.get("name"))
                code = self._clean_text(raw_language.get("code"))
            else:
                name = self._clean_text(raw_language)
                code = None
            if not name:
                continue
            identity = (code.casefold() if code else "", name.casefold())
            if identity in seen_languages:
                continue
            seen_languages.add(identity)
            languages.append({"code": code, "name": name})
        if not languages:
            languages.append({"code": "en", "name": "English"})

        categories: list[str] = []
        seen_categories: set[str] = set()
        for raw_category in raw_categories if isinstance(raw_categories, list) else []:
            category = self._clean_text(raw_category)
            if not category:
                continue
            identity = category.casefold()
            if identity in seen_categories:
                continue
            seen_categories.add(identity)
            categories.append(category)
        if not categories:
            categories.append("general_web")

        return regions, languages, categories

    def _coverage_region_code(self, value: str | None, country_code: str) -> str:
        text = self._clean_text(value) or self._clean_text(country_code) or "region"
        slug = "-".join(text.lower().replace("_", " ").split())
        slug = "".join(char for char in slug if char.isalnum() or char == "-").strip("-")
        slug = slug or "region"
        if len(slug) <= 32:
            return slug
        digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:8]
        return f"{slug[:23].rstrip('-')}-{digest}"[:32]

    def _clean_text(self, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    def _assign_agent(
        self, agents: list[ScrapingExecutionAgent], source_category: str
    ) -> ScrapingExecutionAgent:
        if not agents:
            raise RuntimeError("no_execution_agents")
        category = source_category.lower()
        for agent in agents:
            haystack = " ".join(
                [
                    agent.team_agent.name,
                    agent.team_agent.role,
                    agent.team_agent.purpose,
                    str(agent.team_agent.assigned_scope),
                ]
            ).lower()
            if any(word in haystack for word in category.replace("_", " ").split()):
                return agent
        return agents[sum(ord(char) for char in source_category) % len(agents)]

    def _log(self, event: str, *, level: str = "info", **fields: object) -> None:
        getattr(logger, level)(
            "scraping_execution_%s",
            event,
            extra={"scraping_execution_event": event, **fields},
        )
