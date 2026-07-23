"""Persistent real source-discovery execution campaign worker logic."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.db.models import (
    FacilityCandidatePublicationStatus,
    RehabilitationFacility,
    RehabilitationPossibleDuplicate,
    ScrapingFacilityCandidate,
    ScrapingFacilityCandidateEvidence,
    ScrapingFacilityCandidatePublication,
    ScrapingFacilityExtractionAttempt,
    ScrapingCoverageCell,
    ScrapingCoverageStatus,
    ScrapingEvent,
    ScrapingExecution,
    ScrapingExecutionAgent,
    ScrapingExecutionAgentStatus,
    ScrapingExecutionStatus,
    ScrapingRun,
    ScrapingSourceCandidate,
    ScrapingSourceDocument,
    ScrapingSourceDocumentChunk,
    ScrapingSourceDocumentText,
    ScrapingSourceDiscoveryQuery,
    ScrapingSourceRetrievalAttempt,
    ScrapingTask,
    ScrapingTaskStatus,
    SourceCandidateStatus,
    SourceDiscoveryQueryStatus,
    SourceRetrievalAttemptStatus,
)
from app.db.session import AsyncSessionLocal
from app.schemas.api import SourceDiscoveryContext, SourceDiscoverySummary
from app.services.scraping.document_text_preparation_service import (
    SourceDocumentPreparationContext,
    document_text_preparation_service,
)
from app.services.scraping.execution_service import execution_service
from app.services.scraping.execution_outcome import GAP_COVERAGE_STATUSES
from app.services.scraping.facility_candidate_publication_service import (
    facility_candidate_publication_service,
)
from app.services.scraping.facility_extraction_service import (
    FacilityExtractionContext,
    facility_extraction_service,
)
from app.services.scraping.source_discovery_service import source_discovery_service
from app.services.scraping.scale_profile import ScaleProfile, resolve_scale_profile
from app.services.scraping.source_retrieval_service import (
    SourceRetrievalContext,
    SourceRetrievalSummary,
    source_retrieval_service,
)

logger = logging.getLogger(__name__)

TASK_TYPES = ["create_coverage_matrix", "discover_sources", "retrieve_source", "audit_coverage"]

METRIC_REFRESH_TASK_INTERVAL = 25

NON_RETRYABLE_RETRIEVAL_STATUSES = {
    SourceRetrievalAttemptStatus.UNSAFE_URL.value,
    SourceRetrievalAttemptStatus.PRIVATE_OR_RESERVED_ADDRESS.value,
    SourceRetrievalAttemptStatus.BLOCKED_BY_ROBOTS.value,
    SourceRetrievalAttemptStatus.UNSUPPORTED_CONTENT_TYPE.value,
    SourceRetrievalAttemptStatus.MALFORMED_CONTENT.value,
    SourceRetrievalAttemptStatus.UNSAFE_REDIRECT.value,
    SourceRetrievalAttemptStatus.REDIRECT_LIMIT_EXCEEDED.value,
}

RETRYABLE_RETRIEVAL_STATUSES = {
    SourceRetrievalAttemptStatus.TIMEOUT.value,
    SourceRetrievalAttemptStatus.CONNECTION_FAILED.value,
    SourceRetrievalAttemptStatus.PROVIDER_HTTP_ERROR.value,
}

OFFICIAL_SOURCE_CATEGORY_TERMS = (
    "official",
    "government",
    "gov",
    "public health",
    "public-health",
    "ministry",
)

LANGUAGE_CODE_BY_NAME = {
    "arabic": "ar",
    "bengali": "bn",
    "catalan": "ca",
    "chinese": "zh",
    "dutch": "nl",
    "english": "en",
    "french": "fr",
    "german": "de",
    "hindi": "hi",
    "indonesian": "id",
    "italian": "it",
    "japanese": "ja",
    "korean": "ko",
    "malay": "ms",
    "portuguese": "pt",
    "russian": "ru",
    "spanish": "es",
    "turkish": "tr",
    "urdu": "ur",
}


class ExecutionCancelled(Exception):
    pass


class CoverageDimensionError(RuntimeError):
    pass


async def run_scraping_execution(ctx: dict, execution_id: str) -> None:
    logger.info(
        "scraping_execution_job_entered",
        extra={"execution_id": execution_id},
    )
    try:
        async with AsyncSessionLocal() as db:
            await SourceDiscoveryExecutionOrchestrator(db).run(execution_id)
    except asyncio.CancelledError:
        logger.warning(
            "scraping_execution_job_cancelled",
            extra={"execution_id": execution_id, "scraping_execution_event": "job_cancelled"},
        )
        cleanup_task = asyncio.create_task(
            mark_scraping_execution_timeout_failed(execution_id)
        )
        try:
            await asyncio.shield(cleanup_task)
        finally:
            raise


async def mark_scraping_execution_timeout_failed(execution_id: str) -> None:
    async with AsyncSessionLocal() as db:
        await SourceDiscoveryExecutionOrchestrator(db)._mark_failed_safely(
            execution_id,
            "worker_timeout",
            error_message="Source discovery execution failed after the worker job timed out.",
            event_message="Source discovery execution failed after the worker job timed out.",
        )


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


class SourceDiscoveryExecutionOrchestrator:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.current_stage = "not_started"
        self.coverage_region_count = 0
        self.coverage_language_count = 0
        self.coverage_source_category_count = 0
        self.attempted_coverage_cell_count = 0
        self.scale_profile: ScaleProfile = resolve_scale_profile("real", get_settings())

    async def run(self, execution_id: str) -> None:
        safe_execution_id = execution_id
        self.current_stage = "load_execution"
        self._log("orchestrator_entered", execution_id=execution_id)
        execution = await self._load_execution(execution_id)
        if execution is None:
            self._log("execution_skipped", execution_id=execution_id, reason="execution_not_found")
            return
        self.scale_profile = resolve_scale_profile(execution.mode, get_settings())
        self._log(
            "execution_loaded",
            execution_id=execution.id,
            status=execution.status.value,
            country_code=execution.country_code,
            mode=self.scale_profile.mode,
            scale_label=self.scale_profile.label,
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
            self.db,
            execution.id,
            "execution_started",
            f"{self.scale_profile.label} source discovery campaign started.",
            metadata=self.scale_profile.as_metadata(),
        )
        await self.db.commit()

        try:
            await self._ensure_profile_matrix_and_tasks(execution, execution_agents)
            await self._process_tasks(execution)
            await self._check_cancelled(execution)
            await self._run_facility_extraction_phase(execution)
            await self._check_cancelled(execution)
            await self._run_facility_publication_phase(execution)
            await self._check_cancelled(execution)
            await self._refresh_metrics(execution)
            if execution.status == ScrapingExecutionStatus.CANCEL_REQUESTED:
                await self._finish_cancelled(execution)
                return
            self.current_stage = "complete_execution"
            execution.status = ScrapingExecutionStatus.COMPLETED
            execution.completed_at = datetime.now(UTC)
            settings = get_settings()
            if settings.facility_publication_enabled and settings.facility_extraction_enabled:
                completion_message = (
                    "Real source discovery, retrieval, facility extraction, and "
                    "publication completed."
                )
            elif settings.facility_extraction_enabled:
                completion_message = (
                    "Real source discovery, secure retrieval, and bounded facility "
                    "extraction completed."
                )
            else:
                completion_message = (
                    "Real source discovery and bounded secure retrieval completed. "
                    "Facility extraction is disabled."
                )
            await execution_service.emit_event(
                self.db,
                execution.id,
                "execution_completed",
                completion_message,
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
            await self._mark_failed_safely(
                safe_execution_id,
                failure_category,
                error_message=(
                    f"Source discovery execution failed during {self.current_stage}: "
                    f"{failure_category}"
                ),
                event_message=(
                    f"Source discovery execution failed during {self.current_stage} "
                    f"({failure_category})."
                ),
            )

    async def _ensure_profile_matrix_and_tasks(
        self,
        execution: ScrapingExecution,
        execution_agents: list[ScrapingExecutionAgent],
    ) -> None:
        await self._check_cancelled(execution)
        if execution.country_profile_json is None:
            self.current_stage = "snapshot_source_discovery_profile"
            regions, languages, categories = self._coverage_dimensions_from_blueprint(
                execution.blueprint.blueprint_json or {},
                execution.country_code,
                execution.country_name,
            )
            profile = self.scale_profile
            max_queries = min(
                max(profile.serper_max_queries_per_discovery, 1),
                profile.discovery_query_hard_cap,
            )
            results_per_query = min(
                max(profile.serper_results_per_query, 1),
                profile.discovery_results_hard_cap,
            )
            execution.country_profile_json = {
                "phase": "source_discovery",
                "mode": profile.mode,
                "country_code": execution.country_code,
                "country_name": execution.country_name,
                "administrative_regions": regions,
                "languages": languages,
                "source_categories": categories,
                "max_queries_per_discovery": max_queries,
                "results_per_query": results_per_query,
                "max_search_request_count": len(regions)
                * len(languages)
                * len(categories)
                * max_queries,
            }
            await execution_service.emit_event(
                self.db,
                execution.id,
                "task_completed",
                "Source discovery profile snapshot created from the approved blueprint.",
                metadata={
                    "task_type": "source_discovery_profile",
                    "max_search_request_count": execution.country_profile_json[
                        "max_search_request_count"
                    ],
                },
            )
            await self.db.commit()
            self._log(
                "source_discovery_profile_created",
                execution_id=execution.id,
                region_count=len(regions),
                language_count=len(languages),
                source_category_count=len(categories),
            )

        coverage_count = await self._coverage_count(execution.id)
        if coverage_count == 0:
            self.current_stage = "normalize_coverage_dimensions"
            profile_json = execution.country_profile_json or {}
            regions = profile_json["administrative_regions"]
            languages = profile_json["languages"]
            categories = profile_json["source_categories"]
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
                                metadata_json={
                                    "phase": "source_discovery",
                                    "provider": get_settings().source_discovery_provider,
                                },
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
                            "country_code": execution.country_code,
                            "country_name": execution.country_name,
                            "region_code": cell.region_code,
                            "region_name": cell.region_name,
                            "language_code": cell.language_code,
                            "language_name": cell.language_name,
                            "source_category": cell.source_category,
                            "phase": "source_discovery",
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
        processed_count = 0
        while True:
            tasks = await self._queued_tasks(execution.id)
            if not tasks:
                break
            for task in tasks:
                processed_count += 1
                execution = await self._load_execution(execution.id)
                await self._check_cancelled(execution)
                if task.task_type == "discover_sources":
                    await self._process_discovery_task(execution, task)
                elif task.task_type == "retrieve_source":
                    await self._process_retrieval_task(execution, task)
                elif task.task_type == "audit_coverage":
                    await self._process_audit_task(execution, task)
                else:
                    task.status = ScrapingTaskStatus.FAILED
                    task.completed_at = datetime.now(UTC)
                    task.error_message = f"Unsupported scraping task type: {task.task_type}"
                    await self.db.commit()
                execution.heartbeat_at = datetime.now(UTC)
                if processed_count % METRIC_REFRESH_TASK_INTERVAL == 0:
                    await self._refresh_metrics(execution)
                    await self.db.commit()

        await self._reconcile_coverage_after_retrieval(execution)
        await self._refresh_metrics(execution)
        await self.db.commit()
        await self._create_gap_audit_task(execution)

    async def _run_facility_extraction_phase(self, execution: ScrapingExecution) -> None:
        self.current_stage = "facility_extraction"
        settings = get_settings()
        if not settings.facility_extraction_enabled:
            await execution_service.emit_event(
                self.db,
                execution.id,
                "facility_extraction_phase_disabled",
                "Facility extraction phase skipped because it is disabled.",
                metadata={"enabled": False},
            )
            await self.db.commit()
            self._log(
                "facility_extraction_phase_disabled",
                execution_id=execution.id,
                enabled=False,
            )
            return

        documents = await self._source_documents_for_extraction(execution)
        document_limit = max(self.scale_profile.extraction_max_documents, 1)
        chunk_limit = max(self.scale_profile.extraction_max_chunks, 1)
        selected_documents = documents[:document_limit]
        summary: dict[str, Any] = {
            "documents_considered": len(documents),
            "documents_prepared": 0,
            "documents_skipped": max(len(documents) - len(selected_documents), 0),
            "documents_failed": 0,
            "chunks_considered": 0,
            "chunks_succeeded": 0,
            "chunks_failed": 0,
            "staging_candidates_created": 0,
            "accepted_evidence_count": 0,
            "rejected_evidence_count": 0,
            "document_limit_reached": len(documents) > document_limit,
            "chunk_limit_reached": False,
        }
        await execution_service.emit_event(
            self.db,
            execution.id,
            "facility_extraction_phase_started",
            "Facility extraction phase started for retrieved source documents.",
            metadata={
                "documents_considered": summary["documents_considered"],
                "document_limit": document_limit,
                "chunk_limit": chunk_limit,
                "document_limit_reached": summary["document_limit_reached"],
            },
        )
        await self.db.commit()

        for document in selected_documents:
            await self._check_cancelled(execution)
            if summary["chunks_considered"] >= chunk_limit:
                summary["chunk_limit_reached"] = True
                summary["documents_skipped"] += 1
                continue
            prepared = await document_text_preparation_service.prepare(
                self.db,
                SourceDocumentPreparationContext(
                    organization_id=execution.organization_id,
                    execution_id=execution.id,
                    source_document_id=document.id,
                ),
            )
            if prepared.preparation_status != "prepared" or not prepared.id:
                summary["documents_skipped"] += 1
                await execution_service.emit_event(
                    self.db,
                    execution.id,
                    "facility_extraction_document_skipped",
                    "Source document was skipped before extraction.",
                    metadata={
                        "source_document_id": document.id,
                        "source_hostname": _hostname(document.final_url),
                        "preparation_status": prepared.preparation_status,
                        "failure_classification": prepared.failure_classification,
                    },
                )
                await self.db.commit()
                continue
            summary["documents_prepared"] += 1
            await execution_service.emit_event(
                self.db,
                execution.id,
                "facility_extraction_document_prepared",
                "Source document prepared for facility extraction.",
                metadata={
                    "source_document_id": document.id,
                    "source_hostname": _hostname(document.final_url),
                    "prepared_character_count": prepared.character_count,
                    "chunk_count": prepared.chunk_count,
                    "truncated": prepared.truncated,
                    "prepared_text_hash_prefix": (prepared.prepared_text_hash or "")[:12],
                },
            )
            await self.db.commit()
            chunks = await self._chunks_for_prepared_text(prepared.id)
            document_had_failure = False
            document_candidate_count = 0
            document_evidence_count = 0
            for chunk in chunks:
                await self._check_cancelled(execution)
                if summary["chunks_considered"] >= chunk_limit:
                    summary["chunk_limit_reached"] = True
                    break
                summary["chunks_considered"] += 1
                extraction_summary = await facility_extraction_service.extract_one_chunk(
                    self.db,
                    FacilityExtractionContext(
                        organization_id=execution.organization_id,
                        execution_id=execution.id,
                        source_document_id=document.id,
                        prepared_text_id=prepared.id,
                        chunk_id=chunk.id,
                        coverage_cell_id=chunk.coverage_cell_id,
                        idempotency_key=self._facility_extraction_attempt_key(
                            execution.id, document.id, chunk.id
                        ),
                    ),
                )
                if extraction_summary.status == "succeeded":
                    summary["chunks_succeeded"] += 1
                    summary["staging_candidates_created"] += (
                        extraction_summary.extracted_candidate_count
                    )
                    summary["accepted_evidence_count"] += (
                        extraction_summary.accepted_evidence_count
                    )
                    summary["rejected_evidence_count"] += (
                        extraction_summary.rejected_evidence_count
                    )
                    document_candidate_count += extraction_summary.extracted_candidate_count
                    document_evidence_count += extraction_summary.accepted_evidence_count
                else:
                    summary["chunks_failed"] += 1
                    document_had_failure = True
                    self._log(
                        "facility_extraction_chunk_failed",
                        execution_id=execution.id,
                        source_document_id=document.id,
                        chunk_id=chunk.id,
                        failure_classification=extraction_summary.failure_classification,
                    )
            if document_had_failure:
                summary["documents_failed"] += 1
            await execution_service.emit_event(
                self.db,
                execution.id,
                "facility_extraction_document_completed",
                "Source document facility extraction completed.",
                metadata={
                    "source_document_id": document.id,
                    "source_hostname": _hostname(document.final_url),
                    "candidate_count": document_candidate_count,
                    "accepted_evidence_count": document_evidence_count,
                    "had_failure": document_had_failure,
                },
            )
            await self.db.commit()

        summary.update(await self._facility_extraction_metric_metadata(execution.id))
        await execution_service.emit_event(
            self.db,
            execution.id,
            "facility_extraction_phase_completed",
            "Facility extraction phase completed; staging candidates are ready for publication.",
            metadata=summary,
        )
        await self.db.commit()
        self._log(
            "facility_extraction_phase_completed",
            execution_id=execution.id,
            **summary,
        )

    async def _run_facility_publication_phase(self, execution: ScrapingExecution) -> None:
        self.current_stage = "facility_publication"
        settings = get_settings()
        if not settings.facility_extraction_enabled:
            await execution_service.emit_event(
                self.db,
                execution.id,
                "facility_publication_phase_disabled",
                "Facility publication skipped because extraction is disabled.",
                metadata={"enabled": False, "reason": "extraction_disabled"},
            )
            await self.db.commit()
            return
        if not settings.facility_publication_enabled:
            await execution_service.emit_event(
                self.db,
                execution.id,
                "facility_publication_phase_disabled",
                "Facility publication phase skipped because it is disabled.",
                metadata={"enabled": False},
            )
            await self.db.commit()
            self._log(
                "facility_publication_phase_disabled",
                execution_id=execution.id,
                enabled=False,
            )
            return

        await execution_service.emit_event(
            self.db,
            execution.id,
            "facility_publication_phase_started",
            "Facility publication phase started for verified staging candidates.",
            metadata={
                "max_candidates": self.scale_profile.publication_max_candidates,
                "min_confidence": settings.facility_publication_min_confidence,
                "mode": self.scale_profile.mode,
            },
        )
        await self.db.commit()

        summary = await facility_candidate_publication_service.publish_execution_candidates(
            self.db,
            organization_id=execution.organization_id,
            execution_id=execution.id,
            max_candidates=self.scale_profile.publication_max_candidates,
        )
        await execution_service.emit_event(
            self.db,
            execution.id,
            "facility_publication_phase_completed",
            "Facility publication phase completed.",
            metadata=summary,
        )
        await self.db.commit()
        self._log(
            "facility_publication_phase_completed",
            execution_id=execution.id,
            **summary,
        )

    async def _process_discovery_task(
        self, execution: ScrapingExecution, task: ScrapingTask
    ) -> None:
        agent = task.execution_agent
        await self._start_task(
            execution,
            task,
            agent,
            agent_action="Running real source discovery",
            task_action="Planning and searching real source candidates",
        )
        await execution_service.emit_event(
            self.db,
            execution.id,
            "discovery_started",
            "Real source discovery started for this coverage cell.",
            execution_agent_id=agent.id,
            task_id=task.id,
            coverage_cell_id=task.coverage_cell_id,
        )

        context = self._source_discovery_context(execution, task)
        summary = await source_discovery_service.discover(self.db, context)
        output = await self._task_output(task, summary)
        selected_count = await self._create_retrieval_tasks(execution, task)
        output["selected_retrieval_candidate_count"] = selected_count
        output["max_retrieval_estimate"] = await self._max_retrieval_estimate(execution.id)
        task.output_json = output
        await self._emit_discovery_outcome_events(execution, task, agent, output)

        task.status = ScrapingTaskStatus.COMPLETED
        task.completed_at = datetime.now(UTC)
        task.current_action = None
        if task.coverage_cell:
            self._complete_discovery_cell(task.coverage_cell, task)
        self._complete_agent(agent)
        await execution_service.emit_event(
            self.db,
            execution.id,
            "task_completed",
            f"{task.title} completed with real source discovery output.",
            execution_agent_id=agent.id,
            task_id=task.id,
            coverage_cell_id=task.coverage_cell_id,
            metadata={
                "task_type": task.task_type,
                "selected_retrieval_candidate_count": selected_count,
                "max_retrieval_estimate": output["max_retrieval_estimate"],
            },
        )
        await self.db.commit()

    async def _process_retrieval_task(
        self, execution: ScrapingExecution, task: ScrapingTask
    ) -> None:
        agent = task.execution_agent
        source_candidate_id = (task.input_json or {}).get("source_candidate_id")
        if not isinstance(source_candidate_id, str) or not source_candidate_id:
            task.status = ScrapingTaskStatus.FAILED
            task.completed_at = datetime.now(UTC)
            task.error_message = "Retrieval task is missing a persisted source candidate ID."
            await self.db.commit()
            return
        await self._check_cancelled(execution)
        candidate = (
            await self.db.execute(
                select(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.id == source_candidate_id,
                    ScrapingSourceCandidate.execution_id == execution.id,
                )
            )
        ).scalar_one_or_none()
        candidate_url = candidate.canonical_url if candidate else None
        await self._start_task(
            execution,
            task,
            agent,
            agent_action="Retrieving a persisted source candidate",
            task_action=(
                f"Opening {candidate_url}"
                if candidate_url
                else "Securely retrieving persisted source candidate"
            ),
        )
        await execution_service.emit_event(
            self.db,
            execution.id,
            "source_retrieval_started",
            (
                f"Opening website: {candidate_url}"
                if candidate_url
                else "Secure retrieval started for a persisted source candidate."
            ),
            execution_agent_id=agent.id,
            task_id=task.id,
            coverage_cell_id=task.coverage_cell_id,
            metadata={
                "candidate_id": source_candidate_id,
                "canonical_url": candidate_url,
                "title": candidate.title if candidate else None,
                "domain": candidate.domain if candidate else None,
            },
        )
        max_attempts = max(1, min(task.max_attempts or 1, 3))
        summary: SourceRetrievalSummary | None = None
        for attempt_number in range(1, max_attempts + 1):
            await self._check_cancelled(execution)
            task.attempt_count = attempt_number
            summary = await source_retrieval_service.retrieve(
                self.db,
                SourceRetrievalContext(
                    organization_id=execution.organization_id,
                    execution_id=execution.id,
                    source_candidate_id=source_candidate_id,
                    coverage_cell_id=task.coverage_cell_id,
                    task_id=task.id,
                    idempotency_key=self._retrieval_attempt_key(
                        execution.id, task.id, source_candidate_id, attempt_number
                    ),
                ),
            )
            task.output_json = self._safe_retrieval_task_output(summary, source_candidate_id)
            await self._emit_retrieval_outcome_event(execution, task, agent, summary)
            retryable = self._is_retryable_retrieval_summary(summary)
            if summary.status == SourceRetrievalAttemptStatus.SUCCEEDED.value or not retryable:
                break

        task.status = ScrapingTaskStatus.COMPLETED
        task.completed_at = datetime.now(UTC)
        task.current_action = None
        self._complete_agent(agent)
        await execution_service.emit_event(
            self.db,
            execution.id,
            "task_completed",
            f"{task.title} completed with secure retrieval output.",
            execution_agent_id=agent.id,
            task_id=task.id,
            coverage_cell_id=task.coverage_cell_id,
            metadata={"task_type": task.task_type, **(task.output_json or {})},
        )
        await self.db.commit()

    async def _process_audit_task(
        self, execution: ScrapingExecution, task: ScrapingTask
    ) -> None:
        task.status = ScrapingTaskStatus.COMPLETED
        task.started_at = task.started_at or datetime.now(UTC)
        task.completed_at = task.completed_at or datetime.now(UTC)
        await self.db.commit()

    async def _start_task(
        self,
        execution: ScrapingExecution,
        task: ScrapingTask,
        agent: ScrapingExecutionAgent,
        *,
        agent_action: str,
        task_action: str,
    ) -> None:
        agent.status = ScrapingExecutionAgentStatus.RUNNING
        agent.current_task_id = task.id
        agent.current_action = agent_action
        agent.started_at = agent.started_at or datetime.now(UTC)
        task.status = ScrapingTaskStatus.RUNNING
        task.started_at = task.started_at or datetime.now(UTC)
        task.current_action = task_action
        if task.coverage_cell and task.coverage_cell.status == ScrapingCoverageStatus.NOT_STARTED:
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
            metadata={"task_type": task.task_type},
        )
        await self.db.commit()

    def _complete_agent(self, agent: ScrapingExecutionAgent) -> None:
        agent.status = ScrapingExecutionAgentStatus.COMPLETED
        agent.current_task_id = None
        agent.current_action = None
        agent.completed_at = datetime.now(UTC)

    def _source_discovery_context(
        self, execution: ScrapingExecution, task: ScrapingTask
    ) -> SourceDiscoveryContext:
        if task.coverage_cell is None:
            raise RuntimeError("discover_sources_task_missing_coverage_cell")
        blueprint_json = execution.blueprint.blueprint_json or {}
        profile = self.scale_profile
        region_code = task.coverage_cell.region_code
        language_code = (task.coverage_cell.language_code or "und")[:16]
        return SourceDiscoveryContext(
            organization_id=execution.organization_id,
            execution_id=execution.id,
            coverage_cell_id=task.coverage_cell.id,
            task_id=task.id,
            country_code=(execution.country_code or "XX")[:2].upper(),
            country_name=(execution.country_name or "Unknown")[:120],
            region_code=(region_code[:32] if region_code else None),
            region_name=(task.coverage_cell.region_name or "National")[:160],
            language_code=language_code or "und",
            language_name=(task.coverage_cell.language_name or language_code or "und")[:120],
            source_category=(task.coverage_cell.source_category or "directory")[:120],
            mission_goal=self._mission_goal(blueprint_json)[:2000],
            requested_fields=self._requested_fields(blueprint_json)[:50],
            blueprint_context=blueprint_json if isinstance(blueprint_json, dict) else {},
            provider=get_settings().source_discovery_provider,
            max_queries_per_discovery=profile.serper_max_queries_per_discovery,
            results_per_query=profile.serper_results_per_query,
            discovery_query_hard_cap=profile.discovery_query_hard_cap,
            discovery_results_hard_cap=profile.discovery_results_hard_cap,
        )

    async def _task_output(
        self, task: ScrapingTask, summary: SourceDiscoverySummary
    ) -> dict[str, Any]:
        query_result = await self.db.execute(
            select(ScrapingSourceDiscoveryQuery).where(
                ScrapingSourceDiscoveryQuery.task_id == task.id
            )
        )
        queries = list(query_result.scalars().all())
        candidate_result = await self.db.execute(
            select(ScrapingSourceCandidate).where(
                ScrapingSourceCandidate.coverage_cell_id == task.coverage_cell_id
            )
        )
        candidates = list(candidate_result.scalars().all())
        error_codes = sorted(
            {
                query.error_code
                for query in queries
                if query.status == SourceDiscoveryQueryStatus.FAILED and query.error_code
            }
        )
        return {
            "phase": "source_discovery",
            "provider": summary.provider,
            "query_count": summary.query_count,
            "successful_query_count": summary.succeeded_query_count,
            "failed_query_count": summary.failed_query_count,
            "zero_result_query_count": len(
                [
                    query
                    for query in queries
                    if query.status == SourceDiscoveryQueryStatus.SUCCEEDED
                    and query.result_count == 0
                ]
            ),
            "candidate_count": len(candidates),
            "unique_domain_count": len({candidate.domain for candidate in candidates}),
            "error_codes": error_codes,
            "rejected_result_count": summary.rejected_result_count,
            "duplicate_candidate_count": summary.duplicate_candidate_count,
        }

    async def _create_retrieval_tasks(
        self, execution: ScrapingExecution, discovery_task: ScrapingTask
    ) -> int:
        if discovery_task.coverage_cell_id is None:
            return 0
        selected = await self._select_retrieval_candidates(
            execution.id, discovery_task.coverage_cell_id
        )
        if not selected:
            return 0
        existing_result = await self.db.execute(
            select(ScrapingTask).where(
                ScrapingTask.execution_id == execution.id,
                ScrapingTask.task_type == "retrieve_source",
                ScrapingTask.coverage_cell_id == discovery_task.coverage_cell_id,
            )
        )
        existing_candidate_ids = {
            (task.input_json or {}).get("source_candidate_id")
            for task in existing_result.scalars().all()
        }
        created = 0
        for index, candidate in enumerate(selected, start=1):
            if candidate.id in existing_candidate_ids:
                continue
            idempotency_key = self._retrieval_task_key(
                execution.id, discovery_task.coverage_cell_id, candidate.id
            )
            self.db.add(
                ScrapingTask(
                    execution_id=execution.id,
                    execution_agent_id=discovery_task.execution_agent_id,
                    coverage_cell_id=discovery_task.coverage_cell_id,
                    parent_task_id=discovery_task.id,
                    task_type="retrieve_source",
                    title=f"Retrieve source candidate {candidate.title[:120] or candidate.domain}",
                    status=ScrapingTaskStatus.QUEUED,
                    priority=discovery_task.priority + 10 + index,
                    max_attempts=3,
                    input_json={
                        "source_candidate_id": candidate.id,
                        "idempotency_key": idempotency_key,
                        "phase": "source_retrieval",
                    },
                    output_json={},
                    dependency_task_ids_json=[discovery_task.id],
                )
            )
            existing_candidate_ids.add(candidate.id)
            created += 1
        if created:
            await self.db.flush()
            await execution_service.emit_event(
                self.db,
                execution.id,
                "task_queued",
                f"{created} secure source retrieval tasks queued.",
                execution_agent_id=discovery_task.execution_agent_id,
                task_id=discovery_task.id,
                coverage_cell_id=discovery_task.coverage_cell_id,
                metadata={
                    "task_type": "retrieve_source",
                    "selected_retrieval_candidate_count": created,
                    "max_retrieval_estimate": await self._max_retrieval_estimate(execution.id),
                },
            )
            await self.db.commit()
        return created

    async def _select_retrieval_candidates(
        self, execution_id: str, coverage_cell_id: str
    ) -> list[ScrapingSourceCandidate]:
        per_cell_limit = max(self.scale_profile.retrieval_max_per_cell, 0)
        per_execution_limit = max(self.scale_profile.retrieval_max_per_execution, 0)
        if per_cell_limit == 0 or per_execution_limit == 0:
            return []
        existing_execution_tasks = await self._retrieval_task_count(execution_id)
        remaining = max(per_execution_limit - existing_execution_tasks, 0)
        if remaining == 0:
            return []
        result = await self.db.execute(
            select(ScrapingSourceCandidate).where(
                ScrapingSourceCandidate.execution_id == execution_id,
                ScrapingSourceCandidate.coverage_cell_id == coverage_cell_id,
                ScrapingSourceCandidate.status.in_(
                    [SourceCandidateStatus.DISCOVERED, SourceCandidateStatus.ACCEPTED]
                ),
            )
        )
        candidates = list(result.scalars().all())
        unique_by_url: dict[str, ScrapingSourceCandidate] = {}
        for candidate in sorted(candidates, key=self._candidate_sort_key):
            unique_by_url.setdefault(candidate.canonical_url, candidate)
        sorted_candidates = list(unique_by_url.values())
        limit = min(per_cell_limit, remaining)
        selected: list[ScrapingSourceCandidate] = []
        seen_domains: set[str] = set()
        for candidate in sorted_candidates:
            if candidate.domain in seen_domains:
                continue
            selected.append(candidate)
            seen_domains.add(candidate.domain)
            if len(selected) >= limit:
                return selected
        selected_ids = {candidate.id for candidate in selected}
        for candidate in sorted_candidates:
            if candidate.id in selected_ids:
                continue
            selected.append(candidate)
            if len(selected) >= limit:
                break
        return selected

    def _candidate_sort_key(self, candidate: ScrapingSourceCandidate) -> tuple[int, int, int, str, str]:
        return (
            _trust_tier_rank(candidate.initial_trust_tier),
            candidate.rank,
            0 if _is_official_source_category(candidate.source_category) else 1,
            candidate.canonical_url,
            candidate.id,
        )

    async def _retrieval_task_count(self, execution_id: str) -> int:
        result = await self.db.execute(
            select(ScrapingTask.id).where(
                ScrapingTask.execution_id == execution_id,
                ScrapingTask.task_type == "retrieve_source",
            )
        )
        return len(result.scalars().all())

    async def _max_retrieval_estimate(self, execution_id: str) -> int:
        cell_count = await self._coverage_count(execution_id)
        return min(
            cell_count * max(self.scale_profile.retrieval_max_per_cell, 0),
            max(self.scale_profile.retrieval_max_per_execution, 0),
        )

    async def _emit_discovery_outcome_events(
        self,
        execution: ScrapingExecution,
        task: ScrapingTask,
        agent: ScrapingExecutionAgent,
        output: dict[str, Any],
    ) -> None:
        await execution_service.emit_event(
            self.db,
            execution.id,
            "discovery_query_completed",
            (
                f"{output['successful_query_count']} discovery queries succeeded; "
                f"{output['failed_query_count']} failed."
            ),
            execution_agent_id=agent.id,
            task_id=task.id,
            coverage_cell_id=task.coverage_cell_id,
            metadata={
                "provider": output["provider"],
                "query_count": output["query_count"],
                "successful_query_count": output["successful_query_count"],
                "failed_query_count": output["failed_query_count"],
                "error_codes": output["error_codes"],
            },
        )
        if int(output.get("candidate_count") or 0) > 0:
            await execution_service.emit_event(
                self.db,
                execution.id,
                "source_candidates_discovered",
                f"{output['candidate_count']} real source candidates discovered.",
                execution_agent_id=agent.id,
                task_id=task.id,
                coverage_cell_id=task.coverage_cell_id,
                metadata={
                    "candidate_count": output["candidate_count"],
                    "unique_domain_count": output["unique_domain_count"],
                },
            )
        elif int(output.get("failed_query_count") or 0) == 0:
            await execution_service.emit_event(
                self.db,
                execution.id,
                "discovery_zero_results",
                "Real discovery queries completed with zero source candidates.",
                execution_agent_id=agent.id,
                task_id=task.id,
                coverage_cell_id=task.coverage_cell_id,
                metadata={"zero_result_query_count": output["zero_result_query_count"]},
            )
        else:
            await execution_service.emit_event(
                self.db,
                execution.id,
                "discovery_failed",
                "Real source discovery failed for this coverage cell.",
                execution_agent_id=agent.id,
                task_id=task.id,
                coverage_cell_id=task.coverage_cell_id,
                metadata={"error_codes": output["error_codes"]},
            )

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
                title="Audit remaining source discovery coverage debt",
                status=ScrapingTaskStatus.COMPLETED,
                priority=1000,
                input_json={"coverage_debt": debt, "phase": "source_discovery"},
                output_json={"gap_tasks_created": debt, "phase": "source_discovery"},
                dependency_task_ids_json=[],
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
            )
        )
        await execution_service.emit_event(
            self.db,
            execution.id,
            "coverage_gap_detected",
            f"Coverage audit detected {debt} incomplete source discovery coverage cells.",
            execution_agent_id=agent.id,
        )
        await self.db.commit()

    def _safe_retrieval_task_output(
        self, summary: SourceRetrievalSummary, source_candidate_id: str
    ) -> dict[str, Any]:
        return {
            "phase": "source_retrieval",
            "source_candidate_id": source_candidate_id,
            "status": summary.status,
            "final_hostname": _hostname(summary.final_url),
            "http_status": summary.http_status,
            "content_type": summary.content_type,
            "bytes_received": summary.bytes_received,
            "redirect_count": summary.redirect_count,
            "robots_status": summary.robots_status,
            "failure_classification": summary.failure_classification,
            "document_id": summary.document_id,
            "content_hash_prefix": (
                summary.content_sha256[:12] if summary.content_sha256 else None
            ),
        }

    async def _emit_retrieval_outcome_event(
        self,
        execution: ScrapingExecution,
        task: ScrapingTask,
        agent: ScrapingExecutionAgent,
        summary: SourceRetrievalSummary,
    ) -> None:
        metadata = self._safe_retrieval_task_output(
            summary, str((task.input_json or {}).get("source_candidate_id") or "")
        )
        if summary.status == SourceRetrievalAttemptStatus.SUCCEEDED.value:
            event_type = "source_retrieval_succeeded"
            message = "Secure source retrieval succeeded."
        elif summary.status == SourceRetrievalAttemptStatus.BLOCKED_BY_ROBOTS.value:
            event_type = "source_retrieval_blocked"
            message = "Secure source retrieval was blocked by robots policy."
        elif summary.status == SourceRetrievalAttemptStatus.UNSUPPORTED_CONTENT_TYPE.value:
            event_type = "source_retrieval_unsupported"
            message = "Secure source retrieval found an unsupported content type."
        else:
            event_type = "source_retrieval_failed"
            message = "Secure source retrieval did not produce a source document."
        await execution_service.emit_event(
            self.db,
            execution.id,
            event_type,
            message,
            execution_agent_id=agent.id,
            task_id=task.id,
            coverage_cell_id=task.coverage_cell_id,
            metadata=metadata,
        )
        if summary.document_id:
            await execution_service.emit_event(
                self.db,
                execution.id,
                "source_document_persisted",
                "Retrieved source document persisted for later extraction.",
                execution_agent_id=agent.id,
                task_id=task.id,
                coverage_cell_id=task.coverage_cell_id,
                metadata=metadata,
            )

    def _is_retryable_retrieval_summary(self, summary: SourceRetrievalSummary) -> bool:
        if (
            summary.status == SourceRetrievalAttemptStatus.BLOCKED_BY_ROBOTS.value
            and summary.robots_status == "unavailable"
        ):
            return True
        if summary.status in RETRYABLE_RETRIEVAL_STATUSES:
            return True
        if summary.status in NON_RETRYABLE_RETRIEVAL_STATUSES:
            return False
        return False

    def _retrieval_task_key(
        self, execution_id: str, coverage_cell_id: str, source_candidate_id: str
    ) -> str:
        digest = hashlib.sha256(
            f"{execution_id}:{coverage_cell_id}:{source_candidate_id}".encode("utf-8")
        ).hexdigest()
        return f"retrieve_source:{digest}"

    def _retrieval_attempt_key(
        self,
        execution_id: str,
        task_id: str,
        source_candidate_id: str,
        attempt_number: int,
    ) -> str:
        digest = hashlib.sha256(
            f"{execution_id}:{task_id}:{source_candidate_id}:{attempt_number}".encode("utf-8")
        ).hexdigest()
        return f"retrieve_source_attempt:{digest}"

    def _facility_extraction_attempt_key(
        self,
        execution_id: str,
        source_document_id: str,
        chunk_id: str,
    ) -> str:
        digest = hashlib.sha256(
            f"{execution_id}:{source_document_id}:{chunk_id}".encode("utf-8")
        ).hexdigest()
        return f"facility_extract:{digest}"

    async def _source_documents_for_extraction(
        self, execution: ScrapingExecution
    ) -> list[ScrapingSourceDocument]:
        result = await self.db.execute(
            select(ScrapingSourceDocument).where(
                ScrapingSourceDocument.organization_id == execution.organization_id,
                ScrapingSourceDocument.execution_id == execution.id,
            )
        )
        return sorted(
            result.scalars().all(),
            key=lambda document: (
                document.retrieval_timestamp,
                document.source_candidate_id,
                document.id,
            ),
        )

    async def _chunks_for_prepared_text(
        self, prepared_text_id: str
    ) -> list[ScrapingSourceDocumentChunk]:
        result = await self.db.execute(
            select(ScrapingSourceDocumentChunk)
            .where(ScrapingSourceDocumentChunk.prepared_text_id == prepared_text_id)
            .order_by(ScrapingSourceDocumentChunk.chunk_index)
        )
        return list(result.scalars().all())

    async def _reconcile_coverage_after_retrieval(self, execution: ScrapingExecution) -> None:
        cells = await self._coverage_cells(execution.id)
        for cell in cells:
            candidate_count = await self._candidate_count_for_cell(execution.id, cell.id)
            if candidate_count == 0:
                continue
            attempts = await self._retrieval_attempts_for_cell(execution.id, cell.id)
            document_count = await self._document_count_for_cell(execution.id, cell.id)
            cell.result_count = candidate_count
            cell.completed_at = datetime.now(UTC)
            if document_count > 0:
                failed_count = len(
                    [
                        attempt
                        for attempt in attempts
                        if attempt.status != SourceRetrievalAttemptStatus.SUCCEEDED
                    ]
                )
                cell.status = ScrapingCoverageStatus.PARTIALLY_COVERED
                cell.reason = (
                    "Real source pages were retrieved and stored for facility extraction."
                    if failed_count == 0
                    else (
                        "Some real source pages were retrieved and stored; remaining candidates "
                        "have retrieval debt before extraction."
                    )
                )
            elif attempts and all(
                attempt.status == SourceRetrievalAttemptStatus.BLOCKED_BY_ROBOTS
                for attempt in attempts
            ):
                cell.status = ScrapingCoverageStatus.BLOCKED
                cell.reason = "All selected real source candidates were blocked by robots policy."
            elif attempts:
                cell.status = ScrapingCoverageStatus.FAILED
                cell.reason = "Selected real source candidates did not produce retrievable source documents."
            elif cell.status != ScrapingCoverageStatus.COVERED_NO_RESULTS:
                cell.status = ScrapingCoverageStatus.FAILED
                cell.reason = (
                    "Real source candidates were discovered but none were selected for retrieval "
                    "within configured limits."
                )
        await execution_service.emit_event(
            self.db,
            execution.id,
            "retrieval_phase_completed",
            "Bounded secure retrieval phase completed and coverage was reconciled.",
            metadata=await self._retrieval_metric_metadata(execution.id),
        )
        await self.db.commit()

    def _complete_discovery_cell(self, cell: ScrapingCoverageCell, task: ScrapingTask) -> None:
        output = task.output_json or {}
        candidate_count = int(output.get("candidate_count") or 0)
        query_count = int(output.get("query_count") or 0)
        successful_query_count = int(output.get("successful_query_count") or 0)
        failed_query_count = int(output.get("failed_query_count") or 0)
        error_codes = set(output.get("error_codes") or [])
        result_count = candidate_count
        cell.result_count = result_count
        cell.completed_at = datetime.now(UTC)
        if candidate_count > 0:
            cell.status = ScrapingCoverageStatus.IN_PROGRESS
            cell.reason = (
                "Real source candidates were discovered and queued for bounded secure retrieval."
            )
        elif query_count > 0 and successful_query_count == query_count:
            cell.status = ScrapingCoverageStatus.COVERED_NO_RESULTS
            cell.result_count = 0
            cell.reason = "Real discovery queries completed but produced no source candidates."
        elif error_codes & {"configuration_missing", "authentication_failed", "rate_limited"}:
            cell.status = ScrapingCoverageStatus.BLOCKED
            cell.reason = "Provider configuration, authentication, or rate limits blocked discovery."
        elif failed_query_count > 0:
            cell.status = ScrapingCoverageStatus.FAILED
            cell.reason = "Source discovery failed before usable candidates were found."
        else:
            cell.status = ScrapingCoverageStatus.FAILED
            cell.reason = "Source discovery ended without a clear successful outcome."

    async def _candidate_count_for_cell(self, execution_id: str, coverage_cell_id: str) -> int:
        result = await self.db.execute(
            select(ScrapingSourceCandidate.id).where(
                ScrapingSourceCandidate.execution_id == execution_id,
                ScrapingSourceCandidate.coverage_cell_id == coverage_cell_id,
            )
        )
        return len(result.scalars().all())

    async def _retrieval_attempts_for_cell(
        self, execution_id: str, coverage_cell_id: str
    ) -> list[ScrapingSourceRetrievalAttempt]:
        result = await self.db.execute(
            select(ScrapingSourceRetrievalAttempt).where(
                ScrapingSourceRetrievalAttempt.execution_id == execution_id,
                ScrapingSourceRetrievalAttempt.coverage_cell_id == coverage_cell_id,
            )
        )
        return list(result.scalars().all())

    async def _document_count_for_cell(self, execution_id: str, coverage_cell_id: str) -> int:
        result = await self.db.execute(
            select(ScrapingSourceDocument.id)
            .join(
                ScrapingSourceCandidate,
                ScrapingSourceCandidate.id == ScrapingSourceDocument.source_candidate_id,
            )
            .where(
                ScrapingSourceDocument.execution_id == execution_id,
                ScrapingSourceCandidate.coverage_cell_id == coverage_cell_id,
            )
        )
        return len(result.scalars().all())

    async def _refresh_metrics(self, execution: ScrapingExecution) -> None:
        candidate_count = len(
            (
                await self.db.execute(
                    select(ScrapingSourceCandidate.id).where(
                        ScrapingSourceCandidate.execution_id == execution.id
                    )
                )
            ).scalars().all()
        )
        retrieval_metadata = await self._retrieval_metric_metadata(execution.id)
        staging_candidates = len(
            (
                await self.db.execute(
                    select(ScrapingFacilityCandidate.id).where(
                        ScrapingFacilityCandidate.execution_id == execution.id
                    )
                )
            ).scalars().all()
        )
        published_facilities = len(
            (
                await self.db.execute(
                    select(RehabilitationFacility.id).where(
                        RehabilitationFacility.execution_id == execution.id,
                        RehabilitationFacility.is_mock.is_(False),
                    )
                )
            ).scalars().all()
        )
        published_rows = len(
            (
                await self.db.execute(
                    select(ScrapingFacilityCandidatePublication.id).where(
                        ScrapingFacilityCandidatePublication.execution_id == execution.id,
                        ScrapingFacilityCandidatePublication.status
                        == FacilityCandidatePublicationStatus.PUBLISHED,
                    )
                )
            ).scalars().all()
        )
        duplicates = len(
            (
                await self.db.execute(
                    select(RehabilitationPossibleDuplicate.id).where(
                        RehabilitationPossibleDuplicate.execution_id == execution.id,
                        RehabilitationPossibleDuplicate.is_mock.is_(False),
                    )
                )
            ).scalars().all()
        )
        execution.sources_discovered = candidate_count
        execution.documents_found = int(retrieval_metadata["source_document_count"])
        execution.records_extracted = staging_candidates
        execution.records_verified = published_facilities or published_rows
        execution.duplicates_detected = duplicates
        execution.blocked_sources = int(retrieval_metadata["blocked_retrieval_count"])
        execution.coverage_debt = await self._coverage_debt(execution.id)

    async def _retrieval_metric_metadata(self, execution_id: str) -> dict[str, Any]:
        attempts = (
            await self.db.execute(
                select(ScrapingSourceRetrievalAttempt).where(
                    ScrapingSourceRetrievalAttempt.execution_id == execution_id
                )
            )
        ).scalars().all()
        documents = (
            await self.db.execute(
                select(ScrapingSourceDocument).where(
                    ScrapingSourceDocument.execution_id == execution_id
                )
            )
        ).scalars().all()
        retrieval_tasks = (
            await self.db.execute(
                select(ScrapingTask).where(
                    ScrapingTask.execution_id == execution_id,
                    ScrapingTask.task_type == "retrieve_source",
                )
            )
        ).scalars().all()
        unique_domains = {
            hostname
            for hostname in (_hostname(document.final_url) for document in documents)
            if hostname
        }
        return {
            "selected_retrieval_candidate_count": len(retrieval_tasks),
            "retrieval_attempt_count": len(attempts),
            "successful_retrieval_count": len(
                [
                    attempt
                    for attempt in attempts
                    if attempt.status == SourceRetrievalAttemptStatus.SUCCEEDED
                ]
            ),
            "blocked_retrieval_count": len(
                [
                    attempt
                    for attempt in attempts
                    if attempt.status == SourceRetrievalAttemptStatus.BLOCKED_BY_ROBOTS
                ]
            ),
            "unsupported_retrieval_count": len(
                [
                    attempt
                    for attempt in attempts
                    if attempt.status == SourceRetrievalAttemptStatus.UNSUPPORTED_CONTENT_TYPE
                ]
            ),
            "failed_retrieval_count": len(
                [
                    attempt
                    for attempt in attempts
                    if attempt.status
                    not in {
                        SourceRetrievalAttemptStatus.SUCCEEDED,
                        SourceRetrievalAttemptStatus.BLOCKED_BY_ROBOTS,
                        SourceRetrievalAttemptStatus.UNSUPPORTED_CONTENT_TYPE,
                    }
                ]
            ),
            "source_document_count": len(documents),
            "unique_retrieved_domains": len(unique_domains),
            "total_downloaded_bytes": sum(document.byte_size or 0 for document in documents),
        }

    async def _facility_extraction_metric_metadata(self, execution_id: str) -> dict[str, Any]:
        prepared_count = len(
            (
                await self.db.execute(
                    select(ScrapingSourceDocumentText.id).where(
                        ScrapingSourceDocumentText.execution_id == execution_id
                    )
                )
            ).scalars().all()
        )
        chunk_count = len(
            (
                await self.db.execute(
                    select(ScrapingSourceDocumentChunk.id).where(
                        ScrapingSourceDocumentChunk.execution_id == execution_id
                    )
                )
            ).scalars().all()
        )
        attempt_count = len(
            (
                await self.db.execute(
                    select(ScrapingFacilityExtractionAttempt.id).where(
                        ScrapingFacilityExtractionAttempt.execution_id == execution_id
                    )
                )
            ).scalars().all()
        )
        candidate_count = len(
            (
                await self.db.execute(
                    select(ScrapingFacilityCandidate.id).where(
                        ScrapingFacilityCandidate.execution_id == execution_id
                    )
                )
            ).scalars().all()
        )
        evidence_count = len(
            (
                await self.db.execute(
                    select(ScrapingFacilityCandidateEvidence.id).where(
                        ScrapingFacilityCandidateEvidence.execution_id == execution_id
                    )
                )
            ).scalars().all()
        )
        return {
            "prepared_text_total_count": prepared_count,
            "chunk_total_count": chunk_count,
            "extraction_attempt_total_count": attempt_count,
            "staging_candidate_total_count": candidate_count,
            "accepted_evidence_total_count": evidence_count,
        }

    async def _finish_cancelled(self, execution: ScrapingExecution) -> None:
        await execution_service._cancel_pending_children(self.db, execution.id)
        execution.status = ScrapingExecutionStatus.CANCELLED
        execution.completed_at = datetime.now(UTC)
        await execution_service.emit_event(
            self.db, execution.id, "execution_cancelled", "Source discovery execution cancelled."
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

    async def _mark_failed_safely(
        self,
        execution_id: str,
        failure_category: str,
        *,
        error_message: str = "Source discovery execution failed.",
        event_message: str = "Source discovery execution failed.",
    ) -> None:
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
            execution.error_message = error_message
            execution.completed_at = datetime.now(UTC)
            await self._terminalize_failed_children(execution_id, error_message)
            execution.coverage_debt = await self._coverage_debt(execution_id)
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
                    event_message,
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

    async def _terminalize_failed_children(self, execution_id: str, error_message: str) -> None:
        now = datetime.now(UTC)
        await self.db.execute(
            update(ScrapingExecutionAgent)
            .where(
                ScrapingExecutionAgent.execution_id == execution_id,
                ScrapingExecutionAgent.status.in_(
                    [
                        ScrapingExecutionAgentStatus.WAITING,
                        ScrapingExecutionAgentStatus.QUEUED,
                        ScrapingExecutionAgentStatus.RUNNING,
                    ]
                ),
            )
            .values(
                status=ScrapingExecutionAgentStatus.FAILED,
                current_task_id=None,
                current_action=None,
                completed_at=now,
                error_message=error_message,
            )
        )
        await self.db.execute(
            update(ScrapingTask)
            .where(
                ScrapingTask.execution_id == execution_id,
                ScrapingTask.status.in_(
                    [
                        ScrapingTaskStatus.QUEUED,
                        ScrapingTaskStatus.BLOCKED,
                        ScrapingTaskStatus.RUNNING,
                    ]
                ),
            )
            .values(
                status=ScrapingTaskStatus.FAILED,
                current_action=None,
                completed_at=now,
                error_message=error_message,
            )
        )

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
                    [ScrapingCoverageStatus(status) for status in GAP_COVERAGE_STATUSES]
                ),
            )
        )
        return len(result.scalars().all())

    def _coverage_dimensions_from_blueprint(
        self,
        blueprint_json: dict,
        country_code: str,
        country_name: str,
    ) -> tuple[list[dict[str, str | None]], list[dict[str, str | None]], list[str]]:
        scope = blueprint_json.get("scope") if isinstance(blueprint_json, dict) else {}
        raw_regions = scope.get("regions") if isinstance(scope, dict) else None
        raw_languages = blueprint_json.get("languages") if isinstance(blueprint_json, dict) else None
        source_strategy = blueprint_json.get("source_strategy") if isinstance(blueprint_json, dict) else None

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
            raise CoverageDimensionError("Approved blueprint has no usable scope.regions.")

        languages: list[dict[str, str | None]] = []
        seen_languages: set[tuple[str, str]] = set()
        for raw_language in raw_languages if isinstance(raw_languages, list) else []:
            if isinstance(raw_language, dict):
                name = self._clean_text(raw_language.get("name"))
                code = self._coverage_language_code(raw_language.get("code"), name)
            else:
                name = self._clean_text(raw_language)
                code = self._coverage_language_code(None, name)
            if not name:
                continue
            identity = (code.casefold() if code else "", name.casefold())
            if identity in seen_languages:
                continue
            seen_languages.add(identity)
            languages.append({"code": code, "name": name})
        if not languages:
            raise CoverageDimensionError("Approved blueprint has no usable languages.")

        categories: list[str] = []
        seen_categories: set[str] = set()
        for raw_item in source_strategy if isinstance(source_strategy, list) else []:
            if isinstance(raw_item, dict):
                category = self._clean_text(raw_item.get("source_type"))
            else:
                category = self._clean_text(raw_item)
            if not category:
                continue
            # Coverage + discovery context fields are capped (DB/API max 120).
            category = category[:120].rstrip()
            identity = category.casefold()
            if identity in seen_categories:
                continue
            seen_categories.add(identity)
            categories.append(category)
        if not categories:
            raise CoverageDimensionError("Approved blueprint has no usable source_strategy entries.")

        return regions, languages, categories

    def _mission_goal(self, blueprint_json: dict[str, Any]) -> str:
        mission_summary = blueprint_json.get("mission_summary")
        if isinstance(mission_summary, dict):
            goal = self._clean_text(mission_summary.get("goal"))
            if goal:
                return goal
        return "Discover real candidate source URLs for this scraping mission."

    def _requested_fields(self, blueprint_json: dict[str, Any]) -> list[str]:
        schema = blueprint_json.get("data_schema")
        fields: list[str] = []
        for raw_field in schema if isinstance(schema, list) else []:
            if isinstance(raw_field, dict):
                field_name = self._clean_text(raw_field.get("field_name"))
            else:
                field_name = self._clean_text(raw_field)
            if field_name:
                fields.append(field_name)
        return fields

    def _coverage_region_code(self, value: str | None, country_code: str) -> str:
        text = self._clean_text(value) or self._clean_text(country_code) or "region"
        slug = "-".join(text.lower().replace("_", " ").split())
        slug = "".join(char for char in slug if char.isalnum() or char == "-").strip("-")
        slug = slug or "region"
        if len(slug) <= 32:
            return slug
        digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:8]
        return f"{slug[:23].rstrip('-')}-{digest}"[:32]

    def _coverage_language_code(self, code: object, name: str) -> str:
        explicit_code = self._clean_text(code)
        if explicit_code:
            return self._bounded_language_code(explicit_code)

        normalized_name = " ".join(name.casefold().split())
        mapped_code = LANGUAGE_CODE_BY_NAME.get(normalized_name)
        if mapped_code:
            return mapped_code

        return self._bounded_language_code(name or "language")

    def _bounded_language_code(self, value: str) -> str:
        slug = "-".join(value.lower().replace("_", " ").split())
        slug = "".join(char for char in slug if char.isalnum() or char == "-").strip("-")
        slug = slug or "language"
        if len(slug) <= 16:
            return slug
        digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:6]
        return f"{slug[:9].rstrip('-')}-{digest}"[:16]

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


def _trust_tier_rank(value: str | None) -> int:
    normalized = (value or "").strip().lower()
    ranks = {
        "high": 0,
        "trusted": 0,
        "official": 0,
        "medium": 1,
        "moderate": 1,
        "low": 2,
    }
    return ranks.get(normalized, 3)


def _is_official_source_category(value: str | None) -> bool:
    normalized = (value or "").replace("_", " ").lower()
    return any(term in normalized for term in OFFICIAL_SOURCE_CATEGORY_TERMS)


def _hostname(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return urlsplit(value).hostname
    except ValueError:
        return None
