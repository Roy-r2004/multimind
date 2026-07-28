"""Guarded Phase 4 real-Serper one-page smoke runner.

Operational only — not imported by application startup or ARQ workers.

Commands:
  preview  — read-only eligibility inspection
  run      — exactly one claim + one Serper page + one persistence action
  verify   — read-only post-smoke proof

Live ``run`` uses production Phase 4 services and the real configured Serper
adapter. It never enqueues continuation or runs mock/later scraper stages.

``prepare-existing`` reopens historical mock-completed Step 3B executions for
one guarded smoke action without touching production worker orchestration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models import (
    ScrapingCrawlEdge,
    ScrapingEvent,
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingSourceCandidate,
    ScrapingSourceDiscoveryQuery,
    ScrapingCrawlNode,
    SourceDiscoveryQueryStatus,
)
from app.db.session import AsyncSessionLocal
from app.schemas.scraping_execution_plan import supports_deterministic_query_generation
from app.services.scraping.execution_service import execution_service
from app.services.scraping.search_providers import resolve_v2_discovery_provider
from app.services.scraping.search_providers.base import SearchProviderConfigurationError
from app.services.scraping.source_discovery_claim_service import (
    ClaimBatchResult,
    ClaimedQueryJob,
    SourceDiscoveryClaimService,
    evaluate_claim_lifecycle,
    immediate_retry_policy,
    normalize_lifecycle_error_code,
)
from app.services.scraping.source_discovery_execution_service import (
    PROVIDER_BLOCK_CODE_KEY,
    PROVIDER_BLOCK_PROFILE_KEY,
    PROVIDER_BLOCK_PROVIDER_KEY,
    PROVIDER_BLOCK_STAGE_KEY,
)
from app.services.scraping.source_discovery_provider_service import (
    DiscoveryProviderExecutionResult,
    PROVIDER_WIDE_BLOCKERS as PROVIDER_WIDE_BLOCKERS_PROVIDER,
    QUERY_TERMINAL_OUTCOMES,
    RETRYABLE_OUTCOMES,
    SourceDiscoveryProviderService,
)
from app.services.scraping.source_discovery_result_service import (
    DiscoveryPersistenceResult,
    SourceDiscoveryResultService,
    prepare_provider_results,
)

SmokeOutcome = Literal[
    "succeeded",
    "page_continued",
    "retry_scheduled",
    "provider_blocked",
    "query_failed",
    "claim_mismatch",
    "lifecycle_blocked",
    "configuration_error",
    "persistence_error",
    "preparation_error",
    "unexpected_error",
]

NowFactory = Callable[[], datetime]

FORBIDDEN_MOCK_STAGE_EVENTS = frozenset(
    {
        "stage_completed",
        "execution_completed",
        "mission_campaign_completed",
    }
)

HISTORICAL_MOCK_STAGES = frozenset(
    {
        "discovery",
        "verification",
        "citation_checking",
        "database_cleaning",
    }
)

LEGACY_REOPEN_REASON = "completed_by_historical_mock_workflow_with_pending_phase4_jobs"

PHASE4_SMOKE_PREPARED_KEY = "phase4_smoke_prepared"
PHASE4_SMOKE_PREPARED_AT_KEY = "phase4_smoke_prepared_at"
PHASE4_SMOKE_ORIGINAL_STATUS_KEY = "phase4_smoke_original_status"
PHASE4_SMOKE_ORIGINAL_STAGE_KEY = "phase4_smoke_original_stage"
PHASE4_SMOKE_ORIGINAL_COMPLETED_AT_KEY = "phase4_smoke_original_completed_at"
PHASE4_SMOKE_EVENT_BASELINE_AT_KEY = "phase4_smoke_event_baseline_at"
PHASE4_SMOKE_EVENT_BASELINE_EVENT_ID_KEY = "phase4_smoke_event_baseline_event_id"
PHASE4_SMOKE_EXPECTED_JOB_KEY = "phase4_smoke_expected_query_job_id"

PrepareOutcome = Literal["prepared", "already_prepared", "failed"]


@dataclass(frozen=True)
class PreviewExecutionRow:
    organization_id: str
    execution_id: str
    mission_id: str | None
    status: str
    current_stage: str | None
    pending_count: int
    running_count: int
    succeeded_count: int
    failed_count: int
    provider_blocked: bool
    provider_block_code: str | None
    earliest_job_id: str | None
    earliest_priority: int | None
    earliest_ordinal: int | None
    earliest_page: int | None
    earliest_language: str | None
    earliest_scope: str | None
    earliest_region: str | None
    earliest_city: str | None
    earliest_query_text: str | None
    legacy_step3b_reopen_candidate: bool = False
    legacy_reopen_reason: str | None = None


@dataclass(frozen=True)
class PrepareExistingResult:
    outcome: PrepareOutcome
    organization_id: str
    execution_id: str
    expected_query_job_id: str
    error_code: str | None = None
    pending_count: int | None = None
    original_status: str | None = None
    original_stage: str | None = None
    original_completed_at: str | None = None
    event_baseline_at: str | None = None
    event_baseline_event_id: str | None = None
    execution_status: str | None = None
    execution_current_stage: str | None = None


@dataclass(frozen=True)
class SmokeRunResult:
    outcome: SmokeOutcome
    organization_id: str
    execution_id: str
    query_job_id: str | None = None
    provider: str = "serper"
    requested_page: int | None = None
    raw_provider_count: int = 0
    parsed_provider_count: int = 0
    malformed_provider_count: int = 0
    invalid_url_count: int = 0
    unsafe_url_count: int = 0
    duplicate_within_query_count: int = 0
    candidate_inserted_count: int = 0
    candidate_existing_count: int = 0
    crawl_node_created_count: int = 0
    crawl_node_existing_count: int = 0
    query_status: str | None = None
    pages_completed: int | None = None
    next_page_number: int | None = None
    pagination_completed: bool | None = None
    execution_status: str | None = None
    execution_current_stage: str | None = None
    continuation_enqueued: bool = False
    mock_stages_executed: bool = False
    error_code: str | None = None
    query_text: str | None = None
    post_smoke_paused: bool = False
    provider_calls: int = 0
    final_preview: dict[str, Any] | None = None


def _emit_final_preview(
    preview: dict[str, Any],
    *,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    payload = sanitize_public_mapping(preview)
    if emit is not None:
        emit(payload)
    else:
        print(json.dumps(payload, indent=2, default=str))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def is_serper_configured() -> bool:
    key = (get_settings().serper_api_key or "").strip()
    return bool(key)


def assert_live_provider_service(provider_service: SourceDiscoveryProviderService) -> None:
    """Reject injected/fake providers in live smoke mode."""
    if getattr(provider_service, "_injected_provider", None) is not None:
        raise RuntimeError("Live smoke mode rejects an injected provider service.")
    if getattr(provider_service, "_client_factory", None) is not None:
        raise RuntimeError("Live smoke mode rejects an injected HTTP client factory.")


def assert_real_serper_resolver() -> None:
    resolve_v2_discovery_provider("serper")


def is_step3b_query(row: ScrapingSourceDiscoveryQuery) -> bool:
    return (
        row.execution_id is not None
        and row.query_job_fingerprint is not None
        and row.plan_hash_snapshot is not None
    )


def is_eligible_pending_query(row: ScrapingSourceDiscoveryQuery, *, now: datetime) -> bool:
    if row.status != SourceDiscoveryQueryStatus.PENDING:
        return False
    if not is_step3b_query(row):
        return False
    if row.next_attempt_at is not None and row.next_attempt_at > now:
        return False
    if bool(getattr(row, "pagination_completed", False)):
        return False
    return True


def is_virgin_smoke_query(row: ScrapingSourceDiscoveryQuery) -> bool:
    return (
        row.status == SourceDiscoveryQueryStatus.PENDING
        and row.provider is None
        and row.requested_at is None
        and row.claim_token is None
        and int(getattr(row, "next_page_number", None) or 1) == 1
        and int(getattr(row, "pages_completed", None) or 0) == 0
        and not bool(getattr(row, "pagination_completed", False))
    )


def _execution_status_value(execution: ScrapingExecution) -> str:
    return execution.status.value if hasattr(execution.status, "value") else str(execution.status)


def _iso_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_profile_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return None


def _smoke_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    return dict(profile or {})


def requires_smoke_preparation_marker(profile: dict[str, Any]) -> bool:
    return bool(profile.get(PHASE4_SMOKE_PREPARED_KEY)) or (
        profile.get(PHASE4_SMOKE_ORIGINAL_STATUS_KEY) == ScrapingExecutionStatus.COMPLETED.value
    )


async def _count_pending_without_step3b_provenance(
    session: AsyncSession,
    *,
    organization_id: str,
    execution_id: str,
) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(ScrapingSourceDiscoveryQuery)
                .where(
                    ScrapingSourceDiscoveryQuery.organization_id == organization_id,
                    ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                    ScrapingSourceDiscoveryQuery.status == SourceDiscoveryQueryStatus.PENDING,
                    or_(
                        ScrapingSourceDiscoveryQuery.query_job_fingerprint.is_(None),
                        ScrapingSourceDiscoveryQuery.plan_hash_snapshot.is_(None),
                    ),
                )
            )
        ).scalar_one()
        or 0
    )


async def _count_table_rows(
    session: AsyncSession,
    model: type,
    *,
    organization_id: str,
    execution_id: str,
) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(model)
                .where(
                    model.organization_id == organization_id,
                    model.execution_id == execution_id,
                )
            )
        ).scalar_one()
        or 0
    )


async def _has_query_generation_completed(session: AsyncSession, execution_id: str) -> bool:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(ScrapingEvent)
                .where(
                    ScrapingEvent.execution_id == execution_id,
                    ScrapingEvent.event_type == "query_generation_completed",
                )
            )
        ).scalar_one()
        or 0
    ) > 0


async def _earliest_pending_step3b_job(
    session: AsyncSession,
    *,
    organization_id: str,
    execution_id: str,
) -> ScrapingSourceDiscoveryQuery | None:
    result = await session.execute(
        select(ScrapingSourceDiscoveryQuery)
        .where(
            ScrapingSourceDiscoveryQuery.organization_id == organization_id,
            ScrapingSourceDiscoveryQuery.execution_id == execution_id,
            ScrapingSourceDiscoveryQuery.status == SourceDiscoveryQueryStatus.PENDING,
            ScrapingSourceDiscoveryQuery.query_job_fingerprint.is_not(None),
            ScrapingSourceDiscoveryQuery.plan_hash_snapshot.is_not(None),
        )
        .order_by(
            ScrapingSourceDiscoveryQuery.priority.asc(),
            ScrapingSourceDiscoveryQuery.generation_ordinal.asc(),
            ScrapingSourceDiscoveryQuery.id.asc(),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _latest_event_row(
    session: AsyncSession, execution_id: str
) -> ScrapingEvent | None:
    result = await session.execute(
        select(ScrapingEvent)
        .where(ScrapingEvent.execution_id == execution_id)
        .order_by(ScrapingEvent.sequence_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _evaluate_legacy_reopen_preconditions(
    session: AsyncSession,
    *,
    organization_id: str,
    execution_id: str,
    expected_query_job_id: str,
    execution: ScrapingExecution,
) -> str | None:
    """Return a sanitized failure code, or None when all strict preconditions pass."""
    if execution.organization_id != organization_id or execution.id != execution_id:
        return "execution_mismatch"
    if execution.execution_type != "mission_campaign":
        return "not_mission_campaign"
    if execution.execution_plan_schema_version != "2":
        return "not_schema_v2"
    if not supports_deterministic_query_generation(execution.execution_plan_schema_version):
        return "not_schema_v2"

    if execution.status in {
        ScrapingExecutionStatus.CANCELLED,
        ScrapingExecutionStatus.CANCEL_REQUESTED,
        ScrapingExecutionStatus.FAILED,
    }:
        return "cancelled_or_failed"

    profile = _smoke_profile(execution.country_profile_json)
    if profile.get(PHASE4_SMOKE_PREPARED_KEY):
        stored_job = profile.get(PHASE4_SMOKE_EXPECTED_JOB_KEY)
        if stored_job != expected_query_job_id:
            return "inconsistent_prepared_state"
        if execution.status != ScrapingExecutionStatus.RUNNING:
            return "inconsistent_prepared_state"
        if execution.current_stage != "web_discovery":
            return "inconsistent_prepared_state"
        return None

    if execution.status != ScrapingExecutionStatus.COMPLETED:
        return "status_not_completed"
    if execution.completed_at is None:
        return "completed_at_missing"
    if execution.current_stage not in HISTORICAL_MOCK_STAGES:
        return "stage_not_historical_mock"

    if profile.get(PROVIDER_BLOCK_PROFILE_KEY):
        return "provider_blocked"

    pending_count = await _count_jobs(
        session,
        organization_id=organization_id,
        execution_id=execution_id,
        status=SourceDiscoveryQueryStatus.PENDING,
    )
    running_count = await _count_jobs(
        session,
        organization_id=organization_id,
        execution_id=execution_id,
        status=SourceDiscoveryQueryStatus.RUNNING,
    )
    succeeded_count = await _count_jobs(
        session,
        organization_id=organization_id,
        execution_id=execution_id,
        status=SourceDiscoveryQueryStatus.SUCCEEDED,
    )
    failed_count = await _count_jobs(
        session,
        organization_id=organization_id,
        execution_id=execution_id,
        status=SourceDiscoveryQueryStatus.FAILED,
    )

    if pending_count <= 0:
        return "no_pending_jobs"
    if running_count > 0:
        return "running_jobs_exist"
    if succeeded_count > 0:
        return "succeeded_jobs_exist"
    if failed_count > 0:
        return "failed_jobs_exist"

    if await _count_pending_without_step3b_provenance(
        session, organization_id=organization_id, execution_id=execution_id
    ) > 0:
        return "step3b_provenance_missing"

    if not await _has_query_generation_completed(session, execution_id):
        return "query_generation_not_completed"

    candidate_count = await _count_table_rows(
        session,
        ScrapingSourceCandidate,
        organization_id=organization_id,
        execution_id=execution_id,
    )
    crawl_node_count = await _count_table_rows(
        session,
        ScrapingCrawlNode,
        organization_id=organization_id,
        execution_id=execution_id,
    )
    crawl_edge_count = await _count_table_rows(
        session,
        ScrapingCrawlEdge,
        organization_id=organization_id,
        execution_id=execution_id,
    )
    if candidate_count > 0:
        return "candidates_exist"
    if crawl_node_count > 0:
        return "crawl_nodes_exist"
    if crawl_edge_count > 0:
        return "crawl_edges_exist"

    expected_job = await session.get(ScrapingSourceDiscoveryQuery, expected_query_job_id)
    if (
        expected_job is None
        or expected_job.organization_id != organization_id
        or expected_job.execution_id != execution_id
        or not is_step3b_query(expected_job)
    ):
        return "query_job_mismatch"
    if not is_virgin_smoke_query(expected_job):
        return "expected_job_not_virgin"

    earliest = await _earliest_pending_step3b_job(
        session, organization_id=organization_id, execution_id=execution_id
    )
    if earliest is None or earliest.id != expected_query_job_id:
        return "expected_job_not_earliest"

    return None


def _is_legacy_reopen_candidate(
    execution: ScrapingExecution,
    *,
    pending_count: int,
    running_count: int,
    succeeded_count: int,
    failed_count: int,
    provider_blocked: bool,
    earliest_job_id: str | None,
) -> bool:
    if provider_blocked:
        return False
    if execution.status != ScrapingExecutionStatus.COMPLETED:
        return False
    if execution.completed_at is None:
        return False
    if execution.current_stage not in HISTORICAL_MOCK_STAGES:
        return False
    if pending_count <= 0 or earliest_job_id is None:
        return False
    if running_count or succeeded_count or failed_count:
        return False
    return True


def sanitize_public_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    blocked_keys = {
        "claim_token",
        "query_job_fingerprint",
        "plan_hash_snapshot",
        "canonical_url_hash",
        "last_page_fingerprint",
        "api_key",
        "authorization",
        "raw_response",
        "stack",
        "traceback",
    }

    def _scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: _scrub(v)
                for k, v in value.items()
                if k.lower() not in blocked_keys and not k.lower().endswith("_hash")
            }
        if isinstance(value, list):
            return [_scrub(v) for v in value]
        return value

    return _scrub(payload)


async def _load_execution(
    session: AsyncSession, *, organization_id: str, execution_id: str
) -> ScrapingExecution | None:
    result = await session.execute(
        select(ScrapingExecution).where(
            ScrapingExecution.id == execution_id,
            ScrapingExecution.organization_id == organization_id,
        )
    )
    return result.scalar_one_or_none()


async def _count_jobs(
    session: AsyncSession,
    *,
    organization_id: str,
    execution_id: str,
    status: SourceDiscoveryQueryStatus,
) -> int:
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(ScrapingSourceDiscoveryQuery)
                .where(
                    ScrapingSourceDiscoveryQuery.organization_id == organization_id,
                    ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                    ScrapingSourceDiscoveryQuery.status == status,
                )
            )
        ).scalar_one()
        or 0
    )


async def _earliest_eligible_job(
    session: AsyncSession,
    *,
    organization_id: str,
    execution_id: str,
    now: datetime,
) -> ScrapingSourceDiscoveryQuery | None:
    result = await session.execute(
        select(ScrapingSourceDiscoveryQuery)
        .where(
            ScrapingSourceDiscoveryQuery.organization_id == organization_id,
            ScrapingSourceDiscoveryQuery.execution_id == execution_id,
            ScrapingSourceDiscoveryQuery.status == SourceDiscoveryQueryStatus.PENDING,
            ScrapingSourceDiscoveryQuery.query_job_fingerprint.is_not(None),
            ScrapingSourceDiscoveryQuery.plan_hash_snapshot.is_not(None),
            or_(
                ScrapingSourceDiscoveryQuery.next_attempt_at.is_(None),
                ScrapingSourceDiscoveryQuery.next_attempt_at <= now,
            ),
        )
        .order_by(
            ScrapingSourceDiscoveryQuery.priority.asc(),
            ScrapingSourceDiscoveryQuery.generation_ordinal.asc(),
            ScrapingSourceDiscoveryQuery.id.asc(),
        )
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None or not is_eligible_pending_query(row, now=now):
        return None
    return row


async def preview_executions(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: str | None = None,
    execution_id: str | None = None,
    now: datetime | None = None,
) -> tuple[list[PreviewExecutionRow], list[PreviewExecutionRow]]:
    """Return (eligible_rows, all_inspected_rows)."""
    clock = now or _utc_now()
    all_rows: list[PreviewExecutionRow] = []
    eligible_rows: list[PreviewExecutionRow] = []
    async with session_factory() as session:
        stmt = select(ScrapingExecution).where(
            ScrapingExecution.execution_type == "mission_campaign",
            ScrapingExecution.execution_plan_schema_version == "2",
        )
        if organization_id:
            stmt = stmt.where(ScrapingExecution.organization_id == organization_id)
        if execution_id:
            stmt = stmt.where(ScrapingExecution.id == execution_id)
        executions = (await session.execute(stmt.order_by(ScrapingExecution.created_at.desc()))).scalars().all()

        for execution in executions:
            if not supports_deterministic_query_generation(execution.execution_plan_schema_version):
                continue
            lifecycle_block = evaluate_claim_lifecycle(execution)
            profile = dict(execution.country_profile_json or {})
            provider_blocked = bool(profile.get(PROVIDER_BLOCK_PROFILE_KEY))
            earliest = await _earliest_eligible_job(
                session,
                organization_id=execution.organization_id,
                execution_id=execution.id,
                now=clock,
            )
            pending_count = await _count_jobs(
                session,
                organization_id=execution.organization_id,
                execution_id=execution.id,
                status=SourceDiscoveryQueryStatus.PENDING,
            )
            running_count = await _count_jobs(
                session,
                organization_id=execution.organization_id,
                execution_id=execution.id,
                status=SourceDiscoveryQueryStatus.RUNNING,
            )
            succeeded_count = await _count_jobs(
                session,
                organization_id=execution.organization_id,
                execution_id=execution.id,
                status=SourceDiscoveryQueryStatus.SUCCEEDED,
            )
            failed_count = await _count_jobs(
                session,
                organization_id=execution.organization_id,
                execution_id=execution.id,
                status=SourceDiscoveryQueryStatus.FAILED,
            )
            legacy_candidate = _is_legacy_reopen_candidate(
                execution,
                pending_count=pending_count,
                running_count=running_count,
                succeeded_count=succeeded_count,
                failed_count=failed_count,
                provider_blocked=provider_blocked,
                earliest_job_id=earliest.id if earliest else None,
            )
            row = PreviewExecutionRow(
                organization_id=execution.organization_id,
                execution_id=execution.id,
                mission_id=execution.mission_id,
                status=_execution_status_value(execution),
                current_stage=execution.current_stage,
                pending_count=pending_count,
                running_count=running_count,
                succeeded_count=succeeded_count,
                failed_count=failed_count,
                provider_blocked=provider_blocked,
                provider_block_code=profile.get(PROVIDER_BLOCK_CODE_KEY),
                earliest_job_id=earliest.id if earliest else None,
                earliest_priority=earliest.priority if earliest else None,
                earliest_ordinal=earliest.generation_ordinal if earliest else None,
                earliest_page=int(getattr(earliest, "next_page_number", None) or 1) if earliest else None,
                earliest_language=earliest.language_code if earliest else None,
                earliest_scope=earliest.scope_level if earliest else None,
                earliest_region=earliest.region_name if earliest else None,
                earliest_city=earliest.important_city if earliest else None,
                earliest_query_text=earliest.query_text if earliest else None,
                legacy_step3b_reopen_candidate=legacy_candidate,
                legacy_reopen_reason=LEGACY_REOPEN_REASON if legacy_candidate else None,
            )
            all_rows.append(row)
            if (
                lifecycle_block is None
                and not provider_blocked
                and earliest is not None
                and execution.status == ScrapingExecutionStatus.RUNNING
            ):
                eligible_rows.append(row)
    return eligible_rows, all_rows


async def _pause_execution_for_review(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: str,
    execution_id: str,
    now: datetime,
) -> bool:
    """Pause RUNNING execution after smoke; do not overwrite blocked/cancelled/terminal."""
    async with session_factory() as session:
        async with session.begin():
            execution = await _load_execution(
                session, organization_id=organization_id, execution_id=execution_id
            )
            if execution is None:
                return False
            if execution.status != ScrapingExecutionStatus.RUNNING:
                return False
            profile = dict(execution.country_profile_json or {})
            if profile.get(PROVIDER_BLOCK_PROFILE_KEY):
                return False
            execution.status = ScrapingExecutionStatus.PAUSED
            execution.paused_at = execution.paused_at or now
            execution.completed_at = None
            execution.current_stage = "web_discovery"
            execution.current_stage_label = "Web discovery"
            execution.latest_message = (
                "Paused after guarded Phase 4 Serper smoke — review before resuming worker."
            )
            return True


async def prepare_existing_execution(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: str,
    execution_id: str,
    expected_query_job_id: str,
    now: datetime | None = None,
) -> PrepareExistingResult:
    clock = now or _utc_now()
    async with session_factory() as session:
        async with session.begin():
            execution = await _load_execution(
                session, organization_id=organization_id, execution_id=execution_id
            )
            if execution is None:
                return PrepareExistingResult(
                    outcome="failed",
                    organization_id=organization_id,
                    execution_id=execution_id,
                    expected_query_job_id=expected_query_job_id,
                    error_code="execution_mismatch",
                )

            profile = _smoke_profile(execution.country_profile_json)
            if profile.get(PHASE4_SMOKE_PREPARED_KEY):
                failure = await _evaluate_legacy_reopen_preconditions(
                    session,
                    organization_id=organization_id,
                    execution_id=execution_id,
                    expected_query_job_id=expected_query_job_id,
                    execution=execution,
                )
                if failure is not None:
                    return PrepareExistingResult(
                        outcome="failed",
                        organization_id=organization_id,
                        execution_id=execution_id,
                        expected_query_job_id=expected_query_job_id,
                        error_code=failure,
                    )
                return PrepareExistingResult(
                    outcome="already_prepared",
                    organization_id=organization_id,
                    execution_id=execution_id,
                    expected_query_job_id=expected_query_job_id,
                    pending_count=await _count_jobs(
                        session,
                        organization_id=organization_id,
                        execution_id=execution_id,
                        status=SourceDiscoveryQueryStatus.PENDING,
                    ),
                    original_status=profile.get(PHASE4_SMOKE_ORIGINAL_STATUS_KEY),
                    original_stage=profile.get(PHASE4_SMOKE_ORIGINAL_STAGE_KEY),
                    original_completed_at=profile.get(PHASE4_SMOKE_ORIGINAL_COMPLETED_AT_KEY),
                    event_baseline_at=profile.get(PHASE4_SMOKE_EVENT_BASELINE_AT_KEY),
                    event_baseline_event_id=profile.get(PHASE4_SMOKE_EVENT_BASELINE_EVENT_ID_KEY),
                    execution_status=_execution_status_value(execution),
                    execution_current_stage=execution.current_stage,
                )

            failure = await _evaluate_legacy_reopen_preconditions(
                session,
                organization_id=organization_id,
                execution_id=execution_id,
                expected_query_job_id=expected_query_job_id,
                execution=execution,
            )
            if failure is not None:
                return PrepareExistingResult(
                    outcome="failed",
                    organization_id=organization_id,
                    execution_id=execution_id,
                    expected_query_job_id=expected_query_job_id,
                    error_code=failure,
                )

            pending_count = await _count_jobs(
                session,
                organization_id=organization_id,
                execution_id=execution_id,
                status=SourceDiscoveryQueryStatus.PENDING,
            )
            latest_event = await _latest_event_row(session, execution_id)
            baseline_at = clock
            baseline_event_id: str | None = None
            if latest_event is not None:
                baseline_event_id = latest_event.id
                baseline_at = latest_event.created_at or clock

            original_status = _execution_status_value(execution)
            original_stage = execution.current_stage
            original_completed_at = _iso_datetime(execution.completed_at)

            profile[PHASE4_SMOKE_PREPARED_KEY] = True
            profile[PHASE4_SMOKE_PREPARED_AT_KEY] = clock.isoformat()
            profile[PHASE4_SMOKE_ORIGINAL_STATUS_KEY] = original_status
            profile[PHASE4_SMOKE_ORIGINAL_STAGE_KEY] = original_stage
            profile[PHASE4_SMOKE_ORIGINAL_COMPLETED_AT_KEY] = original_completed_at
            profile[PHASE4_SMOKE_EVENT_BASELINE_AT_KEY] = baseline_at.isoformat()
            if baseline_event_id is not None:
                profile[PHASE4_SMOKE_EVENT_BASELINE_EVENT_ID_KEY] = baseline_event_id
            profile[PHASE4_SMOKE_EXPECTED_JOB_KEY] = expected_query_job_id
            execution.country_profile_json = profile

            execution.status = ScrapingExecutionStatus.RUNNING
            execution.current_stage = "web_discovery"
            execution.current_stage_label = "Web discovery"
            execution.current_provider = "serper"
            execution.completed_at = None
            execution.paused_at = None
            execution.pause_requested_at = None
            execution.cancel_requested_at = None
            execution.error_message = None
            execution.latest_message = (
                "Reopened for guarded Phase 4 Serper smoke after historical mock completion."
            )

            await execution_service.emit_event(
                session,
                execution_id,
                "web_discovery_smoke_prepared",
                "Execution reopened for guarded Phase 4 Serper smoke.",
                metadata={
                    "execution_id": execution_id,
                    "expected_query_job_id": expected_query_job_id,
                    "pending_count": pending_count,
                    "original_status": original_status,
                    "original_stage": original_stage,
                },
            )

            return PrepareExistingResult(
                outcome="prepared",
                organization_id=organization_id,
                execution_id=execution_id,
                expected_query_job_id=expected_query_job_id,
                pending_count=pending_count,
                original_status=original_status,
                original_stage=original_stage,
                original_completed_at=original_completed_at,
                event_baseline_at=baseline_at.isoformat(),
                event_baseline_event_id=baseline_event_id,
                execution_status=_execution_status_value(execution),
                execution_current_stage=execution.current_stage,
            )


async def _evaluate_prepare_next_preconditions(
    session: AsyncSession,
    *,
    organization_id: str,
    execution_id: str,
    next_query_job_id: str,
    execution: ScrapingExecution,
) -> str | None:
    """Return a sanitized failure code, or None when prepare-next may proceed."""
    if execution.organization_id != organization_id or execution.id != execution_id:
        return "execution_mismatch"

    profile = _smoke_profile(execution.country_profile_json)
    if not profile.get(PHASE4_SMOKE_PREPARED_KEY):
        return "smoke_preparation_required"
    if profile.get(PROVIDER_BLOCK_PROFILE_KEY):
        return "provider_blocked"

    if execution.status in {
        ScrapingExecutionStatus.COMPLETED,
        ScrapingExecutionStatus.FAILED,
        ScrapingExecutionStatus.CANCELLED,
        ScrapingExecutionStatus.CANCEL_REQUESTED,
    }:
        return "execution_not_eligible"

    if execution.status != ScrapingExecutionStatus.PAUSED:
        return "execution_not_paused"
    if execution.current_stage != "web_discovery":
        return "execution_stage_mismatch"

    prior_job_id = profile.get(PHASE4_SMOKE_EXPECTED_JOB_KEY)
    if not isinstance(prior_job_id, str) or not prior_job_id:
        return "smoke_preparation_required"
    if prior_job_id == next_query_job_id:
        return "next_job_same_as_prior"

    prior_job = await session.get(ScrapingSourceDiscoveryQuery, prior_job_id)
    if (
        prior_job is None
        or prior_job.organization_id != organization_id
        or prior_job.execution_id != execution_id
    ):
        return "prior_smoke_job_not_succeeded"
    if prior_job.status != SourceDiscoveryQueryStatus.SUCCEEDED:
        return "prior_smoke_job_not_succeeded"
    if not bool(getattr(prior_job, "pagination_completed", False)):
        return "prior_smoke_job_pagination_incomplete"

    prior_candidate_count = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ScrapingSourceCandidate)
                .where(
                    ScrapingSourceCandidate.organization_id == organization_id,
                    ScrapingSourceCandidate.discovery_query_id == prior_job_id,
                )
            )
        ).scalar_one()
        or 0
    )
    if prior_candidate_count <= 0:
        return "prior_smoke_evidence_missing"

    prior_node_count = int(
        (
            await session.execute(
                select(func.count(func.distinct(ScrapingSourceCandidate.crawl_node_id)))
                .select_from(ScrapingSourceCandidate)
                .where(
                    ScrapingSourceCandidate.organization_id == organization_id,
                    ScrapingSourceCandidate.discovery_query_id == prior_job_id,
                    ScrapingSourceCandidate.crawl_node_id.is_not(None),
                )
            )
        ).scalar_one()
        or 0
    )
    if prior_node_count <= 0:
        return "prior_smoke_crawl_evidence_missing"

    running_count = await _count_jobs(
        session,
        organization_id=organization_id,
        execution_id=execution_id,
        status=SourceDiscoveryQueryStatus.RUNNING,
    )
    if running_count > 0:
        return "running_jobs_exist"

    next_job = await session.get(ScrapingSourceDiscoveryQuery, next_query_job_id)
    if (
        next_job is None
        or next_job.organization_id != organization_id
        or next_job.execution_id != execution_id
        or not is_step3b_query(next_job)
    ):
        return "query_job_mismatch"
    if not is_virgin_smoke_query(next_job):
        return "expected_job_not_virgin"

    earliest = await _earliest_pending_step3b_job(
        session, organization_id=organization_id, execution_id=execution_id
    )
    if earliest is None or earliest.id != next_query_job_id:
        return "expected_job_not_earliest"

    return None


async def prepare_next_smoke_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: str,
    execution_id: str,
    next_query_job_id: str,
    now: datetime | None = None,
) -> PrepareExistingResult:
    clock = now or _utc_now()
    async with session_factory() as session:
        async with session.begin():
            execution = await _load_execution(
                session, organization_id=organization_id, execution_id=execution_id
            )
            if execution is None:
                return PrepareExistingResult(
                    outcome="failed",
                    organization_id=organization_id,
                    execution_id=execution_id,
                    expected_query_job_id=next_query_job_id,
                    error_code="execution_mismatch",
                )

            profile = _smoke_profile(execution.country_profile_json)
            if (
                profile.get(PHASE4_SMOKE_EXPECTED_JOB_KEY) == next_query_job_id
                and execution.status == ScrapingExecutionStatus.RUNNING
                and execution.current_stage == "web_discovery"
            ):
                return PrepareExistingResult(
                    outcome="already_prepared",
                    organization_id=organization_id,
                    execution_id=execution_id,
                    expected_query_job_id=next_query_job_id,
                    pending_count=await _count_jobs(
                        session,
                        organization_id=organization_id,
                        execution_id=execution_id,
                        status=SourceDiscoveryQueryStatus.PENDING,
                    ),
                    event_baseline_at=profile.get(PHASE4_SMOKE_EVENT_BASELINE_AT_KEY),
                    event_baseline_event_id=profile.get(
                        PHASE4_SMOKE_EVENT_BASELINE_EVENT_ID_KEY
                    ),
                    execution_status=_execution_status_value(execution),
                    execution_current_stage=execution.current_stage,
                )

            failure = await _evaluate_prepare_next_preconditions(
                session,
                organization_id=organization_id,
                execution_id=execution_id,
                next_query_job_id=next_query_job_id,
                execution=execution,
            )
            if failure is not None:
                return PrepareExistingResult(
                    outcome="failed",
                    organization_id=organization_id,
                    execution_id=execution_id,
                    expected_query_job_id=next_query_job_id,
                    error_code=failure,
                )

            pending_count = await _count_jobs(
                session,
                organization_id=organization_id,
                execution_id=execution_id,
                status=SourceDiscoveryQueryStatus.PENDING,
            )
            prior_job_id = profile.get(PHASE4_SMOKE_EXPECTED_JOB_KEY)
            latest_event = await _latest_event_row(session, execution_id)
            baseline_at = clock
            baseline_event_id: str | None = None
            if latest_event is not None:
                baseline_event_id = latest_event.id
                baseline_at = latest_event.created_at or clock

            profile[PHASE4_SMOKE_EXPECTED_JOB_KEY] = next_query_job_id
            profile[PHASE4_SMOKE_EVENT_BASELINE_AT_KEY] = baseline_at.isoformat()
            if baseline_event_id is not None:
                profile[PHASE4_SMOKE_EVENT_BASELINE_EVENT_ID_KEY] = baseline_event_id
            execution.country_profile_json = profile

            execution.status = ScrapingExecutionStatus.RUNNING
            execution.current_stage = "web_discovery"
            execution.current_stage_label = "Web discovery"
            execution.current_provider = "serper"
            execution.completed_at = None
            execution.paused_at = None
            execution.pause_requested_at = None
            execution.cancel_requested_at = None
            execution.error_message = None
            execution.latest_message = (
                "Prepared next guarded Phase 4 Serper smoke job after prior smoke success."
            )

            await execution_service.emit_event(
                session,
                execution_id,
                "web_discovery_smoke_next_prepared",
                "Next smoke query job prepared for guarded Phase 4 Serper smoke.",
                metadata={
                    "execution_id": execution_id,
                    "prior_query_job_id": prior_job_id,
                    "next_query_job_id": next_query_job_id,
                    "pending_count": pending_count,
                },
            )

            return PrepareExistingResult(
                outcome="prepared",
                organization_id=organization_id,
                execution_id=execution_id,
                expected_query_job_id=next_query_job_id,
                pending_count=pending_count,
                event_baseline_at=baseline_at.isoformat(),
                event_baseline_event_id=baseline_event_id,
                execution_status=_execution_status_value(execution),
                execution_current_stage=execution.current_stage,
            )


async def _apply_provider_blocked_pause(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: str,
    execution_id: str,
    blocker_code: str,
    now: datetime,
) -> None:
    safe_code = normalize_lifecycle_error_code(blocker_code)
    async with session_factory() as session:
        async with session.begin():
            execution = await _load_execution(
                session, organization_id=organization_id, execution_id=execution_id
            )
            if execution is None:
                return
            if execution.status in {
                ScrapingExecutionStatus.COMPLETED,
                ScrapingExecutionStatus.FAILED,
                ScrapingExecutionStatus.CANCELLED,
                ScrapingExecutionStatus.CANCEL_REQUESTED,
            }:
                return
            profile = dict(execution.country_profile_json or {})
            profile[PROVIDER_BLOCK_PROFILE_KEY] = True
            profile[PROVIDER_BLOCK_CODE_KEY] = safe_code
            profile[PROVIDER_BLOCK_PROVIDER_KEY] = "serper"
            profile[PROVIDER_BLOCK_STAGE_KEY] = "web_discovery"
            execution.country_profile_json = profile
            execution.status = ScrapingExecutionStatus.PAUSED
            execution.paused_at = execution.paused_at or now
            execution.completed_at = None
            execution.current_stage = "web_discovery"
            execution.current_stage_label = "Web discovery"
            execution.current_provider = "serper"
            execution.latest_message = (
                "Web discovery paused: provider configuration or authentication must be fixed before resume."
            )


async def _release_unexpected_claim(
    claim_service: SourceDiscoveryClaimService,
    job: ClaimedQueryJob,
    *,
    now: datetime,
) -> None:
    await claim_service.requeue_retryable_failure(
        organization_id=job.organization_id,
        execution_id=job.execution_id,
        query_job_id=job.id,
        claim_token=job.claim_token,
        error_code="smoke_claim_mismatch",
        next_attempt_at=now,
        retry_policy=immediate_retry_policy(),
        now=now,
    )


def _result_from_persistence(
    *,
    base: SmokeRunResult,
    provider_result: DiscoveryProviderExecutionResult,
    persisted: DiscoveryPersistenceResult,
    execution: ScrapingExecution | None,
) -> SmokeRunResult:
    counts = persisted.counts
    exec_status = None
    exec_stage = None
    if execution is not None:
        exec_status = execution.status.value if hasattr(execution.status, "value") else str(execution.status)
        exec_stage = execution.current_stage
    outcome: SmokeOutcome
    if persisted.outcome == "page_continued":
        outcome = "page_continued"
    elif persisted.outcome in {"applied", "idempotent_replay"}:
        outcome = "succeeded"
    else:
        outcome = "persistence_error"
    return SmokeRunResult(
        outcome=outcome,
        organization_id=base.organization_id,
        execution_id=base.execution_id,
        query_job_id=base.query_job_id,
        provider=base.provider,
        requested_page=provider_result.page_number,
        raw_provider_count=counts.raw_provider_count,
        parsed_provider_count=counts.parsed_provider_count,
        malformed_provider_count=counts.malformed_provider_count,
        invalid_url_count=counts.invalid_url_count,
        unsafe_url_count=counts.unsafe_url_count,
        duplicate_within_query_count=counts.duplicate_within_query_count,
        candidate_inserted_count=counts.candidate_inserted_count,
        candidate_existing_count=counts.candidate_existing_count,
        crawl_node_created_count=counts.crawl_node_created_count,
        crawl_node_existing_count=counts.crawl_node_existing_count,
        query_status=persisted.query_status,
        pages_completed=counts.pages_completed,
        next_page_number=counts.next_page_number,
        pagination_completed=counts.pagination_completed,
        execution_status=exec_status,
        execution_current_stage=exec_stage,
        continuation_enqueued=False,
        mock_stages_executed=False,
        error_code=persisted.error_code,
        query_text=base.query_text,
        provider_calls=base.provider_calls,
    )


async def run_one_page_smoke(
    *,
    organization_id: str,
    execution_id: str,
    expected_query_job_id: str,
    claim_service: SourceDiscoveryClaimService,
    provider_service: SourceDiscoveryProviderService,
    result_service: SourceDiscoveryResultService,
    session_factory: async_sessionmaker[AsyncSession],
    live_mode: bool = True,
    provider_name: str = "serper",
    lease_duration: timedelta | None = None,
    now_factory: NowFactory | None = None,
    preview_emitter: Callable[[dict[str, Any]], None] | None = None,
) -> SmokeRunResult:
    """Exactly one claim, one Serper page, one persistence transaction."""
    clock_fn = now_factory or _utc_now
    now = clock_fn()
    lease = lease_duration or timedelta(seconds=120)
    base = SmokeRunResult(
        outcome="unexpected_error",
        organization_id=organization_id,
        execution_id=execution_id,
        query_job_id=expected_query_job_id,
        provider=provider_name,
    )

    if live_mode:
        if not is_serper_configured():
            return SmokeRunResult(
                outcome="configuration_error",
                organization_id=organization_id,
                execution_id=execution_id,
                query_job_id=expected_query_job_id,
                error_code="provider_not_configured",
            )
        assert_live_provider_service(provider_service)
        try:
            assert_real_serper_resolver()
        except SearchProviderConfigurationError:
            return SmokeRunResult(
                outcome="configuration_error",
                organization_id=organization_id,
                execution_id=execution_id,
                query_job_id=expected_query_job_id,
                error_code="unsupported_provider",
            )

    async with session_factory() as session:
        execution = await _load_execution(
            session, organization_id=organization_id, execution_id=execution_id
        )
        blocked = evaluate_claim_lifecycle(execution)
        if blocked is not None:
            return SmokeRunResult(
                outcome="lifecycle_blocked",
                organization_id=organization_id,
                execution_id=execution_id,
                query_job_id=expected_query_job_id,
                error_code=blocked,
            )
        profile = _smoke_profile(execution.country_profile_json)
        if requires_smoke_preparation_marker(profile):
            if not profile.get(PHASE4_SMOKE_PREPARED_KEY):
                return SmokeRunResult(
                    outcome="lifecycle_blocked",
                    organization_id=organization_id,
                    execution_id=execution_id,
                    query_job_id=expected_query_job_id,
                    error_code="smoke_preparation_required",
                )
            if profile.get(PHASE4_SMOKE_EXPECTED_JOB_KEY) != expected_query_job_id:
                return SmokeRunResult(
                    outcome="lifecycle_blocked",
                    organization_id=organization_id,
                    execution_id=execution_id,
                    query_job_id=expected_query_job_id,
                    error_code="smoke_preparation_job_mismatch",
                )
        expected_job = await session.get(ScrapingSourceDiscoveryQuery, expected_query_job_id)
        if (
            expected_job is None
            or expected_job.organization_id != organization_id
            or expected_job.execution_id != execution_id
            or not is_step3b_query(expected_job)
        ):
            return SmokeRunResult(
                outcome="unexpected_error",
                organization_id=organization_id,
                execution_id=execution_id,
                query_job_id=expected_query_job_id,
                error_code="query_job_mismatch",
            )
        if not is_eligible_pending_query(expected_job, now=now):
            return SmokeRunResult(
                outcome="lifecycle_blocked",
                organization_id=organization_id,
                execution_id=execution_id,
                query_job_id=expected_query_job_id,
                error_code="job_not_eligible",
            )
        earliest = await _earliest_eligible_job(
            session,
            organization_id=organization_id,
            execution_id=execution_id,
            now=now,
        )
        if earliest is None or earliest.id != expected_query_job_id:
            return SmokeRunResult(
                outcome="lifecycle_blocked",
                organization_id=organization_id,
                execution_id=execution_id,
                query_job_id=expected_query_job_id,
                error_code="expected_job_not_next_eligible",
            )
        preview_payload = sanitize_public_mapping(
            {
                "query_job_id": expected_job.id,
                "query_text": expected_job.query_text,
                "current_page": int(getattr(expected_job, "next_page_number", None) or 1),
                "language_code": expected_job.language_code,
                "scope_level": expected_job.scope_level,
                "region_name": expected_job.region_name,
                "important_city": expected_job.important_city,
                "provider": provider_name,
            }
        )
        base = SmokeRunResult(
            outcome="unexpected_error",
            organization_id=organization_id,
            execution_id=execution_id,
            query_job_id=expected_query_job_id,
            provider=provider_name,
            query_text=expected_job.query_text,
            requested_page=int(getattr(expected_job, "next_page_number", None) or 1),
        )

    _emit_final_preview(preview_payload, emit=preview_emitter)

    claim_batch: ClaimBatchResult = await claim_service.claim_eligible_jobs(
        organization_id=organization_id,
        execution_id=execution_id,
        provider=provider_name,
        batch_size=1,
        lease_duration=lease,
        now=now,
    )
    if claim_batch.outcome != "claimed" or not claim_batch.jobs:
        return SmokeRunResult(
            outcome="lifecycle_blocked",
            organization_id=organization_id,
            execution_id=execution_id,
            query_job_id=expected_query_job_id,
            error_code=claim_batch.lifecycle_reason or "no_work",
            query_text=base.query_text,
        )

    claimed = claim_batch.jobs[0]
    if claimed.id != expected_query_job_id:
        await _release_unexpected_claim(claim_service, claimed, now=now)
        return SmokeRunResult(
            outcome="claim_mismatch",
            organization_id=organization_id,
            execution_id=execution_id,
            query_job_id=expected_query_job_id,
            error_code="claimed_unexpected_job",
        )

    preflight = await claim_service.preflight_claimed_job(
        organization_id=organization_id,
        execution_id=execution_id,
        query_job_id=claimed.id,
        claim_token=claimed.claim_token,
        now=now,
    )
    if preflight.outcome != "ok":
        if preflight.outcome in {"stale_claim", "not_eligible", "not_found"}:
            return SmokeRunResult(
                outcome="lifecycle_blocked",
                organization_id=organization_id,
                execution_id=execution_id,
                query_job_id=claimed.id,
                error_code=preflight.outcome,
                query_text=claimed.query_text,
            )
        return SmokeRunResult(
            outcome="lifecycle_blocked",
            organization_id=organization_id,
            execution_id=execution_id,
            query_job_id=claimed.id,
            error_code=str(preflight.outcome),
            query_text=claimed.query_text,
        )

    renew = await claim_service.renew_claim(
        organization_id=organization_id,
        execution_id=execution_id,
        query_job_id=claimed.id,
        claim_token=claimed.claim_token,
        lease_duration=lease,
        now=now,
    )
    if renew.outcome != "applied":
        return SmokeRunResult(
            outcome="lifecycle_blocked",
            organization_id=organization_id,
            execution_id=execution_id,
            query_job_id=claimed.id,
            error_code=renew.outcome,
            query_text=claimed.query_text,
        )

    provider_calls = 0
    try:
        provider_result = await provider_service.execute_claimed_query(
            claimed, provider_name
        )
        provider_calls = 1
    except Exception:
        await claim_service.requeue_retryable_failure(
            organization_id=claimed.organization_id,
            execution_id=claimed.execution_id,
            query_job_id=claimed.id,
            claim_token=claimed.claim_token,
            error_code="unexpected_provider_failure",
            next_attempt_at=now,
            retry_policy=immediate_retry_policy(),
            now=now,
        )
        return SmokeRunResult(
            outcome="unexpected_error",
            organization_id=organization_id,
            execution_id=execution_id,
            query_job_id=claimed.id,
            error_code="unexpected_provider_failure",
            query_text=claimed.query_text,
            provider_calls=provider_calls,
        )

    base = SmokeRunResult(
        outcome="unexpected_error",
        organization_id=organization_id,
        execution_id=execution_id,
        query_job_id=claimed.id,
        provider=provider_name,
        query_text=claimed.query_text,
        requested_page=provider_result.page_number,
        raw_provider_count=int(provider_result.raw_result_count or 0),
        parsed_provider_count=int(provider_result.accepted_result_count or 0),
        malformed_provider_count=int(provider_result.skipped_malformed_count or 0),
        provider_calls=provider_calls,
    )

    if provider_result.succeeded:
        prepared = prepare_provider_results(claimed, provider_result, require_dns=False, clock=now)
        if not prepared.ready:
            await claim_service.mark_terminal_failure(
                organization_id=claimed.organization_id,
                execution_id=claimed.execution_id,
                query_job_id=claimed.id,
                claim_token=claimed.claim_token,
                error_code=prepared.error_code or "invalid_provider_batch",
                now=now,
            )
            return SmokeRunResult(
                outcome="preparation_error",
                organization_id=organization_id,
                execution_id=execution_id,
                query_job_id=claimed.id,
                provider=provider_name,
                query_text=claimed.query_text,
                requested_page=provider_result.page_number,
                error_code=prepared.error_code or "invalid_provider_batch",
                provider_calls=provider_calls,
            )

        has_more = bool(
            provider_result.continuation is not None and provider_result.continuation.has_more
        )
        if has_more:
            persisted = await result_service.persist_page_and_continue(
                prepared, now=now, next_attempt_at=now
            )
        else:
            persisted = await result_service.persist_final_page_and_succeed(prepared, now=now)

        async with session_factory() as session:
            execution = await _load_execution(
                session, organization_id=organization_id, execution_id=execution_id
            )

        result = _result_from_persistence(
            base=base,
            provider_result=provider_result,
            persisted=persisted,
            execution=execution,
        )
        if persisted.outcome not in {"applied", "idempotent_replay", "page_continued"}:
            return SmokeRunResult(
                outcome="persistence_error",
                organization_id=organization_id,
                execution_id=execution_id,
                query_job_id=claimed.id,
                provider=provider_name,
                query_text=claimed.query_text,
                requested_page=provider_result.page_number,
                error_code=persisted.error_code or persisted.outcome,
                provider_calls=provider_calls,
            )

        paused = await _pause_execution_for_review(
            session_factory,
            organization_id=organization_id,
            execution_id=execution_id,
            now=now,
        )
        async with session_factory() as session:
            execution = await _load_execution(
                session, organization_id=organization_id, execution_id=execution_id
            )
        return SmokeRunResult(
            outcome=result.outcome,
            organization_id=organization_id,
            execution_id=execution_id,
            query_job_id=claimed.id,
            provider=provider_name,
            query_text=claimed.query_text,
            requested_page=provider_result.page_number,
            raw_provider_count=result.raw_provider_count,
            parsed_provider_count=result.parsed_provider_count,
            malformed_provider_count=result.malformed_provider_count,
            invalid_url_count=result.invalid_url_count,
            unsafe_url_count=result.unsafe_url_count,
            duplicate_within_query_count=result.duplicate_within_query_count,
            candidate_inserted_count=result.candidate_inserted_count,
            candidate_existing_count=result.candidate_existing_count,
            crawl_node_created_count=result.crawl_node_created_count,
            crawl_node_existing_count=result.crawl_node_existing_count,
            query_status=result.query_status,
            pages_completed=result.pages_completed,
            next_page_number=result.next_page_number,
            pagination_completed=result.pagination_completed,
            execution_status=(
                execution.status.value if execution and hasattr(execution.status, "value") else None
            ),
            execution_current_stage=execution.current_stage if execution else None,
            continuation_enqueued=False,
            mock_stages_executed=False,
            post_smoke_paused=paused,
            provider_calls=provider_calls,
            final_preview=preview_payload,
        )

    if (
        provider_result.provider_wide_blocker
        or provider_result.outcome in PROVIDER_WIDE_BLOCKERS_PROVIDER
    ):
        await claim_service.requeue_retryable_failure(
            organization_id=claimed.organization_id,
            execution_id=claimed.execution_id,
            query_job_id=claimed.id,
            claim_token=claimed.claim_token,
            error_code=provider_result.outcome,
            next_attempt_at=now,
            retry_policy=immediate_retry_policy(),
            now=now,
        )
        await _apply_provider_blocked_pause(
            session_factory,
            organization_id=organization_id,
            execution_id=execution_id,
            blocker_code=provider_result.outcome,
            now=now,
        )
        async with session_factory() as session:
            execution = await _load_execution(
                session, organization_id=organization_id, execution_id=execution_id
            )
        return SmokeRunResult(
            outcome="provider_blocked",
            organization_id=organization_id,
            execution_id=execution_id,
            query_job_id=claimed.id,
            provider=provider_name,
            query_text=claimed.query_text,
            requested_page=provider_result.page_number,
            execution_status=(
                execution.status.value if execution and hasattr(execution.status, "value") else None
            ),
            execution_current_stage=execution.current_stage if execution else None,
            continuation_enqueued=False,
            mock_stages_executed=False,
            error_code=provider_result.outcome,
            provider_calls=provider_calls,
        )

    if provider_result.retryable or provider_result.outcome in RETRYABLE_OUTCOMES:
        next_at = provider_result.retry_after_at
        if next_at is None and provider_result.retry_after_seconds is not None:
            next_at = now + timedelta(seconds=float(provider_result.retry_after_seconds))
        if next_at is None:
            next_at = now + timedelta(seconds=30)
        await claim_service.requeue_retryable_failure(
            organization_id=claimed.organization_id,
            execution_id=claimed.execution_id,
            query_job_id=claimed.id,
            claim_token=claimed.claim_token,
            error_code=provider_result.outcome,
            next_attempt_at=next_at,
            retry_policy=immediate_retry_policy(),
            now=now,
        )
        return SmokeRunResult(
            outcome="retry_scheduled",
            organization_id=organization_id,
            execution_id=execution_id,
            query_job_id=claimed.id,
            provider=provider_name,
            query_text=claimed.query_text,
            requested_page=provider_result.page_number,
            error_code=provider_result.outcome,
            continuation_enqueued=False,
            mock_stages_executed=False,
            provider_calls=provider_calls,
        )

    if (
        provider_result.query_terminal
        or provider_result.terminal
        or provider_result.outcome in QUERY_TERMINAL_OUTCOMES
    ):
        await claim_service.mark_terminal_failure(
            organization_id=claimed.organization_id,
            execution_id=claimed.execution_id,
            query_job_id=claimed.id,
            claim_token=claimed.claim_token,
            error_code=provider_result.outcome,
            now=now,
        )
        return SmokeRunResult(
            outcome="query_failed",
            organization_id=organization_id,
            execution_id=execution_id,
            query_job_id=claimed.id,
            provider=provider_name,
            query_text=claimed.query_text,
            requested_page=provider_result.page_number,
            error_code=provider_result.outcome,
            continuation_enqueued=False,
            mock_stages_executed=False,
            provider_calls=provider_calls,
        )

    await claim_service.mark_terminal_failure(
        organization_id=claimed.organization_id,
        execution_id=claimed.execution_id,
        query_job_id=claimed.id,
        claim_token=claimed.claim_token,
        error_code="unexpected_provider_failure",
        now=now,
    )
    return SmokeRunResult(
        outcome="unexpected_error",
        organization_id=organization_id,
        execution_id=execution_id,
        query_job_id=claimed.id,
        provider=provider_name,
        query_text=claimed.query_text,
        requested_page=provider_result.page_number,
        error_code="unexpected_provider_failure",
        continuation_enqueued=False,
        mock_stages_executed=False,
        provider_calls=provider_calls,
    )


async def verify_smoke(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    organization_id: str,
    execution_id: str,
    query_job_id: str,
) -> dict[str, Any]:
    async with session_factory() as session:
        job = await session.get(ScrapingSourceDiscoveryQuery, query_job_id)
        execution = await _load_execution(
            session, organization_id=organization_id, execution_id=execution_id
        )
        if job is None or execution is None or job.organization_id != organization_id:
            return {"ok": False, "error_code": "not_found"}

        candidates = (
            await session.execute(
                select(ScrapingSourceCandidate)
                .where(
                    ScrapingSourceCandidate.organization_id == organization_id,
                    ScrapingSourceCandidate.discovery_query_id == query_job_id,
                )
                .order_by(ScrapingSourceCandidate.rank.asc())
            )
        ).scalars().all()

        node_ids = {c.crawl_node_id for c in candidates if c.crawl_node_id}
        nodes: list[ScrapingCrawlNode] = []
        if node_ids:
            nodes = (
                await session.execute(
                    select(ScrapingCrawlNode).where(ScrapingCrawlNode.id.in_(node_ids))
                )
            ).scalars().all()

        events = (
            await session.execute(
                select(
                    ScrapingEvent.id,
                    ScrapingEvent.event_type,
                    ScrapingEvent.sequence_number,
                    ScrapingEvent.created_at,
                )
                .where(ScrapingEvent.execution_id == execution_id)
                .order_by(ScrapingEvent.sequence_number.asc())
            )
        ).all()

        web_events = [row.event_type for row in events if row.event_type.startswith("web_discovery_")]

        profile = dict(execution.country_profile_json or {})
        baseline_event_id = profile.get(PHASE4_SMOKE_EVENT_BASELINE_EVENT_ID_KEY)
        baseline_at = _parse_profile_datetime(profile.get(PHASE4_SMOKE_EVENT_BASELINE_AT_KEY))
        baseline_sequence: int | None = None
        if baseline_event_id:
            baseline_event = await session.get(ScrapingEvent, baseline_event_id)
            if baseline_event is not None:
                baseline_sequence = baseline_event.sequence_number

        historical_forbidden: list[str] = []
        forbidden_after_smoke: list[str] = []
        for row in events:
            if row.event_type not in FORBIDDEN_MOCK_STAGE_EVENTS:
                continue
            is_after = False
            if baseline_sequence is not None:
                is_after = row.sequence_number > baseline_sequence
            elif baseline_at is not None and row.created_at is not None:
                event_at = row.created_at
                if event_at.tzinfo is None:
                    event_at = event_at.replace(tzinfo=UTC)
                is_after = event_at > baseline_at
            if is_after:
                forbidden_after_smoke.append(row.event_type)
            else:
                historical_forbidden.append(row.event_type)

        sample_candidates = [
            {
                "rank": c.rank,
                "provider_page_number": c.provider_page_number,
                "original_url": c.url,
                "canonical_url": c.canonical_url,
                "status": c.status.value if hasattr(c.status, "value") else str(c.status),
            }
            for c in candidates[:5]
        ]
        sample_nodes = [
            {
                "canonical_url": n.canonical_url,
                "hostname": n.hostname,
                "source_classification": (
                    n.source_classification.value
                    if hasattr(n.source_classification, "value")
                    else str(n.source_classification)
                ),
            }
            for n in nodes[:5]
        ]

        profile = dict(execution.country_profile_json or {})

        provider_page_values = [c.provider_page_number for c in candidates]
        provider_page_numbers = sorted({p for p in provider_page_values if p is not None})
        candidate_provider_page_null_count = sum(1 for p in provider_page_values if p is None)
        expected_provider_page: int | None = None
        if bool(getattr(job, "pagination_completed", False)):
            expected_provider_page = int(
                getattr(job, "pages_completed", None)
                or getattr(job, "next_page_number", None)
                or 1
            )
        else:
            raw_next = getattr(job, "next_page_number", None)
            expected_provider_page = int(raw_next) if raw_next is not None else None

        candidate_provider_page_mismatch_count = 0
        if expected_provider_page is not None and candidates:
            candidate_provider_page_mismatch_count = sum(
                1
                for page in provider_page_values
                if page is not None and page != expected_provider_page
            )

        page_provenance_ok = True
        if candidates:
            page_provenance_ok = (
                candidate_provider_page_null_count == 0
                and candidate_provider_page_mismatch_count == 0
            )

        forbidden_after_present = bool(forbidden_after_smoke)
        ok = not forbidden_after_present and (not candidates or page_provenance_ok)

        return sanitize_public_mapping(
            {
                "ok": ok,
                "organization_id": organization_id,
                "execution_id": execution_id,
                "query_job_id": query_job_id,
                "query_status": job.status.value if hasattr(job.status, "value") else str(job.status),
                "provider": job.provider,
                "requested_at": job.requested_at.isoformat() if job.requested_at else None,
                "attempt_count": job.attempt_count,
                "next_page_number": getattr(job, "next_page_number", None),
                "pages_completed": getattr(job, "pages_completed", None),
                "pagination_completed": getattr(job, "pagination_completed", None),
                "last_page_result_count": getattr(job, "last_page_result_count", None),
                "expected_provider_page": expected_provider_page,
                "candidate_count": len(candidates),
                "provider_page_numbers": provider_page_numbers,
                "candidate_provider_page_null_count": candidate_provider_page_null_count,
                "candidate_provider_page_mismatch_count": candidate_provider_page_mismatch_count,
                "distinct_crawl_node_count": len(node_ids),
                "execution_status": (
                    execution.status.value if hasattr(execution.status, "value") else str(execution.status)
                ),
                "execution_current_stage": execution.current_stage,
                "provider_blocked": bool(profile.get(PROVIDER_BLOCK_PROFILE_KEY)),
                "provider_block_code": profile.get(PROVIDER_BLOCK_CODE_KEY),
                "phase4_smoke_prepared": bool(profile.get(PHASE4_SMOKE_PREPARED_KEY)),
                "sample_candidates": sample_candidates,
                "sample_crawl_nodes": sample_nodes,
                "web_discovery_events": web_events,
                "historical_forbidden_events_before_smoke": historical_forbidden,
                "forbidden_events_after_smoke": forbidden_after_smoke,
            }
        )


def _print_json(payload: Any) -> None:
    print(json.dumps(sanitize_public_mapping(payload if isinstance(payload, dict) else asdict(payload)), indent=2, default=str))


async def _cmd_preview(args: argparse.Namespace) -> int:
    eligible, inspected = await preview_executions(
        AsyncSessionLocal,
        organization_id=args.organization_id,
        execution_id=args.execution_id,
    )
    payload = {
        "eligible_executions": [asdict(r) for r in eligible],
        "legacy_reopen_candidates": [
            asdict(r) for r in inspected if r.legacy_step3b_reopen_candidate
        ],
        "all_inspected": [asdict(r) for r in inspected],
    }
    if not eligible:
        print("No safe eligible schema-v2 executions with pending Step 3B jobs found.")
        _print_json(payload)
        return 0
    _print_json(payload)
    return 0


async def _cmd_run(args: argparse.Namespace) -> int:
    if not args.confirm_real_serper or not args.acknowledge_worker_stopped:
        print("Refusing live run: require --confirm-real-serper and --acknowledge-worker-stopped.")
        return 2

    claim_service = SourceDiscoveryClaimService(session_factory=AsyncSessionLocal)
    provider_service = SourceDiscoveryProviderService()
    result_service = SourceDiscoveryResultService(session_factory=AsyncSessionLocal)

    result = await run_one_page_smoke(
        organization_id=args.organization_id,
        execution_id=args.execution_id,
        expected_query_job_id=args.expected_query_job_id,
        claim_service=claim_service,
        provider_service=provider_service,
        result_service=result_service,
        session_factory=AsyncSessionLocal,
        live_mode=True,
    )

    _print_json(result)
    if result.outcome in {"succeeded", "page_continued", "retry_scheduled", "provider_blocked"}:
        print("Keep scraping-worker stopped until ChatGPT reviews this output.")
        return 0
    return 1


async def _cmd_verify(args: argparse.Namespace) -> int:
    payload = await verify_smoke(
        AsyncSessionLocal,
        organization_id=args.organization_id,
        execution_id=args.execution_id,
        query_job_id=args.query_job_id,
    )
    _print_json(payload)
    return 0 if payload.get("ok") else 1


async def _cmd_prepare_existing(args: argparse.Namespace) -> int:
    if not args.confirm_reopen_step3b_execution or not args.acknowledge_worker_stopped:
        print(
            "Refusing prepare-existing: require --confirm-reopen-step3b-execution "
            "and --acknowledge-worker-stopped."
        )
        return 2

    result = await prepare_existing_execution(
        AsyncSessionLocal,
        organization_id=args.organization_id,
        execution_id=args.execution_id,
        expected_query_job_id=args.expected_query_job_id,
    )
    _print_json(result)
    if result.outcome in {"prepared", "already_prepared"}:
        print("Keep scraping-worker stopped until guarded smoke completes and is reviewed.")
        return 0
    return 1


async def _cmd_prepare_next(args: argparse.Namespace) -> int:
    if not args.confirm_prepare_next_job or not args.acknowledge_worker_stopped:
        print(
            "Refusing prepare-next: require --confirm-prepare-next-job "
            "and --acknowledge-worker-stopped."
        )
        return 2

    result = await prepare_next_smoke_job(
        AsyncSessionLocal,
        organization_id=args.organization_id,
        execution_id=args.execution_id,
        next_query_job_id=args.next_query_job_id,
    )
    _print_json(result)
    if result.outcome in {"prepared", "already_prepared"}:
        print("Keep scraping-worker stopped until the next guarded smoke run completes.")
        return 0
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guarded Phase 4 real Serper smoke runner.")
    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser("preview", help="Read-only eligibility inspection.")
    preview.add_argument("--organization-id", default=None)
    preview.add_argument("--execution-id", default=None)

    run = sub.add_parser("run", help="One real Serper page through production Phase 4 services.")
    run.add_argument("--organization-id", required=True)
    run.add_argument("--execution-id", required=True)
    run.add_argument("--expected-query-job-id", required=True)
    run.add_argument("--confirm-real-serper", action="store_true")
    run.add_argument("--acknowledge-worker-stopped", action="store_true")

    prepare = sub.add_parser(
        "prepare-existing",
        help="Reopen a historical mock-completed Step 3B execution for guarded smoke.",
    )
    prepare.add_argument("--organization-id", required=True)
    prepare.add_argument("--execution-id", required=True)
    prepare.add_argument("--expected-query-job-id", required=True)
    prepare.add_argument("--confirm-reopen-step3b-execution", action="store_true")
    prepare.add_argument("--acknowledge-worker-stopped", action="store_true")

    prepare_next = sub.add_parser(
        "prepare-next",
        help="Prepare the next pending Step 3B job after a successful smoke.",
    )
    prepare_next.add_argument("--organization-id", required=True)
    prepare_next.add_argument("--execution-id", required=True)
    prepare_next.add_argument("--next-query-job-id", required=True)
    prepare_next.add_argument("--confirm-prepare-next-job", action="store_true")
    prepare_next.add_argument("--acknowledge-worker-stopped", action="store_true")

    verify = sub.add_parser("verify", help="Read-only post-smoke verification.")
    verify.add_argument("--organization-id", required=True)
    verify.add_argument("--execution-id", required=True)
    verify.add_argument("--query-job-id", required=True)
    return parser


async def _async_main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "preview":
        return await _cmd_preview(args)
    if args.command == "run":
        return await _cmd_run(args)
    if args.command == "prepare-existing":
        return await _cmd_prepare_existing(args)
    if args.command == "prepare-next":
        return await _cmd_prepare_next(args)
    if args.command == "verify":
        return await _cmd_verify(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


def main() -> None:
    raise SystemExit(asyncio.run(_async_main()))


if __name__ == "__main__":
    main()
