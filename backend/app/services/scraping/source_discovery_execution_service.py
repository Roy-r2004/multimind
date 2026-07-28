"""Phase 4 Slice 6: production v2 discovery orchestration (work-slice lifecycle).

Coordinates Slice 2–5 without duplicating their logic. The mission-campaign worker
invokes ``run_discovery_work_slice``; provider HTTP never holds a DB transaction.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models import (
    ScrapingCrawlNode,
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingSourceCandidate,
    ScrapingSourceDiscoveryQuery,
    SourceDiscoveryQueryStatus,
)
from app.db.session import AsyncSessionLocal
from app.schemas.scraping_execution_plan import supports_deterministic_query_generation
from app.services.scraping.discovery_url_service import DiscoveryDnsResolver
from app.services.scraping.execution_service import execution_service
from app.services.scraping.source_discovery_claim_service import (
    ClaimBatchResult,
    ClaimedQueryJob,
    ClaimMutationResult,
    ClaimPreflightResult,
    RecoverExpiredClaimsResult,
    SourceDiscoveryClaimService,
    WorkInspectionResult,
    cancel_supersedes_pause,
    exponential_backoff_policy,
    immediate_retry_policy,
    normalize_lifecycle_error_code,
    source_discovery_claim_service,
)
from app.services.scraping.source_discovery_provider_service import (
    DiscoveryProviderExecutionResult,
    PROVIDER_WIDE_BLOCKERS,
    QUERY_TERMINAL_OUTCOMES,
    RETRYABLE_OUTCOMES,
    TERMINAL_OUTCOMES,
    SourceDiscoveryProviderService,
)
from app.services.scraping.source_discovery_result_service import (
    DiscoveryPersistenceResult,
    PreparedDiscoveryBatch,
    SourceDiscoveryResultService,
    prepare_provider_results,
)

logger = logging.getLogger(__name__)

NowFactory = Callable[[], datetime]
SleepFn = Callable[[float], Awaitable[None]]
QueueContinuationFn = Callable[..., Awaitable[None]]
EventEmitterFn = Callable[..., Awaitable[Any]]

V2_DISCOVERY_PROVIDER = "serper"
DISCOVERY_CONTINUATION_JOB_ID_PREFIX = "scraping-discovery:"

# Provider-wide blockers pause the execution; remaining pending jobs stay pending.
PROVIDER_WIDE_BLOCKERS_CODES: frozenset[str] = frozenset(PROVIDER_WIDE_BLOCKERS)

# Query-terminal: fail that query only (preserve earlier pages; do not claim pagination done).
QUERY_TERMINAL_CODES: frozenset[str] = frozenset(QUERY_TERMINAL_OUTCOMES)

# Legacy name kept for imports/tests that still reference config/auth terminals.
CONFIG_AUTH_TERMINAL: frozenset[str] = frozenset(
    {
        "provider_not_configured",
        "provider_authentication_failed",
    }
)

PROVIDER_BLOCK_PROFILE_KEY = "provider_blocked"
PROVIDER_BLOCK_CODE_KEY = "provider_block_code"
PROVIDER_BLOCK_PROVIDER_KEY = "provider_block_provider"
PROVIDER_BLOCK_STAGE_KEY = "provider_block_stage"

DiscoverySliceOutcome = Literal[
    "completed",
    "paused",
    "cancelled",
    "provider_blocked",
    "continue_enqueued",
    "delayed_continue_enqueued",
    "waiting_active_leases",
    "no_work",
    "lifecycle_blocked",
    "not_eligible",
    "not_found",
    "failed",
]


class ClaimPort(Protocol):
    async def recover_expired_claims(self, **kwargs: Any) -> RecoverExpiredClaimsResult: ...

    async def claim_eligible_jobs(self, **kwargs: Any) -> ClaimBatchResult: ...

    async def preflight_claimed_job(self, **kwargs: Any) -> ClaimPreflightResult: ...

    async def renew_claim(self, **kwargs: Any) -> ClaimMutationResult: ...

    async def requeue_retryable_failure(self, **kwargs: Any) -> ClaimMutationResult: ...

    async def mark_terminal_failure(self, **kwargs: Any) -> ClaimMutationResult: ...

    async def inspect_remaining_work(self, **kwargs: Any) -> WorkInspectionResult: ...


class ProviderPort(Protocol):
    async def execute_claimed_query(
        self,
        claimed_job: ClaimedQueryJob,
        provider_name: str,
        *,
        result_page_size: int | None = None,
    ) -> DiscoveryProviderExecutionResult: ...


class ResultPort(Protocol):
    async def persist_prepared_batch_and_succeed(
        self,
        prepared_batch: PreparedDiscoveryBatch,
        *,
        now: datetime | None = None,
    ) -> DiscoveryPersistenceResult: ...

    async def persist_page_and_continue(
        self,
        prepared_batch: PreparedDiscoveryBatch,
        *,
        now: datetime | None = None,
        next_attempt_at: datetime | None = None,
    ) -> DiscoveryPersistenceResult: ...

    async def persist_final_page_and_succeed(
        self,
        prepared_batch: PreparedDiscoveryBatch,
        *,
        now: datetime | None = None,
    ) -> DiscoveryPersistenceResult: ...


PrepareFn = Callable[..., PreparedDiscoveryBatch]


@dataclass(frozen=True)
class DiscoveryWorkSliceCounts:
    recovered_count: int = 0
    claimed_count: int = 0
    succeeded_count: int = 0
    retry_scheduled_count: int = 0
    failed_count: int = 0
    paused_released_count: int = 0
    cancelled_released_count: int = 0
    stale_skipped_count: int = 0
    provider_calls: int = 0
    candidates_persisted: int = 0
    crawl_nodes_persisted: int = 0
    pending_eligible_remaining: int = 0
    pending_delayed_remaining: int = 0
    running_remaining: int = 0
    query_succeeded_total: int = 0
    query_failed_total: int = 0
    candidate_total: int = 0
    crawl_node_total: int = 0


@dataclass(frozen=True)
class DiscoveryWorkSliceResult:
    outcome: DiscoverySliceOutcome
    organization_id: str
    execution_id: str
    counts: DiscoveryWorkSliceCounts = field(default_factory=DiscoveryWorkSliceCounts)
    error_code: str | None = None
    lifecycle_reason: str | None = None
    next_attempt_at: datetime | None = None
    continuation_enqueued: bool = False


@dataclass
class _MutableSliceCounts:
    recovered_count: int = 0
    claimed_count: int = 0
    succeeded_count: int = 0
    page_continued_count: int = 0
    retry_scheduled_count: int = 0
    failed_count: int = 0
    paused_released_count: int = 0
    cancelled_released_count: int = 0
    stale_skipped_count: int = 0
    provider_calls: int = 0
    candidates_persisted: int = 0
    crawl_nodes_persisted: int = 0
    stop_new_provider_http: bool = False
    pause_requested: bool = False
    cancel_requested: bool = False
    provider_blocked: bool = False
    provider_block_code: str | None = None
    provider_blocked_released_count: int = 0

    def freeze(
        self,
        *,
        inspection: WorkInspectionResult | None = None,
        candidate_total: int = 0,
        crawl_node_total: int = 0,
    ) -> DiscoveryWorkSliceCounts:
        return DiscoveryWorkSliceCounts(
            recovered_count=self.recovered_count,
            claimed_count=self.claimed_count,
            succeeded_count=self.succeeded_count,
            retry_scheduled_count=self.retry_scheduled_count,
            failed_count=self.failed_count,
            paused_released_count=self.paused_released_count,
            cancelled_released_count=self.cancelled_released_count,
            stale_skipped_count=self.stale_skipped_count,
            provider_calls=self.provider_calls,
            candidates_persisted=self.candidates_persisted,
            crawl_nodes_persisted=self.crawl_nodes_persisted,
            pending_eligible_remaining=(
                inspection.pending_eligible_count if inspection is not None else 0
            ),
            pending_delayed_remaining=(
                inspection.pending_delayed_count if inspection is not None else 0
            ),
            running_remaining=inspection.running_count if inspection is not None else 0,
            query_succeeded_total=(
                inspection.succeeded_count if inspection is not None else 0
            ),
            query_failed_total=inspection.failed_count if inspection is not None else 0,
            candidate_total=candidate_total,
            crawl_node_total=crawl_node_total,
        )


class SourceDiscoveryExecutionService:
    """Bounded Phase 4 discovery work-slice orchestrator for schema-v2 campaigns."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        claim_service: ClaimPort | None = None,
        provider_service: ProviderPort | None = None,
        result_service: ResultPort | None = None,
        prepare_fn: PrepareFn | None = None,
        dns_resolver: DiscoveryDnsResolver | None = None,
        now_factory: NowFactory | None = None,
        sleep_fn: SleepFn | None = None,
        queue_continuation: QueueContinuationFn | None = None,
        event_emitter: EventEmitterFn | None = None,
        claim_batch_size: int | None = None,
        provider_concurrency: int | None = None,
        recovery_batch_size: int | None = None,
        lease_duration: timedelta | None = None,
        provider_name: str = V2_DISCOVERY_PROVIDER,
    ) -> None:
        settings = get_settings()
        self._session_factory = session_factory or AsyncSessionLocal
        if claim_service is not None:
            self._claim_service = claim_service
        elif session_factory is not None:
            self._claim_service = SourceDiscoveryClaimService(
                session_factory=session_factory
            )
        else:
            self._claim_service = source_discovery_claim_service
        self._provider_service = provider_service or SourceDiscoveryProviderService()
        if result_service is not None:
            self._result_service = result_service
        else:
            self._result_service = SourceDiscoveryResultService(
                session_factory=self._session_factory
            )
        self._prepare_fn = prepare_fn or prepare_provider_results
        self._dns_resolver = dns_resolver
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._sleep_fn = sleep_fn or asyncio.sleep
        self._queue_continuation = queue_continuation or self._default_queue_continuation
        self._event_emitter = event_emitter or self._default_emit_event
        self._claim_batch_size = (
            claim_batch_size
            if claim_batch_size is not None
            else settings.scraping_discovery_claim_batch_size
        )
        self._provider_concurrency = (
            provider_concurrency
            if provider_concurrency is not None
            else settings.scraping_discovery_provider_concurrency
        )
        if self._provider_concurrency < 1:
            raise ValueError("provider concurrency must be >= 1")
        self._recovery_batch_size = (
            recovery_batch_size
            if recovery_batch_size is not None
            else settings.scraping_discovery_recovery_batch_size
        )
        self._lease_duration = lease_duration or timedelta(
            seconds=settings.scraping_discovery_lease_seconds
        )
        self._provider_name = provider_name.strip().lower() or V2_DISCOVERY_PROVIDER
        self._retry_policy = exponential_backoff_policy(
            base=timedelta(seconds=settings.scraping_discovery_retry_base_seconds),
            factor=2.0,
            max_delay=timedelta(seconds=900),
        )

    async def run_discovery_work_slice(
        self,
        organization_id: str,
        execution_id: str,
        *,
        claim_batch_size: int | None = None,
        provider_concurrency: int | None = None,
        recovery_batch_size: int | None = None,
    ) -> DiscoveryWorkSliceResult:
        """Run one bounded discovery work slice for a schema-v2 mission campaign."""
        if self._provider_concurrency < 1 and provider_concurrency is None:
            raise ValueError("provider concurrency must be >= 1")
        concurrency = provider_concurrency or self._provider_concurrency
        if concurrency < 1:
            raise ValueError("provider concurrency must be >= 1")
        batch_size = claim_batch_size or self._claim_batch_size
        recovery_size = recovery_batch_size or self._recovery_batch_size
        counts = _MutableSliceCounts()
        now = self._now_factory()

        gate = await self._validate_active_v2_campaign(organization_id, execution_id)
        if gate is not None:
            return gate

        lifecycle = await self._load_execution_lifecycle(organization_id, execution_id)
        if lifecycle is None:
            return DiscoveryWorkSliceResult(
                outcome="not_found",
                organization_id=organization_id,
                execution_id=execution_id,
                error_code="execution_not_found",
            )
        blocked = self._lifecycle_block_reason(lifecycle)
        if blocked == "cancelled":
            await self._acknowledge_cancelled(organization_id, execution_id)
            return DiscoveryWorkSliceResult(
                outcome="cancelled",
                organization_id=organization_id,
                execution_id=execution_id,
                lifecycle_reason="cancelled",
            )
        if blocked == "paused":
            await self._acknowledge_paused(organization_id, execution_id)
            return DiscoveryWorkSliceResult(
                outcome="paused",
                organization_id=organization_id,
                execution_id=execution_id,
                lifecycle_reason="paused",
            )
        if blocked in {"completed", "failed"}:
            return DiscoveryWorkSliceResult(
                outcome="lifecycle_blocked",
                organization_id=organization_id,
                execution_id=execution_id,
                lifecycle_reason=blocked,
            )
        if blocked is not None:
            return DiscoveryWorkSliceResult(
                outcome="not_eligible",
                organization_id=organization_id,
                execution_id=execution_id,
                lifecycle_reason=blocked,
            )

        await self._ensure_discovery_stage(organization_id, execution_id)
        await self._promote_queued_to_running(organization_id, execution_id)
        await self._clear_provider_blocked_state(organization_id, execution_id)
        await self._emit(
            execution_id,
            "web_discovery_started",
            "Phase 4 web discovery work slice started.",
            metadata={"provider": self._provider_name},
        )

        recovered = await self._claim_service.recover_expired_claims(
            organization_id=organization_id,
            execution_id=execution_id,
            batch_size=recovery_size,
            recovery_policy=immediate_retry_policy(),
            now=now,
        )
        counts.recovered_count = int(recovered.recovered_count or 0)

        # Re-check pause/cancel after recovery before claiming.
        lifecycle = await self._load_execution_lifecycle(organization_id, execution_id)
        if lifecycle is not None:
            blocked = self._lifecycle_block_reason(lifecycle)
            if blocked == "cancelled":
                await self._acknowledge_cancelled(organization_id, execution_id)
                return await self._finish_with_inspection(
                    organization_id,
                    execution_id,
                    outcome="cancelled",
                    counts=counts,
                    lifecycle_reason="cancelled",
                )
            if blocked == "paused":
                await self._acknowledge_paused(organization_id, execution_id)
                return await self._finish_with_inspection(
                    organization_id,
                    execution_id,
                    outcome="paused",
                    counts=counts,
                    lifecycle_reason="paused",
                )

        claim = await self._claim_service.claim_eligible_jobs(
            organization_id=organization_id,
            execution_id=execution_id,
            provider=self._provider_name,
            batch_size=batch_size,
            lease_duration=self._lease_duration,
            now=self._now_factory(),
        )
        # Claim TX is committed inside claim_eligible_jobs before we touch providers.

        if claim.outcome == "lifecycle_blocked":
            reason = claim.lifecycle_reason or "not_eligible"
            if reason == "cancelled":
                await self._acknowledge_cancelled(organization_id, execution_id)
                await self._emit(
                    execution_id,
                    "web_discovery_cancelled",
                    "Web discovery cancelled before claim.",
                    metadata={"lifecycle_reason": reason},
                )
                return await self._finish_with_inspection(
                    organization_id,
                    execution_id,
                    outcome="cancelled",
                    counts=counts,
                    lifecycle_reason=reason,
                )
            if reason == "paused":
                await self._acknowledge_paused(organization_id, execution_id)
                await self._emit(
                    execution_id,
                    "web_discovery_paused",
                    "Web discovery paused before claim.",
                    metadata={"lifecycle_reason": reason},
                )
                return await self._finish_with_inspection(
                    organization_id,
                    execution_id,
                    outcome="paused",
                    counts=counts,
                    lifecycle_reason=reason,
                )
            return await self._finish_with_inspection(
                organization_id,
                execution_id,
                outcome="lifecycle_blocked",
                counts=counts,
                lifecycle_reason=reason,
            )

        jobs: tuple[ClaimedQueryJob, ...] = claim.jobs if claim.outcome == "claimed" else ()
        counts.claimed_count = len(jobs)
        if jobs:
            await self._emit(
                execution_id,
                "web_discovery_batch_claimed",
                "Web discovery claimed a bounded query batch.",
                metadata={
                    "claimed_count": counts.claimed_count,
                    "provider": self._provider_name,
                    "recovered_count": counts.recovered_count,
                },
            )

        if jobs:
            await self._process_claimed_jobs(
                jobs,
                concurrency=concurrency,
                counts=counts,
            )
            await self._emit(
                execution_id,
                "web_discovery_batch_completed",
                "Web discovery finished processing the claimed batch.",
                metadata={
                    "claimed_count": counts.claimed_count,
                    "succeeded_count": counts.succeeded_count,
                    "retry_scheduled_count": counts.retry_scheduled_count,
                    "failed_count": counts.failed_count,
                    "provider_calls": counts.provider_calls,
                },
            )

        if counts.cancel_requested:
            await self._acknowledge_cancelled(organization_id, execution_id)
            await self._emit(
                execution_id,
                "web_discovery_cancelled",
                "Web discovery cancelled.",
                metadata={"cancelled_released_count": counts.cancelled_released_count},
            )
            return await self._finish_with_inspection(
                organization_id,
                execution_id,
                outcome="cancelled",
                counts=counts,
                lifecycle_reason="cancelled",
            )

        if counts.provider_blocked:
            # Recoverable blocked state: pause execution; do NOT enqueue continuation.
            released = (
                counts.provider_blocked_released_count + counts.paused_released_count
            )
            await self._acknowledge_provider_blocked(
                organization_id,
                execution_id,
                blocker_code=counts.provider_block_code or "provider_not_configured",
                released_count=released,
            )
            return await self._finish_with_inspection(
                organization_id,
                execution_id,
                outcome="provider_blocked",
                counts=counts,
                lifecycle_reason="paused",
                error_code=counts.provider_block_code,
            )

        if counts.pause_requested:
            await self._acknowledge_paused(organization_id, execution_id)
            await self._emit(
                execution_id,
                "web_discovery_paused",
                "Web discovery paused at a safe boundary.",
                metadata={"paused_released_count": counts.paused_released_count},
            )
            return await self._finish_with_inspection(
                organization_id,
                execution_id,
                outcome="paused",
                counts=counts,
                lifecycle_reason="paused",
            )

        return await self._decide_continuation(organization_id, execution_id, counts)

    async def _process_claimed_jobs(
        self,
        jobs: Sequence[ClaimedQueryJob],
        *,
        concurrency: int,
        counts: _MutableSliceCounts,
    ) -> None:
        """Process claimed jobs with bounded concurrency; preserve claim order for starts."""
        semaphore = asyncio.Semaphore(concurrency)
        # Start tasks in claim order; completion may reorder.
        tasks = [
            asyncio.create_task(
                self._run_one_job_guarded(job, semaphore=semaphore, counts=counts)
            )
            for job in jobs
        ]
        if tasks:
            await asyncio.gather(*tasks)

    async def _run_one_job_guarded(
        self,
        job: ClaimedQueryJob,
        *,
        semaphore: asyncio.Semaphore,
        counts: _MutableSliceCounts,
    ) -> None:
        async with semaphore:
            try:
                await self._process_one_job(job, counts=counts)
            except Exception:
                logger.exception(
                    "discovery_job_unexpected_failure execution_id=%s query_job_id=%s",
                    job.execution_id,
                    job.id,
                )
                counts.failed_count += 1
                try:
                    await self._claim_service.mark_terminal_failure(
                        organization_id=job.organization_id,
                        execution_id=job.execution_id,
                        query_job_id=job.id,
                        claim_token=job.claim_token,
                        error_code="unexpected_provider_failure",
                        now=self._now_factory(),
                    )
                except Exception:
                    logger.exception(
                        "discovery_job_terminal_transition_failed execution_id=%s",
                        job.execution_id,
                    )
                await self._emit(
                    job.execution_id,
                    "web_discovery_job_failed",
                    "Web discovery job failed unexpectedly.",
                    metadata={
                        "query_job_id": job.id,
                        "error_code": "unexpected_provider_failure",
                        "provider": self._provider_name,
                    },
                )

    async def _process_one_job(
        self,
        job: ClaimedQueryJob,
        *,
        counts: _MutableSliceCounts,
    ) -> None:
        if counts.cancel_requested:
            await self._release_cancelled(job, counts)
            return
        if counts.provider_blocked or counts.stop_new_provider_http:
            # Provider-wide blocker already observed: release without more HTTP.
            # Already-started in-flight siblings may still finish their HTTP call;
            # they must not invent success and should requeue/preserve page cursor.
            await self._release_provider_blocked(job, counts)
            return
        if counts.pause_requested:
            await self._release_paused(job, counts)
            return

        preflight = await self._claim_service.preflight_claimed_job(
            organization_id=job.organization_id,
            execution_id=job.execution_id,
            query_job_id=job.id,
            claim_token=job.claim_token,
            now=self._now_factory(),
        )
        if preflight.outcome == "cancelled":
            counts.cancel_requested = True
            await self._release_cancelled(job, counts)
            return
        if preflight.outcome == "paused":
            counts.pause_requested = True
            await self._release_paused(job, counts)
            return
        if preflight.outcome == "stale_claim":
            counts.stale_skipped_count += 1
            return
        if preflight.outcome != "ok":
            if preflight.outcome in {"completed", "failed", "not_eligible", "not_found"}:
                counts.cancel_requested = preflight.outcome in {"not_found"}
            counts.stale_skipped_count += 1
            return

        # Finite request timeout lives in the Serper adapter. Renew lease
        # token-protected before HTTP; no background unbounded heartbeat; no TX during HTTP.
        renew = await self._claim_service.renew_claim(
            organization_id=job.organization_id,
            execution_id=job.execution_id,
            query_job_id=job.id,
            claim_token=job.claim_token,
            lease_duration=self._lease_duration,
            now=self._now_factory(),
        )
        if renew.outcome == "stale_claim":
            counts.stale_skipped_count += 1
            return
        if renew.outcome not in {"applied"}:
            counts.stale_skipped_count += 1
            return

        if counts.provider_blocked or counts.stop_new_provider_http:
            await self._release_provider_blocked(job, counts)
            return

        # Provider HTTP — no DB session held.
        counts.provider_calls += 1
        provider_result = await self._provider_service.execute_claimed_query(
            job,
            self._provider_name,
        )

        # After HTTP: cancel blocks late persist; pause allows finalize of started work.
        lifecycle = await self._load_execution_lifecycle(
            job.organization_id, job.execution_id
        )
        if lifecycle is not None:
            if self._is_cancelled(lifecycle):
                counts.cancel_requested = True
                await self._release_cancelled(job, counts)
                return
            if self._is_pause_requested(lifecycle):
                counts.pause_requested = True

        if provider_result.succeeded:
            await self._handle_success(job, provider_result, counts)
            return
        if provider_result.retryable or provider_result.outcome in RETRYABLE_OUTCOMES:
            await self._handle_retryable(job, provider_result, counts)
            return
        if (
            provider_result.provider_wide_blocker
            or provider_result.outcome in PROVIDER_WIDE_BLOCKERS_CODES
        ):
            await self._handle_provider_blocker(job, provider_result, counts)
            return
        if (
            provider_result.query_terminal
            or provider_result.outcome in QUERY_TERMINAL_CODES
            or provider_result.terminal
            or provider_result.outcome in TERMINAL_OUTCOMES
        ):
            await self._handle_terminal(job, provider_result, counts)
            return

        await self._claim_service.mark_terminal_failure(
            organization_id=job.organization_id,
            execution_id=job.execution_id,
            query_job_id=job.id,
            claim_token=job.claim_token,
            error_code="unexpected_provider_failure",
            now=self._now_factory(),
        )
        counts.failed_count += 1
        await self._emit(
            job.execution_id,
            "web_discovery_job_failed",
            "Web discovery job failed.",
            metadata={
                "query_job_id": job.id,
                "error_code": "unexpected_provider_failure",
                "provider": self._provider_name,
            },
        )

    async def _handle_success(
        self,
        job: ClaimedQueryJob,
        provider_result: DiscoveryProviderExecutionResult,
        counts: _MutableSliceCounts,
    ) -> None:
        prepared = self._prepare_fn(
            job,
            provider_result,
            resolver=self._dns_resolver,
            require_dns=False,
            clock=self._now_factory(),
        )
        if not prepared.ready:
            await self._claim_service.mark_terminal_failure(
                organization_id=job.organization_id,
                execution_id=job.execution_id,
                query_job_id=job.id,
                claim_token=job.claim_token,
                error_code=normalize_lifecycle_error_code(
                    prepared.error_code or "invalid_provider_batch"
                ),
                now=self._now_factory(),
            )
            counts.failed_count += 1
            await self._emit(
                job.execution_id,
                "web_discovery_job_failed",
                "Web discovery preparation rejected the provider batch.",
                metadata={
                    "query_job_id": job.id,
                    "error_code": prepared.error_code or "invalid_provider_batch",
                    "provider": self._provider_name,
                },
            )
            return

        continuation = provider_result.continuation
        has_more = bool(continuation is not None and continuation.has_more)
        # If a provider-wide blocker landed mid-batch after this HTTP started,
        # still finalize the already-started page (pause allows late persist).
        try:
            if has_more:
                persisted = await self._result_service.persist_page_and_continue(
                    prepared,
                    now=self._now_factory(),
                    next_attempt_at=self._now_factory(),
                )
            else:
                persisted = await self._result_service.persist_final_page_and_succeed(
                    prepared,
                    now=self._now_factory(),
                )
        except Exception:
            logger.exception(
                "discovery_persistence_unexpected execution_id=%s query_job_id=%s",
                job.execution_id,
                job.id,
            )
            await self._claim_service.mark_terminal_failure(
                organization_id=job.organization_id,
                execution_id=job.execution_id,
                query_job_id=job.id,
                claim_token=job.claim_token,
                error_code="discovery_persistence_failure",
                now=self._now_factory(),
            )
            counts.failed_count += 1
            await self._emit(
                job.execution_id,
                "web_discovery_job_failed",
                "Web discovery persistence failed.",
                metadata={
                    "query_job_id": job.id,
                    "error_code": "discovery_persistence_failure",
                    "provider": self._provider_name,
                },
            )
            return

        await self._map_persistence_outcome(job, persisted, counts)

    async def _map_persistence_outcome(
        self,
        job: ClaimedQueryJob,
        persisted: DiscoveryPersistenceResult,
        counts: _MutableSliceCounts,
    ) -> None:
        outcome = persisted.outcome
        if outcome == "page_continued":
            counts.page_continued_count += 1
            counts.candidates_persisted += int(
                persisted.counts.candidate_inserted_count
                + persisted.counts.candidate_existing_count
            )
            counts.crawl_nodes_persisted += int(
                persisted.counts.crawl_node_created_count
                + persisted.counts.crawl_node_existing_count
            )
            await self._emit(
                job.execution_id,
                "web_discovery_page_persisted",
                "Web discovery page persisted; more pages may exist.",
                metadata={
                    "query_job_id": job.id,
                    "provider": self._provider_name,
                    "pages_completed": persisted.counts.pages_completed,
                    "next_page_number": persisted.counts.next_page_number,
                    "candidate_inserted_count": persisted.counts.candidate_inserted_count,
                },
            )
            return
        if outcome in {"applied", "idempotent_replay"}:
            counts.succeeded_count += 1
            counts.candidates_persisted += int(
                persisted.counts.candidate_inserted_count
                + persisted.counts.candidate_existing_count
            )
            counts.crawl_nodes_persisted += int(
                persisted.counts.crawl_node_created_count
                + persisted.counts.crawl_node_existing_count
            )
            await self._emit(
                job.execution_id,
                "web_discovery_job_succeeded",
                "Web discovery query succeeded.",
                metadata={
                    "query_job_id": job.id,
                    "provider": self._provider_name,
                    "candidate_inserted_count": persisted.counts.candidate_inserted_count,
                    "crawl_node_created_count": persisted.counts.crawl_node_created_count,
                    "persisted_count": persisted.counts.persisted_count,
                    "pagination_completed": True,
                },
            )
            return
        if outcome == "stale_claim":
            counts.stale_skipped_count += 1
            return
        if outcome == "lifecycle_blocked":
            reason = persisted.lifecycle_reason or "cancelled"
            if reason == "cancelled":
                counts.cancel_requested = True
                await self._release_cancelled(job, counts)
            else:
                counts.failed_count += 1
                await self._claim_service.mark_terminal_failure(
                    organization_id=job.organization_id,
                    execution_id=job.execution_id,
                    query_job_id=job.id,
                    claim_token=job.claim_token,
                    error_code=normalize_lifecycle_error_code(
                        persisted.error_code or "execution_terminal"
                    ),
                    now=self._now_factory(),
                )
            return
        if outcome in {"hash_collision", "persistence_conflict", "database_failure", "rejected"}:
            code = normalize_lifecycle_error_code(
                persisted.error_code or "discovery_persistence_failure"
            )
            mutation = await self._claim_service.mark_terminal_failure(
                organization_id=job.organization_id,
                execution_id=job.execution_id,
                query_job_id=job.id,
                claim_token=job.claim_token,
                error_code=code,
                now=self._now_factory(),
            )
            if mutation.outcome == "stale_claim":
                counts.stale_skipped_count += 1
                return
            counts.failed_count += 1
            await self._emit(
                job.execution_id,
                "web_discovery_job_failed",
                "Web discovery persistence failed for one query.",
                metadata={
                    "query_job_id": job.id,
                    "error_code": code,
                    "provider": self._provider_name,
                },
            )
            return
        counts.failed_count += 1

    async def _handle_retryable(
        self,
        job: ClaimedQueryJob,
        provider_result: DiscoveryProviderExecutionResult,
        counts: _MutableSliceCounts,
    ) -> None:
        # Retryable on page N: keep next_page_number; do not increment pages_completed
        # (requeue clears claim only — pagination cursor stays on the row).
        error_code = normalize_lifecycle_error_code(provider_result.outcome)
        next_at = provider_result.retry_after_at
        if next_at is None and provider_result.retry_after_seconds is not None:
            next_at = self._now_factory() + timedelta(
                seconds=float(provider_result.retry_after_seconds)
            )
        if next_at is None:
            next_at = self._retry_policy(self._now_factory(), int(job.attempt_count or 1))

        mutation = await self._claim_service.requeue_retryable_failure(
            organization_id=job.organization_id,
            execution_id=job.execution_id,
            query_job_id=job.id,
            claim_token=job.claim_token,
            error_code=error_code,
            next_attempt_at=next_at,
            now=self._now_factory(),
        )
        if mutation.outcome == "stale_claim":
            counts.stale_skipped_count += 1
            return
        if mutation.outcome != "applied":
            counts.failed_count += 1
            return
        counts.retry_scheduled_count += 1
        await self._emit(
            job.execution_id,
            "web_discovery_job_retry_scheduled",
            "Web discovery query scheduled for retry.",
            metadata={
                "query_job_id": job.id,
                "error_code": error_code,
                "provider": self._provider_name,
                "next_attempt_at": next_at.isoformat(),
                "attempt_count": mutation.attempt_count,
                "page_number": getattr(job, "next_page_number", None),
            },
        )

    async def _handle_provider_blocker(
        self,
        job: ClaimedQueryJob,
        provider_result: DiscoveryProviderExecutionResult,
        counts: _MutableSliceCounts,
    ) -> None:
        """Provider-wide blocker: requeue this job at current page; pause execution.

        Does not permanently fail remaining pending jobs. Does not fabricate or
        switch providers. Already-started in-flight siblings may complete HTTP;
        not-yet-started claimed jobs are released via claim tokens.
        """
        error_code = normalize_lifecycle_error_code(provider_result.outcome)
        counts.provider_blocked = True
        counts.provider_block_code = error_code
        counts.stop_new_provider_http = True

        mutation = await self._claim_service.requeue_retryable_failure(
            organization_id=job.organization_id,
            execution_id=job.execution_id,
            query_job_id=job.id,
            claim_token=job.claim_token,
            error_code=error_code,
            next_attempt_at=self._now_factory(),
            retry_policy=immediate_retry_policy(),
            now=self._now_factory(),
        )
        if mutation.outcome == "stale_claim":
            counts.stale_skipped_count += 1
            return
        if mutation.outcome == "applied":
            counts.provider_blocked_released_count += 1
        await self._emit(
            job.execution_id,
            "web_discovery_job_blocked",
            "Web discovery query blocked by provider configuration or auth.",
            metadata={
                "query_job_id": job.id,
                "error_code": error_code,
                "provider": self._provider_name,
                "page_number": getattr(job, "next_page_number", None),
            },
        )

    async def _handle_terminal(
        self,
        job: ClaimedQueryJob,
        provider_result: DiscoveryProviderExecutionResult,
        counts: _MutableSliceCounts,
    ) -> None:
        """Query-terminal failure: fail this job only; preserve earlier pages.

        Does not set pagination_completed=true. Does not fail the campaign.
        """
        error_code = normalize_lifecycle_error_code(provider_result.outcome)
        mutation = await self._claim_service.mark_terminal_failure(
            organization_id=job.organization_id,
            execution_id=job.execution_id,
            query_job_id=job.id,
            claim_token=job.claim_token,
            error_code=error_code,
            now=self._now_factory(),
        )
        if mutation.outcome == "stale_claim":
            counts.stale_skipped_count += 1
            return
        counts.failed_count += 1
        await self._emit(
            job.execution_id,
            "web_discovery_job_failed",
            "Web discovery query failed terminally.",
            metadata={
                "query_job_id": job.id,
                "error_code": error_code,
                "provider": self._provider_name,
                "page_number": getattr(job, "next_page_number", None),
            },
        )

    async def _release_provider_blocked(
        self, job: ClaimedQueryJob, counts: _MutableSliceCounts
    ) -> None:
        mutation = await self._claim_service.requeue_retryable_failure(
            organization_id=job.organization_id,
            execution_id=job.execution_id,
            query_job_id=job.id,
            claim_token=job.claim_token,
            error_code=normalize_lifecycle_error_code(
                counts.provider_block_code or "provider_not_configured"
            ),
            next_attempt_at=self._now_factory(),
            retry_policy=immediate_retry_policy(),
            now=self._now_factory(),
        )
        if mutation.outcome == "applied":
            counts.provider_blocked_released_count += 1
        elif mutation.outcome == "stale_claim":
            counts.stale_skipped_count += 1

    async def _release_paused(
        self, job: ClaimedQueryJob, counts: _MutableSliceCounts
    ) -> None:
        mutation = await self._claim_service.requeue_retryable_failure(
            organization_id=job.organization_id,
            execution_id=job.execution_id,
            query_job_id=job.id,
            claim_token=job.claim_token,
            error_code="paused_before_request",
            next_attempt_at=self._now_factory(),
            retry_policy=immediate_retry_policy(),
            now=self._now_factory(),
        )
        if mutation.outcome == "applied":
            counts.paused_released_count += 1
        elif mutation.outcome == "stale_claim":
            counts.stale_skipped_count += 1

    async def _release_cancelled(
        self, job: ClaimedQueryJob, counts: _MutableSliceCounts
    ) -> None:
        mutation = await self._claim_service.mark_terminal_failure(
            organization_id=job.organization_id,
            execution_id=job.execution_id,
            query_job_id=job.id,
            claim_token=job.claim_token,
            error_code="cancelled_before_request",
            now=self._now_factory(),
        )
        if mutation.outcome == "applied":
            counts.cancelled_released_count += 1
        elif mutation.outcome == "stale_claim":
            counts.stale_skipped_count += 1

    async def _decide_continuation(
        self,
        organization_id: str,
        execution_id: str,
        counts: _MutableSliceCounts,
    ) -> DiscoveryWorkSliceResult:
        inspection = await self._claim_service.inspect_remaining_work(
            organization_id=organization_id,
            execution_id=execution_id,
            now=self._now_factory(),
        )
        candidate_total, crawl_node_total = await self._count_artifacts(
            organization_id, execution_id
        )
        frozen = counts.freeze(
            inspection=inspection if inspection.outcome != "not_found" else None,
            candidate_total=candidate_total,
            crawl_node_total=crawl_node_total,
        )

        if inspection.outcome == "lifecycle_blocked":
            reason = inspection.lifecycle_reason or "not_eligible"
            if reason == "cancelled":
                await self._acknowledge_cancelled(organization_id, execution_id)
                return DiscoveryWorkSliceResult(
                    outcome="cancelled",
                    organization_id=organization_id,
                    execution_id=execution_id,
                    counts=frozen,
                    lifecycle_reason=reason,
                )
            if reason == "paused":
                await self._acknowledge_paused(organization_id, execution_id)
                return DiscoveryWorkSliceResult(
                    outcome="paused",
                    organization_id=organization_id,
                    execution_id=execution_id,
                    counts=frozen,
                    lifecycle_reason=reason,
                )
            return DiscoveryWorkSliceResult(
                outcome="lifecycle_blocked",
                organization_id=organization_id,
                execution_id=execution_id,
                counts=frozen,
                lifecycle_reason=reason,
            )

        pending_eligible = inspection.pending_eligible_count
        pending_delayed = inspection.pending_delayed_count
        running = inspection.running_count
        expired = inspection.expired_lease_count

        if pending_eligible > 0 or expired > 0:
            await self._requeue_execution(organization_id, execution_id, defer_until=None)
            return DiscoveryWorkSliceResult(
                outcome="continue_enqueued",
                organization_id=organization_id,
                execution_id=execution_id,
                counts=frozen,
                continuation_enqueued=True,
            )

        if pending_delayed > 0 and running == 0:
            next_at = inspection.earliest_next_attempt_at or (
                self._now_factory() + timedelta(seconds=30)
            )
            await self._requeue_execution(
                organization_id, execution_id, defer_until=next_at
            )
            return DiscoveryWorkSliceResult(
                outcome="delayed_continue_enqueued",
                organization_id=organization_id,
                execution_id=execution_id,
                counts=frozen,
                next_attempt_at=next_at,
                continuation_enqueued=True,
            )

        if running > 0:
            # Active leases remain — do not declare completion; schedule a short
            # deferred slice so lease recovery can progress without busy-looping.
            defer_until = self._now_factory() + timedelta(seconds=15)
            await self._requeue_execution(
                organization_id, execution_id, defer_until=defer_until
            )
            return DiscoveryWorkSliceResult(
                outcome="waiting_active_leases",
                organization_id=organization_id,
                execution_id=execution_id,
                counts=frozen,
                next_attempt_at=defer_until,
                continuation_enqueued=True,
            )

        incomplete_pagination = await self._count_incomplete_pagination(
            organization_id, execution_id
        )
        if incomplete_pagination > 0:
            await self._requeue_execution(organization_id, execution_id, defer_until=None)
            return DiscoveryWorkSliceResult(
                outcome="continue_enqueued",
                organization_id=organization_id,
                execution_id=execution_id,
                counts=frozen,
                continuation_enqueued=True,
                error_code="pagination_incomplete",
            )

        if await self._is_provider_blocked(organization_id, execution_id):
            return DiscoveryWorkSliceResult(
                outcome="provider_blocked",
                organization_id=organization_id,
                execution_id=execution_id,
                counts=frozen,
                lifecycle_reason="paused",
            )

        # No pending/running/delayed; every succeeded job pagination_completed.
        await self._complete_web_discovery(
            organization_id,
            execution_id,
            frozen,
        )
        return DiscoveryWorkSliceResult(
            outcome="completed",
            organization_id=organization_id,
            execution_id=execution_id,
            counts=frozen,
        )

    async def _count_incomplete_pagination(
        self, organization_id: str, execution_id: str
    ) -> int:
        """Succeeded jobs missing pagination_completed must not complete discovery."""
        async with self._session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(ScrapingSourceDiscoveryQuery)
                .where(
                    ScrapingSourceDiscoveryQuery.organization_id == organization_id,
                    ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                    ScrapingSourceDiscoveryQuery.status
                    == SourceDiscoveryQueryStatus.SUCCEEDED,
                    ScrapingSourceDiscoveryQuery.pagination_completed.is_(False),
                )
            )
            return int(count or 0)

    async def _is_provider_blocked(
        self, organization_id: str, execution_id: str
    ) -> bool:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ScrapingExecution).where(
                    ScrapingExecution.id == execution_id,
                    ScrapingExecution.organization_id == organization_id,
                )
            )
            execution = result.scalar_one_or_none()
            if execution is None:
                return False
            profile = execution.country_profile_json or {}
            return bool(isinstance(profile, dict) and profile.get(PROVIDER_BLOCK_PROFILE_KEY))

    async def _complete_web_discovery(
        self,
        organization_id: str,
        execution_id: str,
        counts: DiscoveryWorkSliceCounts,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(ScrapingExecution)
                    .where(
                        ScrapingExecution.id == execution_id,
                        ScrapingExecution.organization_id == organization_id,
                    )
                    .with_for_update()
                )
                execution = result.scalar_one_or_none()
                if execution is None:
                    return
                if execution.status in {
                    ScrapingExecutionStatus.CANCELLED,
                    ScrapingExecutionStatus.CANCEL_REQUESTED,
                    ScrapingExecutionStatus.COMPLETED,
                    ScrapingExecutionStatus.FAILED,
                }:
                    return
                if execution.status in {
                    ScrapingExecutionStatus.PAUSED,
                    ScrapingExecutionStatus.PAUSE_REQUESTED,
                }:
                    return
                now = self._now_factory()
                execution.status = ScrapingExecutionStatus.COMPLETED
                execution.completed_at = now
                execution.heartbeat_at = now
                execution.current_stage = "web_discovery"
                execution.current_stage_label = "Web discovery"
                execution.current_provider = self._provider_name
                execution.progress_percent = 100
                execution.latest_message = (
                    "Phase 4 web discovery completed. Later scraper phases are not "
                    "implemented on this path yet."
                )
                # Temporary lifecycle limitation: no non-terminal awaiting-later-phase
                # status exists, so the execution becomes completed after web discovery
                # only. Stage metadata must not claim extraction/publication.
                profile = dict(execution.country_profile_json or {})
                profile.update(
                    {
                        "phase": "web_discovery",
                        "phase4_complete": True,
                        "later_phases_executed": False,
                        "facility_generation": False,
                        "external_calls": True,
                        "provider": self._provider_name,
                    }
                )
                execution.country_profile_json = profile

        await self._emit(
            execution_id,
            "web_discovery_completed",
            "Phase 4 web discovery completed.",
            metadata={
                "provider": self._provider_name,
                "query_succeeded_total": counts.query_succeeded_total,
                "query_failed_total": counts.query_failed_total,
                "candidate_total": counts.candidate_total,
                "crawl_node_total": counts.crawl_node_total,
                "later_phases_executed": False,
            },
        )
        # Truthful campaign completion for schema-v2: web discovery only.
        await self._emit(
            execution_id,
            "mission_campaign_completed",
            "Mission campaign completed after Phase 4 web discovery.",
            metadata={
                "mode": "live_discovery",
                "phase": "web_discovery",
                "later_phases_executed": False,
                "facility_generation": False,
                "external_calls": True,
            },
        )

    async def _requeue_execution(
        self,
        organization_id: str,
        execution_id: str,
        *,
        defer_until: datetime | None,
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(ScrapingExecution)
                    .where(
                        ScrapingExecution.id == execution_id,
                        ScrapingExecution.organization_id == organization_id,
                    )
                    .with_for_update()
                )
                execution = result.scalar_one_or_none()
                if execution is None:
                    return
                if execution.status not in {
                    ScrapingExecutionStatus.RUNNING,
                    ScrapingExecutionStatus.QUEUED,
                }:
                    return
                execution.status = ScrapingExecutionStatus.QUEUED
                execution.heartbeat_at = self._now_factory()
                execution.completed_at = None
                execution.current_stage = "web_discovery"
                execution.current_stage_label = "Web discovery"
                execution.latest_message = "Web discovery continuation queued."

        await self._queue_continuation(
            execution_id,
            defer_until=defer_until,
            job_id=f"{DISCOVERY_CONTINUATION_JOB_ID_PREFIX}{execution_id}",
        )

    async def _finish_with_inspection(
        self,
        organization_id: str,
        execution_id: str,
        *,
        outcome: DiscoverySliceOutcome,
        counts: _MutableSliceCounts,
        lifecycle_reason: str | None = None,
        error_code: str | None = None,
    ) -> DiscoveryWorkSliceResult:
        inspection = await self._claim_service.inspect_remaining_work(
            organization_id=organization_id,
            execution_id=execution_id,
            now=self._now_factory(),
        )
        candidate_total, crawl_node_total = await self._count_artifacts(
            organization_id, execution_id
        )
        return DiscoveryWorkSliceResult(
            outcome=outcome,
            organization_id=organization_id,
            execution_id=execution_id,
            counts=counts.freeze(
                inspection=inspection if inspection.outcome != "not_found" else None,
                candidate_total=candidate_total,
                crawl_node_total=crawl_node_total,
            ),
            lifecycle_reason=lifecycle_reason,
            error_code=error_code,
        )

    async def _validate_active_v2_campaign(
        self, organization_id: str, execution_id: str
    ) -> DiscoveryWorkSliceResult | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ScrapingExecution).where(
                    ScrapingExecution.id == execution_id,
                    ScrapingExecution.organization_id == organization_id,
                )
            )
            execution = result.scalar_one_or_none()
        if execution is None:
            return DiscoveryWorkSliceResult(
                outcome="not_found",
                organization_id=organization_id,
                execution_id=execution_id,
                error_code="execution_not_found",
            )
        if execution.execution_type != "mission_campaign":
            return DiscoveryWorkSliceResult(
                outcome="not_eligible",
                organization_id=organization_id,
                execution_id=execution_id,
                lifecycle_reason="not_eligible",
                error_code="not_mission_campaign",
            )
        if not supports_deterministic_query_generation(
            execution.execution_plan_schema_version
        ):
            return DiscoveryWorkSliceResult(
                outcome="not_eligible",
                organization_id=organization_id,
                execution_id=execution_id,
                lifecycle_reason="not_eligible",
                error_code="schema_not_v2",
            )
        if not execution.execution_plan_hash:
            return DiscoveryWorkSliceResult(
                outcome="not_eligible",
                organization_id=organization_id,
                execution_id=execution_id,
                lifecycle_reason="not_eligible",
                error_code="missing_plan_hash",
            )
        return None

    async def _load_execution_lifecycle(
        self, organization_id: str, execution_id: str
    ) -> ScrapingExecution | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(ScrapingExecution).where(
                    ScrapingExecution.id == execution_id,
                    ScrapingExecution.organization_id == organization_id,
                )
            )
            return result.scalar_one_or_none()

    def _lifecycle_block_reason(self, execution: ScrapingExecution) -> str | None:
        status = execution.status
        if status == ScrapingExecutionStatus.COMPLETED:
            return "completed"
        if status == ScrapingExecutionStatus.FAILED:
            return "failed"
        if status in {
            ScrapingExecutionStatus.CANCELLED,
            ScrapingExecutionStatus.CANCEL_REQUESTED,
        }:
            return "cancelled"
        if status in {
            ScrapingExecutionStatus.PAUSED,
            ScrapingExecutionStatus.PAUSE_REQUESTED,
        }:
            return "cancelled" if cancel_supersedes_pause(execution) else "paused"
        if status == ScrapingExecutionStatus.QUEUED:
            # Continuation / resume requeues the same execution; promote below.
            return None
        if status != ScrapingExecutionStatus.RUNNING:
            return "not_eligible"
        return None

    def _is_cancelled(self, execution: ScrapingExecution) -> bool:
        if execution.status in {
            ScrapingExecutionStatus.CANCELLED,
            ScrapingExecutionStatus.CANCEL_REQUESTED,
        }:
            return True
        if execution.status in {
            ScrapingExecutionStatus.PAUSED,
            ScrapingExecutionStatus.PAUSE_REQUESTED,
        }:
            return cancel_supersedes_pause(execution)
        return False

    def _is_pause_requested(self, execution: ScrapingExecution) -> bool:
        if self._is_cancelled(execution):
            return False
        return execution.status in {
            ScrapingExecutionStatus.PAUSED,
            ScrapingExecutionStatus.PAUSE_REQUESTED,
        }

    async def _promote_queued_to_running(
        self, organization_id: str, execution_id: str
    ) -> None:
        """Continuation slices re-enter as QUEUED; claim service requires RUNNING."""
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(ScrapingExecution)
                    .where(
                        ScrapingExecution.id == execution_id,
                        ScrapingExecution.organization_id == organization_id,
                        ScrapingExecution.status == ScrapingExecutionStatus.QUEUED,
                    )
                    .with_for_update()
                )
                execution = result.scalar_one_or_none()
                if execution is None:
                    return
                execution.status = ScrapingExecutionStatus.RUNNING
                execution.started_at = execution.started_at or self._now_factory()
                execution.heartbeat_at = self._now_factory()
                execution.completed_at = None

    async def _ensure_discovery_stage(
        self, organization_id: str, execution_id: str
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(ScrapingExecution)
                    .where(
                        ScrapingExecution.id == execution_id,
                        ScrapingExecution.organization_id == organization_id,
                    )
                    .with_for_update()
                )
                execution = result.scalar_one_or_none()
                if execution is None:
                    return
                execution.current_stage = "web_discovery"
                execution.current_stage_label = "Web discovery"
                execution.current_provider = self._provider_name
                execution.heartbeat_at = self._now_factory()
                execution.latest_message = "Phase 4 web discovery in progress."

    async def _acknowledge_paused(
        self, organization_id: str, execution_id: str
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(ScrapingExecution)
                    .where(
                        ScrapingExecution.id == execution_id,
                        ScrapingExecution.organization_id == organization_id,
                    )
                    .with_for_update()
                )
                execution = result.scalar_one_or_none()
                if execution is None:
                    return
                if self._is_cancelled(execution):
                    return
                if execution.status in {
                    ScrapingExecutionStatus.COMPLETED,
                    ScrapingExecutionStatus.FAILED,
                    ScrapingExecutionStatus.CANCELLED,
                }:
                    return
                execution.status = ScrapingExecutionStatus.PAUSED
                execution.paused_at = execution.paused_at or self._now_factory()
                execution.completed_at = None
                execution.current_stage = "web_discovery"
                execution.current_stage_label = "Web discovery"
        await self._emit(
            execution_id,
            "execution_paused",
            "Mission campaign paused at a safe checkpoint.",
        )

    async def _acknowledge_provider_blocked(
        self,
        organization_id: str,
        execution_id: str,
        *,
        blocker_code: str,
        released_count: int,
    ) -> None:
        """Pause execution for recoverable provider-wide blocker; storm-safe event."""
        safe_code = normalize_lifecycle_error_code(blocker_code)
        already_blocked = False
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(ScrapingExecution)
                    .where(
                        ScrapingExecution.id == execution_id,
                        ScrapingExecution.organization_id == organization_id,
                    )
                    .with_for_update()
                )
                execution = result.scalar_one_or_none()
                if execution is None:
                    return
                if self._is_cancelled(execution):
                    return
                if execution.status in {
                    ScrapingExecutionStatus.COMPLETED,
                    ScrapingExecutionStatus.FAILED,
                    ScrapingExecutionStatus.CANCELLED,
                }:
                    return
                profile = dict(execution.country_profile_json or {})
                already_blocked = bool(
                    profile.get(PROVIDER_BLOCK_PROFILE_KEY)
                    and profile.get(PROVIDER_BLOCK_CODE_KEY) == safe_code
                    and profile.get(PROVIDER_BLOCK_PROVIDER_KEY) == self._provider_name
                )
                profile[PROVIDER_BLOCK_PROFILE_KEY] = True
                profile[PROVIDER_BLOCK_CODE_KEY] = safe_code
                profile[PROVIDER_BLOCK_PROVIDER_KEY] = self._provider_name
                profile[PROVIDER_BLOCK_STAGE_KEY] = "web_discovery"
                execution.country_profile_json = profile
                execution.status = ScrapingExecutionStatus.PAUSED
                execution.paused_at = execution.paused_at or self._now_factory()
                execution.completed_at = None
                execution.current_stage = "web_discovery"
                execution.current_stage_label = "Web discovery"
                execution.current_provider = self._provider_name
                execution.latest_message = (
                    "Web discovery paused: provider configuration or authentication "
                    "must be fixed before resume."
                )

        if not already_blocked:
            await self._emit(
                execution_id,
                "web_discovery_blocked",
                "Web discovery blocked by provider configuration or authentication.",
                metadata={
                    "execution_id": execution_id,
                    "provider": self._provider_name,
                    "blocker_code": safe_code,
                    "stage": "web_discovery",
                    "released_count": int(released_count),
                },
            )
        await self._emit(
            execution_id,
            "execution_paused",
            "Mission campaign paused for provider configuration recovery.",
        )

    async def _clear_provider_blocked_state(
        self, organization_id: str, execution_id: str
    ) -> None:
        """Clear/supersede blocked markers when a slice starts after resume."""
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(ScrapingExecution)
                    .where(
                        ScrapingExecution.id == execution_id,
                        ScrapingExecution.organization_id == organization_id,
                    )
                    .with_for_update()
                )
                execution = result.scalar_one_or_none()
                if execution is None:
                    return
                profile = dict(execution.country_profile_json or {})
                if not profile.get(PROVIDER_BLOCK_PROFILE_KEY):
                    return
                profile.pop(PROVIDER_BLOCK_PROFILE_KEY, None)
                profile.pop(PROVIDER_BLOCK_CODE_KEY, None)
                profile.pop(PROVIDER_BLOCK_PROVIDER_KEY, None)
                profile.pop(PROVIDER_BLOCK_STAGE_KEY, None)
                execution.country_profile_json = profile

    async def _acknowledge_cancelled(
        self, organization_id: str, execution_id: str
    ) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(ScrapingExecution)
                    .where(
                        ScrapingExecution.id == execution_id,
                        ScrapingExecution.organization_id == organization_id,
                    )
                    .with_for_update()
                )
                execution = result.scalar_one_or_none()
                if execution is None:
                    return
                if execution.status == ScrapingExecutionStatus.CANCELLED:
                    return
                if execution.status in {
                    ScrapingExecutionStatus.COMPLETED,
                    ScrapingExecutionStatus.FAILED,
                }:
                    return
                await execution_service._cancel_pending_children(session, execution.id)
                execution.status = ScrapingExecutionStatus.CANCELLED
                execution.completed_at = self._now_factory()
                execution.current_stage = "web_discovery"
                execution.current_stage_label = "Web discovery"
        await self._emit(
            execution_id,
            "execution_cancelled",
            "Mission campaign cancelled.",
        )

    async def _count_artifacts(
        self, organization_id: str, execution_id: str
    ) -> tuple[int, int]:
        async with self._session_factory() as session:
            candidates = await session.scalar(
                select(func.count())
                .select_from(ScrapingSourceCandidate)
                .where(
                    ScrapingSourceCandidate.organization_id == organization_id,
                    ScrapingSourceCandidate.execution_id == execution_id,
                )
            )
            nodes = await session.scalar(
                select(func.count())
                .select_from(ScrapingCrawlNode)
                .where(
                    ScrapingCrawlNode.organization_id == organization_id,
                    ScrapingCrawlNode.execution_id == execution_id,
                )
            )
            return int(candidates or 0), int(nodes or 0)

    async def _emit(
        self,
        execution_id: str,
        event_type: str,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        safe_meta = _sanitize_event_metadata(metadata or {})
        try:
            await self._event_emitter(
                execution_id, event_type, message, metadata=safe_meta
            )
        except Exception:
            logger.exception(
                "discovery_event_emit_failed execution_id=%s event_type=%s",
                execution_id,
                event_type,
            )

    async def _default_emit_event(
        self,
        execution_id: str,
        event_type: str,
        message: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        async with self._session_factory() as session:
            event = await execution_service.emit_event(
                session,
                execution_id,
                event_type,
                message,
                metadata=metadata,
            )
            await session.commit()
            return event

    async def _default_queue_continuation(
        self,
        execution_id: str,
        *,
        defer_until: datetime | None = None,
        job_id: str | None = None,
    ) -> None:
        defer_by = None
        if defer_until is not None:
            # Prefer absolute defer_until for delayed retries.
            await execution_service.enqueue_execution(
                execution_id,
                job_name="run_mission_campaign_mock",
                defer_until=defer_until,
                job_id=job_id,
            )
            return
        # Immediate continuation: small defer avoids same-job-id collision with the
        # still-finishing ARQ worker invocation; dedicated discovery job id also helps.
        await execution_service.enqueue_execution(
            execution_id,
            job_name="run_mission_campaign_mock",
            defer_by=1,
            job_id=job_id or f"{DISCOVERY_CONTINUATION_JOB_ID_PREFIX}{execution_id}",
        )
        del defer_by


def _sanitize_event_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Drop unsafe keys; keep only aggregate/safe progress fields."""
    forbidden_substrings = (
        "query_text",
        "prompt",
        "api_key",
        "secret",
        "password",
        "authorization",
        "fingerprint",
        "plan_hash",
        "canonical_url_hash",
        "sql",
        "exception",
        "stack",
        "body",
        "payload",
        "dns",
        "frozen_execution_plan",
        "resolved_execution_plan",
    )
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        lowered = str(key).lower()
        if any(part in lowered for part in forbidden_substrings):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            if isinstance(value, str) and len(value) > 200:
                continue
            safe[key] = value
        elif isinstance(value, datetime):
            safe[key] = value.isoformat()
    return safe


source_discovery_execution_service = SourceDiscoveryExecutionService()

__all__ = [
    "DISCOVERY_CONTINUATION_JOB_ID_PREFIX",
    "DiscoveryWorkSliceCounts",
    "DiscoveryWorkSliceResult",
    "SourceDiscoveryExecutionService",
    "V2_DISCOVERY_PROVIDER",
    "source_discovery_execution_service",
]
