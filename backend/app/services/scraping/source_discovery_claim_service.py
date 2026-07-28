"""Phase 4 Slice 3: atomic source-discovery query-job claims, leases, and retries.

Service-owned short transactions return immutable DTOs so future provider HTTP
never holds row locks or open ORM sessions. PostgreSQL ``FOR UPDATE SKIP LOCKED``
is required for claim/recovery concurrency; SQLite is not a supported claim backend.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingSourceDiscoveryQuery,
    SourceDiscoveryQueryStatus,
)
from app.db.session import AsyncSessionLocal
from app.schemas.scraping_execution_plan import supports_deterministic_query_generation

LAST_ERROR_CODE_MAX_LENGTH = 80
PROVIDER_ID_MAX_LENGTH = 64
DEFAULT_MAX_CLAIM_BATCH_SIZE = 50
DEFAULT_MAX_RECOVERY_BATCH_SIZE = 50
DEFAULT_LEASE_DURATION = timedelta(seconds=60)
DEFAULT_RETRY_DELAY = timedelta(seconds=30)
LEASE_EXPIRED_ERROR_CODE = "lease_expired"
FALLBACK_ERROR_CODE = "unspecified_error"

_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_PROVIDER_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

ClaimBatchOutcome = Literal["claimed", "no_work", "lifecycle_blocked"]
LifecycleBlockReason = Literal[
    "paused",
    "cancelled",
    "completed",
    "failed",
    "not_eligible",
    "not_found",
]
ClaimMutationOutcome = Literal["applied", "stale_claim", "not_found", "invalid_state"]
WorkInspectionOutcome = Literal["ok", "lifecycle_blocked", "not_found"]
ClaimPreflightOutcome = Literal[
    "ok",
    "paused",
    "cancelled",
    "completed",
    "failed",
    "not_eligible",
    "not_found",
    "stale_claim",
    "invalid_state",
]

NowFactory = Callable[[], datetime]
BackoffPolicy = Callable[[datetime, int], datetime]


@dataclass(frozen=True)
class ClaimedQueryJob:
    """Detached claim snapshot safe to hold across future provider HTTP."""

    id: str
    organization_id: str
    execution_id: str
    query_text: str
    provider: str
    claim_token: str
    claimed_at: datetime
    lease_expires_at: datetime
    attempt_count: int
    last_attempt_at: datetime
    priority: int
    generation_ordinal: int
    discovery_round: int
    purpose: str
    country_code: str
    country_name: str
    region_code: str | None
    region_name: str | None
    language_code: str
    language_name: str
    source_category: str
    scope_level: str
    important_city: str | None
    query_job_fingerprint: str | None
    plan_hash_snapshot: str | None
    requested_at: datetime
    # Restart-safe Serper page cursor (1-indexed). Operational only — not a campaign cap.
    next_page_number: int = 1
    pages_completed: int = 0
    pagination_completed: bool = False
    last_page_result_count: int | None = None
    last_page_fingerprint: str | None = None


@dataclass(frozen=True)
class ClaimBatchResult:
    outcome: ClaimBatchOutcome
    jobs: tuple[ClaimedQueryJob, ...] = ()
    lifecycle_reason: LifecycleBlockReason | None = None
    claimed_count: int = 0


@dataclass(frozen=True)
class ClaimMutationResult:
    outcome: ClaimMutationOutcome
    query_job_id: str
    status: str | None = None
    lease_expires_at: datetime | None = None
    next_attempt_at: datetime | None = None
    last_error_code: str | None = None
    attempt_count: int | None = None


@dataclass(frozen=True)
class RecoverExpiredClaimsResult:
    recovered_count: int
    recovered_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkInspectionResult:
    outcome: WorkInspectionOutcome
    lifecycle_reason: LifecycleBlockReason | None = None
    pending_eligible_count: int = 0
    pending_delayed_count: int = 0
    running_count: int = 0
    expired_lease_count: int = 0
    succeeded_count: int = 0
    failed_count: int = 0
    earliest_next_attempt_at: datetime | None = None


@dataclass(frozen=True)
class ClaimPreflightResult:
    """Short-TX readiness check immediately before provider HTTP."""

    outcome: ClaimPreflightOutcome
    query_job_id: str
    status: str | None = None
    attempt_count: int | None = None


def generate_claim_token() -> str:
    """Fresh String(36) UUID token matching UUIDPrimaryKeyMixin convention."""
    return str(uuid.uuid4())


def validate_positive_batch_size(
    batch_size: int,
    *,
    max_batch_size: int = DEFAULT_MAX_CLAIM_BATCH_SIZE,
    field_name: str = "batch_size",
) -> int:
    if not isinstance(batch_size, int) or isinstance(batch_size, bool):
        raise ValueError(f"{field_name} must be a positive integer")
    if batch_size < 1:
        raise ValueError(f"{field_name} must be >= 1")
    if batch_size > max_batch_size:
        raise ValueError(f"{field_name} must be <= {max_batch_size}")
    return batch_size


def validate_lease_duration(lease_duration: timedelta) -> timedelta:
    if not isinstance(lease_duration, timedelta):
        raise ValueError("lease_duration must be a timedelta")
    if lease_duration <= timedelta(0):
        raise ValueError("lease_duration must be positive")
    return lease_duration


def validate_retry_delay(delay: timedelta) -> timedelta:
    if not isinstance(delay, timedelta):
        raise ValueError("retry delay must be a timedelta")
    if delay < timedelta(0):
        raise ValueError("retry delay must be non-negative")
    return delay


def validate_provider_id(provider: str) -> str:
    if not isinstance(provider, str):
        raise ValueError("provider must be a string")
    normalized = provider.strip().lower()
    if len(normalized) > PROVIDER_ID_MAX_LENGTH or not _PROVIDER_ID_RE.fullmatch(normalized):
        raise ValueError("provider must be a lowercase identifier (letters, digits, underscore)")
    return normalized


def normalize_lifecycle_error_code(
    value: str | None,
    *,
    fallback: str = FALLBACK_ERROR_CODE,
) -> str:
    """Sanitize to a bounded lowercase code; never store raw messages/secrets."""
    if not isinstance(value, str) or not value.strip():
        return fallback[:LAST_ERROR_CODE_MAX_LENGTH]
    cleaned = value.strip().lower()
    cleaned = re.sub(r"[^a-z0-9_]+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        return fallback[:LAST_ERROR_CODE_MAX_LENGTH]
    return cleaned[:LAST_ERROR_CODE_MAX_LENGTH]


def require_lifecycle_error_code(value: str) -> str:
    """Accept only already-safe codes; reject exception-like / secret-like strings."""
    if not isinstance(value, str):
        raise ValueError("error code must be a string")
    candidate = value.strip()
    if candidate != value:
        raise ValueError("error code must not have leading/trailing whitespace")
    if candidate != candidate.lower():
        raise ValueError("error code must be lowercase")
    if len(candidate) > LAST_ERROR_CODE_MAX_LENGTH:
        raise ValueError(f"error code must be <= {LAST_ERROR_CODE_MAX_LENGTH} characters")
    if not _ERROR_CODE_RE.fullmatch(candidate):
        raise ValueError("error code must match ^[a-z][a-z0-9_]*$")
    return candidate


def fixed_backoff_policy(delay: timedelta) -> BackoffPolicy:
    validate_retry_delay(delay)

    def _policy(now: datetime, attempt_count: int) -> datetime:
        del attempt_count
        return now + delay

    return _policy


def exponential_backoff_policy(
    *,
    base: timedelta = DEFAULT_RETRY_DELAY,
    factor: float = 2.0,
    max_delay: timedelta | None = None,
) -> BackoffPolicy:
    """Deterministic exponential backoff. No random jitter."""
    validate_retry_delay(base)
    if factor < 1.0:
        raise ValueError("backoff factor must be >= 1")
    if max_delay is not None:
        validate_retry_delay(max_delay)

    def _policy(now: datetime, attempt_count: int) -> datetime:
        exponent = max(attempt_count - 1, 0)
        seconds = base.total_seconds() * (factor**exponent)
        delay = timedelta(seconds=seconds)
        if max_delay is not None and delay > max_delay:
            delay = max_delay
        return now + delay

    return _policy


def immediate_retry_policy() -> BackoffPolicy:
    return fixed_backoff_policy(timedelta(0))


def cancel_supersedes_pause(execution: ScrapingExecution) -> bool:
    """Cancel wins when both request timestamps exist (or status is already cancel_requested).

    Semantics match the mission-campaign mock worker helper of the same purpose.
    Duplicated here intentionally so the claim service does not import that worker module.
    """
    if execution.status == ScrapingExecutionStatus.CANCEL_REQUESTED:
        return True
    if execution.cancel_requested_at is None:
        return False
    if execution.pause_requested_at is None:
        return True
    return execution.cancel_requested_at >= execution.pause_requested_at


def evaluate_claim_lifecycle(execution: ScrapingExecution | None) -> LifecycleBlockReason | None:
    """Return a block reason, or None when claiming may proceed.

    Status is authoritative. Historical pause_requested_at / paused_at audit
    timestamps must not block a resumed RUNNING campaign.
    """
    if execution is None:
        return "not_found"
    if execution.execution_type != "mission_campaign":
        return "not_eligible"
    if not supports_deterministic_query_generation(execution.execution_plan_schema_version):
        return "not_eligible"

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
    if status != ScrapingExecutionStatus.RUNNING:
        return "not_eligible"
    return None


def _dto_from_row(row: ScrapingSourceDiscoveryQuery) -> ClaimedQueryJob:
    assert row.execution_id is not None
    assert row.provider is not None
    assert row.claim_token is not None
    assert row.claimed_at is not None
    assert row.lease_expires_at is not None
    assert row.last_attempt_at is not None
    assert row.requested_at is not None
    return ClaimedQueryJob(
        id=row.id,
        organization_id=row.organization_id,
        execution_id=row.execution_id,
        query_text=row.query_text,
        provider=row.provider,
        claim_token=row.claim_token,
        claimed_at=row.claimed_at,
        lease_expires_at=row.lease_expires_at,
        attempt_count=row.attempt_count,
        last_attempt_at=row.last_attempt_at,
        priority=row.priority,
        generation_ordinal=row.generation_ordinal,
        discovery_round=row.discovery_round,
        purpose=row.purpose,
        country_code=row.country_code,
        country_name=row.country_name,
        region_code=row.region_code,
        region_name=row.region_name,
        language_code=row.language_code,
        language_name=row.language_name,
        source_category=row.source_category,
        scope_level=row.scope_level,
        important_city=row.important_city,
        query_job_fingerprint=row.query_job_fingerprint,
        plan_hash_snapshot=row.plan_hash_snapshot,
        requested_at=row.requested_at,
        next_page_number=int(getattr(row, "next_page_number", None) or 1),
        pages_completed=int(getattr(row, "pages_completed", None) or 0),
        pagination_completed=bool(getattr(row, "pagination_completed", False)),
        last_page_result_count=getattr(row, "last_page_result_count", None),
        last_page_fingerprint=getattr(row, "last_page_fingerprint", None),
    )


def _require_postgresql(session: AsyncSession) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError(
            "source_discovery_claim_service requires PostgreSQL "
            "(FOR UPDATE SKIP LOCKED). SQLite is not supported for claim ops."
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SourceDiscoveryClaimService:
    """Atomic claim / lease / retry lifecycle for plan-backed discovery query jobs.

    Contract: every public mutating method opens a short transaction, commits, and
    returns only immutable DTOs. Callers must not perform HTTP inside that
    transaction — there is no hook for it.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        now_factory: NowFactory | None = None,
        max_claim_batch_size: int = DEFAULT_MAX_CLAIM_BATCH_SIZE,
        max_recovery_batch_size: int = DEFAULT_MAX_RECOVERY_BATCH_SIZE,
        default_lease_duration: timedelta = DEFAULT_LEASE_DURATION,
        default_retry_policy: BackoffPolicy | None = None,
        default_recovery_policy: BackoffPolicy | None = None,
    ) -> None:
        self._session_factory = session_factory or AsyncSessionLocal
        self._now_factory = now_factory or _utc_now
        self._max_claim_batch_size = max_claim_batch_size
        self._max_recovery_batch_size = max_recovery_batch_size
        self._default_lease_duration = validate_lease_duration(default_lease_duration)
        self._default_retry_policy = default_retry_policy or fixed_backoff_policy(
            DEFAULT_RETRY_DELAY
        )
        self._default_recovery_policy = default_recovery_policy or immediate_retry_policy()

    async def claim_eligible_jobs(
        self,
        *,
        organization_id: str,
        execution_id: str,
        provider: str,
        batch_size: int,
        lease_duration: timedelta | None = None,
        now: datetime | None = None,
    ) -> ClaimBatchResult:
        """Claim a bounded pending batch. TX commits before return (before any HTTP)."""
        provider_id = validate_provider_id(provider)
        size = validate_positive_batch_size(
            batch_size, max_batch_size=self._max_claim_batch_size
        )
        lease = validate_lease_duration(lease_duration or self._default_lease_duration)
        clock = now or self._now_factory()

        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                execution = await self._load_execution(
                    session, organization_id=organization_id, execution_id=execution_id
                )
                blocked = evaluate_claim_lifecycle(execution)
                if blocked is not None:
                    return ClaimBatchResult(
                        outcome="lifecycle_blocked",
                        lifecycle_reason=blocked,
                    )

                stmt = (
                    select(ScrapingSourceDiscoveryQuery)
                    .where(
                        ScrapingSourceDiscoveryQuery.organization_id == organization_id,
                        ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                        ScrapingSourceDiscoveryQuery.status
                        == SourceDiscoveryQueryStatus.PENDING,
                        or_(
                            ScrapingSourceDiscoveryQuery.next_attempt_at.is_(None),
                            ScrapingSourceDiscoveryQuery.next_attempt_at <= clock,
                        ),
                    )
                    .order_by(
                        ScrapingSourceDiscoveryQuery.priority.asc(),
                        ScrapingSourceDiscoveryQuery.generation_ordinal.asc(),
                        ScrapingSourceDiscoveryQuery.id.asc(),
                    )
                    .limit(size)
                    .with_for_update(skip_locked=True)
                )
                rows = list((await session.execute(stmt)).scalars().all())
                if not rows:
                    return ClaimBatchResult(outcome="no_work")

                lease_expires = clock + lease
                jobs: list[ClaimedQueryJob] = []
                for row in rows:
                    row.status = SourceDiscoveryQueryStatus.RUNNING
                    row.claim_token = generate_claim_token()
                    row.claimed_at = clock
                    row.lease_expires_at = lease_expires
                    row.attempt_count = int(row.attempt_count or 0) + 1
                    row.last_attempt_at = clock
                    row.next_attempt_at = None
                    row.provider = provider_id
                    row.requested_at = clock
                    # New attempt clears prior lifecycle error; retain provenance.
                    row.last_error_code = None
                    row.last_error_at = None
                    row.error_code = None
                    row.error_message = None
                    row.completed_at = None
                    jobs.append(_dto_from_row(row))

                return ClaimBatchResult(
                    outcome="claimed",
                    jobs=tuple(jobs),
                    claimed_count=len(jobs),
                )

    async def renew_claim(
        self,
        *,
        organization_id: str,
        execution_id: str,
        query_job_id: str,
        claim_token: str,
        lease_duration: timedelta | None = None,
        now: datetime | None = None,
    ) -> ClaimMutationResult:
        lease = validate_lease_duration(lease_duration or self._default_lease_duration)
        clock = now or self._now_factory()
        new_expiry = clock + lease

        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                row = await self._load_running_claim(
                    session,
                    organization_id=organization_id,
                    execution_id=execution_id,
                    query_job_id=query_job_id,
                )
                if row is None:
                    return ClaimMutationResult(outcome="not_found", query_job_id=query_job_id)
                if row.status != SourceDiscoveryQueryStatus.RUNNING:
                    return ClaimMutationResult(
                        outcome="invalid_state",
                        query_job_id=query_job_id,
                        status=row.status.value,
                    )
                if row.claim_token != claim_token:
                    return ClaimMutationResult(outcome="stale_claim", query_job_id=query_job_id)

                # Never reduce a still-valid lease.
                current_expiry = row.lease_expires_at
                if current_expiry is not None and current_expiry > new_expiry:
                    return ClaimMutationResult(
                        outcome="applied",
                        query_job_id=query_job_id,
                        status=row.status.value,
                        lease_expires_at=current_expiry,
                        attempt_count=row.attempt_count,
                    )

                row.lease_expires_at = new_expiry
                return ClaimMutationResult(
                    outcome="applied",
                    query_job_id=query_job_id,
                    status=row.status.value,
                    lease_expires_at=new_expiry,
                    attempt_count=row.attempt_count,
                )

    async def mark_succeeded(
        self,
        *,
        organization_id: str,
        execution_id: str,
        query_job_id: str,
        claim_token: str,
        now: datetime | None = None,
    ) -> ClaimMutationResult:
        clock = now or self._now_factory()
        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                row = await self._load_scoped_job(
                    session,
                    organization_id=organization_id,
                    execution_id=execution_id,
                    query_job_id=query_job_id,
                )
                if row is None:
                    return ClaimMutationResult(outcome="not_found", query_job_id=query_job_id)
                if row.status != SourceDiscoveryQueryStatus.RUNNING:
                    return ClaimMutationResult(
                        outcome="invalid_state",
                        query_job_id=query_job_id,
                        status=row.status.value,
                        attempt_count=row.attempt_count,
                    )
                if row.claim_token != claim_token:
                    return ClaimMutationResult(
                        outcome="stale_claim",
                        query_job_id=query_job_id,
                        attempt_count=row.attempt_count,
                    )

                row.status = SourceDiscoveryQueryStatus.SUCCEEDED
                row.completed_at = clock
                row.claim_token = None
                row.claimed_at = None
                row.lease_expires_at = None
                row.next_attempt_at = None
                row.last_error_code = None
                row.last_error_at = None
                row.error_code = None
                row.error_message = None
                # attempt_count / last_attempt_at / provider / requested_at retained.
                return ClaimMutationResult(
                    outcome="applied",
                    query_job_id=query_job_id,
                    status=row.status.value,
                    attempt_count=row.attempt_count,
                )

    async def requeue_retryable_failure(
        self,
        *,
        organization_id: str,
        execution_id: str,
        query_job_id: str,
        claim_token: str,
        error_code: str,
        next_attempt_at: datetime | None = None,
        retry_policy: BackoffPolicy | None = None,
        now: datetime | None = None,
    ) -> ClaimMutationResult:
        """running → pending with scheduled retry. No attempt ceiling. No campaign stop.

        provider / requested_at remain as last-attempted provider semantics.
        """
        safe_code = require_lifecycle_error_code(error_code)
        clock = now or self._now_factory()

        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                row = await self._load_scoped_job(
                    session,
                    organization_id=organization_id,
                    execution_id=execution_id,
                    query_job_id=query_job_id,
                )
                if row is None:
                    return ClaimMutationResult(outcome="not_found", query_job_id=query_job_id)
                if row.status != SourceDiscoveryQueryStatus.RUNNING:
                    return ClaimMutationResult(
                        outcome="invalid_state",
                        query_job_id=query_job_id,
                        status=row.status.value,
                        attempt_count=row.attempt_count,
                    )
                if row.claim_token != claim_token:
                    return ClaimMutationResult(
                        outcome="stale_claim",
                        query_job_id=query_job_id,
                        attempt_count=row.attempt_count,
                    )

                policy = retry_policy or self._default_retry_policy
                scheduled = next_attempt_at if next_attempt_at is not None else policy(
                    clock, int(row.attempt_count or 0)
                )
                row.status = SourceDiscoveryQueryStatus.PENDING
                row.next_attempt_at = scheduled
                row.last_error_code = safe_code
                row.last_error_at = clock
                row.claim_token = None
                row.claimed_at = None
                row.lease_expires_at = None
                # Retain attempt_count, last_attempt_at, provider, requested_at.
                return ClaimMutationResult(
                    outcome="applied",
                    query_job_id=query_job_id,
                    status=row.status.value,
                    next_attempt_at=scheduled,
                    last_error_code=safe_code,
                    attempt_count=row.attempt_count,
                )

    async def mark_terminal_failure(
        self,
        *,
        organization_id: str,
        execution_id: str,
        query_job_id: str,
        claim_token: str,
        error_code: str,
        now: datetime | None = None,
    ) -> ClaimMutationResult:
        """running → failed for one job only. No campaign-wide failure."""
        safe_code = require_lifecycle_error_code(error_code)
        clock = now or self._now_factory()

        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                row = await self._load_scoped_job(
                    session,
                    organization_id=organization_id,
                    execution_id=execution_id,
                    query_job_id=query_job_id,
                )
                if row is None:
                    return ClaimMutationResult(outcome="not_found", query_job_id=query_job_id)
                if row.status != SourceDiscoveryQueryStatus.RUNNING:
                    return ClaimMutationResult(
                        outcome="invalid_state",
                        query_job_id=query_job_id,
                        status=row.status.value,
                        attempt_count=row.attempt_count,
                    )
                if row.claim_token != claim_token:
                    return ClaimMutationResult(
                        outcome="stale_claim",
                        query_job_id=query_job_id,
                        attempt_count=row.attempt_count,
                    )

                row.status = SourceDiscoveryQueryStatus.FAILED
                row.completed_at = clock
                row.last_error_code = safe_code
                row.last_error_at = clock
                row.error_code = safe_code
                row.claim_token = None
                row.claimed_at = None
                row.lease_expires_at = None
                row.next_attempt_at = None
                return ClaimMutationResult(
                    outcome="applied",
                    query_job_id=query_job_id,
                    status=row.status.value,
                    last_error_code=safe_code,
                    attempt_count=row.attempt_count,
                )

    async def recover_expired_claims(
        self,
        *,
        organization_id: str,
        execution_id: str,
        batch_size: int,
        recovery_policy: BackoffPolicy | None = None,
        now: datetime | None = None,
    ) -> RecoverExpiredClaimsResult:
        size = validate_positive_batch_size(
            batch_size,
            max_batch_size=self._max_recovery_batch_size,
            field_name="batch_size",
        )
        clock = now or self._now_factory()
        policy = recovery_policy or self._default_recovery_policy

        async with self._session_factory() as session:
            async with session.begin():
                _require_postgresql(session)
                # Org+exec must match; do not recover across tenants.
                stmt = (
                    select(ScrapingSourceDiscoveryQuery)
                    .where(
                        ScrapingSourceDiscoveryQuery.organization_id == organization_id,
                        ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                        ScrapingSourceDiscoveryQuery.status
                        == SourceDiscoveryQueryStatus.RUNNING,
                        ScrapingSourceDiscoveryQuery.lease_expires_at.is_not(None),
                        ScrapingSourceDiscoveryQuery.lease_expires_at <= clock,
                    )
                    .order_by(
                        ScrapingSourceDiscoveryQuery.lease_expires_at.asc(),
                        ScrapingSourceDiscoveryQuery.id.asc(),
                    )
                    .limit(size)
                    .with_for_update(skip_locked=True)
                )
                rows = list((await session.execute(stmt)).scalars().all())
                recovered: list[str] = []
                for row in rows:
                    row.status = SourceDiscoveryQueryStatus.PENDING
                    row.claim_token = None
                    row.claimed_at = None
                    row.lease_expires_at = None
                    row.last_error_code = LEASE_EXPIRED_ERROR_CODE
                    row.last_error_at = clock
                    row.next_attempt_at = policy(clock, int(row.attempt_count or 0))
                    # Retain attempt_count, fingerprint, provenance, provider history.
                    recovered.append(row.id)
                return RecoverExpiredClaimsResult(
                    recovered_count=len(recovered),
                    recovered_ids=tuple(recovered),
                )

    async def preflight_claimed_job(
        self,
        *,
        organization_id: str,
        execution_id: str,
        query_job_id: str,
        claim_token: str,
        now: datetime | None = None,
    ) -> ClaimPreflightResult:
        """Verify claim + execution readiness before provider HTTP (short TX)."""
        del now  # clock not required; status/token are authoritative
        async with self._session_factory() as session:
            async with session.begin():
                execution = await self._load_execution(
                    session, organization_id=organization_id, execution_id=execution_id
                )
                blocked = evaluate_claim_lifecycle(execution)
                if blocked is not None:
                    mapped: ClaimPreflightOutcome
                    if blocked == "paused":
                        mapped = "paused"
                    elif blocked == "cancelled":
                        mapped = "cancelled"
                    elif blocked == "completed":
                        mapped = "completed"
                    elif blocked == "failed":
                        mapped = "failed"
                    elif blocked == "not_found":
                        mapped = "not_found"
                    else:
                        mapped = "not_eligible"
                    return ClaimPreflightResult(
                        outcome=mapped,
                        query_job_id=query_job_id,
                        status=execution.status.value if execution is not None else None,
                    )

                row = await self._load_scoped_job(
                    session,
                    organization_id=organization_id,
                    execution_id=execution_id,
                    query_job_id=query_job_id,
                )
                if row is None:
                    return ClaimPreflightResult(outcome="not_found", query_job_id=query_job_id)
                if row.status != SourceDiscoveryQueryStatus.RUNNING:
                    return ClaimPreflightResult(
                        outcome="invalid_state",
                        query_job_id=query_job_id,
                        status=row.status.value,
                        attempt_count=row.attempt_count,
                    )
                if row.claim_token != claim_token:
                    return ClaimPreflightResult(
                        outcome="stale_claim",
                        query_job_id=query_job_id,
                        status=row.status.value,
                        attempt_count=row.attempt_count,
                    )
                return ClaimPreflightResult(
                    outcome="ok",
                    query_job_id=query_job_id,
                    status=row.status.value,
                    attempt_count=row.attempt_count,
                )

    async def inspect_remaining_work(
        self,
        *,
        organization_id: str,
        execution_id: str,
        now: datetime | None = None,
    ) -> WorkInspectionResult:
        clock = now or self._now_factory()
        async with self._session_factory() as session:
            async with session.begin():
                execution = await self._load_execution(
                    session, organization_id=organization_id, execution_id=execution_id
                )
                blocked = evaluate_claim_lifecycle(execution)
                if blocked == "not_found":
                    return WorkInspectionResult(
                        outcome="not_found", lifecycle_reason="not_found"
                    )
                # Counts remain useful even when lifecycle is blocked.
                pending_eligible = await session.scalar(
                    select(func.count())
                    .select_from(ScrapingSourceDiscoveryQuery)
                    .where(
                        ScrapingSourceDiscoveryQuery.organization_id == organization_id,
                        ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                        ScrapingSourceDiscoveryQuery.status
                        == SourceDiscoveryQueryStatus.PENDING,
                        or_(
                            ScrapingSourceDiscoveryQuery.next_attempt_at.is_(None),
                            ScrapingSourceDiscoveryQuery.next_attempt_at <= clock,
                        ),
                    )
                )
                pending_delayed = await session.scalar(
                    select(func.count())
                    .select_from(ScrapingSourceDiscoveryQuery)
                    .where(
                        ScrapingSourceDiscoveryQuery.organization_id == organization_id,
                        ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                        ScrapingSourceDiscoveryQuery.status
                        == SourceDiscoveryQueryStatus.PENDING,
                        ScrapingSourceDiscoveryQuery.next_attempt_at.is_not(None),
                        ScrapingSourceDiscoveryQuery.next_attempt_at > clock,
                    )
                )
                earliest = await session.scalar(
                    select(func.min(ScrapingSourceDiscoveryQuery.next_attempt_at)).where(
                        ScrapingSourceDiscoveryQuery.organization_id == organization_id,
                        ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                        ScrapingSourceDiscoveryQuery.status
                        == SourceDiscoveryQueryStatus.PENDING,
                        ScrapingSourceDiscoveryQuery.next_attempt_at.is_not(None),
                        ScrapingSourceDiscoveryQuery.next_attempt_at > clock,
                    )
                )
                running = await session.scalar(
                    select(func.count())
                    .select_from(ScrapingSourceDiscoveryQuery)
                    .where(
                        ScrapingSourceDiscoveryQuery.organization_id == organization_id,
                        ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                        ScrapingSourceDiscoveryQuery.status
                        == SourceDiscoveryQueryStatus.RUNNING,
                    )
                )
                expired = await session.scalar(
                    select(func.count())
                    .select_from(ScrapingSourceDiscoveryQuery)
                    .where(
                        ScrapingSourceDiscoveryQuery.organization_id == organization_id,
                        ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                        ScrapingSourceDiscoveryQuery.status
                        == SourceDiscoveryQueryStatus.RUNNING,
                        ScrapingSourceDiscoveryQuery.lease_expires_at.is_not(None),
                        ScrapingSourceDiscoveryQuery.lease_expires_at <= clock,
                    )
                )
                succeeded = await session.scalar(
                    select(func.count())
                    .select_from(ScrapingSourceDiscoveryQuery)
                    .where(
                        ScrapingSourceDiscoveryQuery.organization_id == organization_id,
                        ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                        ScrapingSourceDiscoveryQuery.status
                        == SourceDiscoveryQueryStatus.SUCCEEDED,
                    )
                )
                failed = await session.scalar(
                    select(func.count())
                    .select_from(ScrapingSourceDiscoveryQuery)
                    .where(
                        ScrapingSourceDiscoveryQuery.organization_id == organization_id,
                        ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                        ScrapingSourceDiscoveryQuery.status
                        == SourceDiscoveryQueryStatus.FAILED,
                    )
                )
                counts = dict(
                    pending_eligible_count=int(pending_eligible or 0),
                    pending_delayed_count=int(pending_delayed or 0),
                    running_count=int(running or 0),
                    expired_lease_count=int(expired or 0),
                    succeeded_count=int(succeeded or 0),
                    failed_count=int(failed or 0),
                    earliest_next_attempt_at=earliest,
                )
                if blocked is not None:
                    return WorkInspectionResult(
                        outcome="lifecycle_blocked",
                        lifecycle_reason=blocked,
                        **counts,
                    )
                return WorkInspectionResult(outcome="ok", **counts)

    async def _load_execution(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        execution_id: str,
    ) -> ScrapingExecution | None:
        result = await session.execute(
            select(ScrapingExecution)
            .where(
                ScrapingExecution.id == execution_id,
                ScrapingExecution.organization_id == organization_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def _load_scoped_job(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        execution_id: str,
        query_job_id: str,
    ) -> ScrapingSourceDiscoveryQuery | None:
        result = await session.execute(
            select(ScrapingSourceDiscoveryQuery)
            .where(
                ScrapingSourceDiscoveryQuery.id == query_job_id,
                ScrapingSourceDiscoveryQuery.organization_id == organization_id,
                ScrapingSourceDiscoveryQuery.execution_id == execution_id,
            )
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def _load_running_claim(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        execution_id: str,
        query_job_id: str,
    ) -> ScrapingSourceDiscoveryQuery | None:
        return await self._load_scoped_job(
            session,
            organization_id=organization_id,
            execution_id=execution_id,
            query_job_id=query_job_id,
        )


source_discovery_claim_service = SourceDiscoveryClaimService()

__all__ = [
    "BackoffPolicy",
    "ClaimBatchResult",
    "ClaimMutationResult",
    "ClaimPreflightResult",
    "ClaimedQueryJob",
    "DEFAULT_LEASE_DURATION",
    "DEFAULT_MAX_CLAIM_BATCH_SIZE",
    "DEFAULT_MAX_RECOVERY_BATCH_SIZE",
    "DEFAULT_RETRY_DELAY",
    "FALLBACK_ERROR_CODE",
    "LAST_ERROR_CODE_MAX_LENGTH",
    "LEASE_EXPIRED_ERROR_CODE",
    "RecoverExpiredClaimsResult",
    "SourceDiscoveryClaimService",
    "WorkInspectionResult",
    "cancel_supersedes_pause",
    "evaluate_claim_lifecycle",
    "exponential_backoff_policy",
    "fixed_backoff_policy",
    "generate_claim_token",
    "immediate_retry_policy",
    "normalize_lifecycle_error_code",
    "require_lifecycle_error_code",
    "source_discovery_claim_service",
    "validate_lease_duration",
    "validate_positive_batch_size",
    "validate_provider_id",
    "validate_retry_delay",
]
