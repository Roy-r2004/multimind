"""Phase 4 Slice 5: prepare + persist provider discovery results.

Two stages:
1. ``prepare_provider_results`` — pure, no DB/HTTP/LLM; canonicalize, safety, classify, hash.
2. ``persist_prepared_batch_and_succeed`` — short TX: token check → candidates/nodes → succeed.

Provider HTTP and DNS must already have completed outside any SQLAlchemy transaction.
Serper alone never fabricates crawl edges; root discovery nodes may have zero edges.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    CrawlNodeSourceClassification,
    ScrapingCrawlNode,
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingSourceCandidate,
    ScrapingSourceDiscoveryQuery,
    SourceCandidateStatus,
    SourceDiscoveryQueryStatus,
)
from app.db.session import AsyncSessionLocal
from app.services.scraping.discovery_url_service import (
    DiscoveryDnsResolver,
    canonicalize_discovery_target,
    classify_discovery_source,
    compute_canonical_url_hash,
    validate_discovery_target_safety,
)
from app.services.scraping.source_discovery_claim_service import (
    ClaimedQueryJob,
    cancel_supersedes_pause,
)
from app.services.scraping.source_discovery_provider_service import (
    DiscoveryProviderExecutionResult,
    DiscoveryProviderResultItem,
)

NowFactory = Callable[[], datetime]

PreparationOutcome = Literal["ready", "rejected"]
PersistenceOutcome = Literal[
    "applied",
    "page_continued",
    "idempotent_replay",
    "stale_claim",
    "lifecycle_blocked",
    "rejected",
    "hash_collision",
    "persistence_conflict",
    "database_failure",
]
LifecyclePersistenceBlock = Literal[
    "cancelled",
    "completed",
    "failed",
    "not_found",
    "not_eligible",
]

MAX_TITLE_LENGTH = 300
MAX_SNIPPET_LENGTH = 1000
MAX_URL_LENGTH = 2048
MAX_DOMAIN_LENGTH = 255

CRAWL_NODE_HASH_UNIQUE = "uq_crawl_node_org_exec_url_hash"
CANDIDATE_QUERY_URL_UNIQUE = "uq_source_candidate_query_url"

# Strong classifications that must not flip into each other without identical evidence.
_STRONG_CLASSIFICATIONS = frozenset(
    {
        CrawlNodeSourceClassification.OFFICIAL_FACILITY_SITE.value,
        CrawlNodeSourceClassification.FACILITY_PROFILE.value,
        CrawlNodeSourceClassification.DIRECTORY.value,
        CrawlNodeSourceClassification.REGISTRY.value,
        CrawlNodeSourceClassification.GOVERNMENT_SOURCE.value,
    }
)

# Lower rank number = better. Used only for relative merge conservatism.
_CLASSIFICATION_STRENGTH: Mapping[str, int] = {
    CrawlNodeSourceClassification.OFFICIAL_FACILITY_SITE.value: 100,
    CrawlNodeSourceClassification.FACILITY_PROFILE.value: 90,
    CrawlNodeSourceClassification.GOVERNMENT_SOURCE.value: 85,
    CrawlNodeSourceClassification.REGISTRY.value: 80,
    CrawlNodeSourceClassification.DIRECTORY.value: 75,
    CrawlNodeSourceClassification.COMMERCIAL_LISTING.value: 60,
    CrawlNodeSourceClassification.PDF.value: 55,
    CrawlNodeSourceClassification.SOCIAL_PROFILE.value: 50,
    CrawlNodeSourceClassification.SUPPORTING_SOURCE.value: 40,
    CrawlNodeSourceClassification.IRRELEVANT.value: 20,
    CrawlNodeSourceClassification.UNCLASSIFIED.value: 0,
}

_FLIP_PAIRS = frozenset(
    {
        frozenset(
            {
                CrawlNodeSourceClassification.OFFICIAL_FACILITY_SITE.value,
                CrawlNodeSourceClassification.DIRECTORY.value,
            }
        ),
        frozenset(
            {
                CrawlNodeSourceClassification.OFFICIAL_FACILITY_SITE.value,
                CrawlNodeSourceClassification.FACILITY_PROFILE.value,
            }
        ),
        frozenset(
            {
                CrawlNodeSourceClassification.OFFICIAL_FACILITY_SITE.value,
                CrawlNodeSourceClassification.REGISTRY.value,
            }
        ),
    }
)

_SECRETISH = re.compile(r"(key|token|secret|password|authorization|api[_-]?key)", re.I)


@dataclass(frozen=True)
class PreparedDiscoveryResult:
    """Immutable persistable (or rejected-but-representable) provider result."""

    original_url: str
    canonical_url: str
    canonical_url_hash: str
    hostname: str
    domain: str
    source_classification: str
    classification_reason_code: str
    title: str
    snippet: str
    rank: int
    provider: str
    provider_result_type: str | None
    discovered_at: datetime
    is_safe: bool
    safety_error_code: str | None
    persist_candidate: bool
    persist_crawl_node: bool
    rejection_code: str | None = None
    provider_page_number: int | None = None


@dataclass(frozen=True)
class PreparedDiscoveryBatch:
    """Immutable preparation output. Ready batches may still contain zero results."""

    outcome: PreparationOutcome
    organization_id: str
    execution_id: str
    query_job_id: str
    claim_token: str
    provider: str
    prepared_at: datetime
    results: tuple[PreparedDiscoveryResult, ...] = ()
    error_code: str | None = None
    raw_provider_count: int = 0
    parsed_provider_count: int = 0
    malformed_provider_count: int = 0
    invalid_url_count: int = 0
    unsafe_url_count: int = 0
    duplicate_within_query_count: int = 0
    # Provenance snapshot from the claimed job (authoritative for persistence).
    country_code: str = ""
    country_name: str = ""
    region_code: str | None = None
    region_name: str | None = None
    language_code: str = ""
    language_name: str = ""
    source_category: str = ""
    scope_level: str = ""
    important_city: str | None = None
    purpose: str = ""
    provider_page_number: int | None = None
    page_fingerprint: str | None = None
    has_more: bool | None = None

    @property
    def ready(self) -> bool:
        return self.outcome == "ready"


@dataclass(frozen=True)
class DiscoveryPersistenceCounts:
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
    crawl_edge_created_count: int = 0
    persisted_count: int = 0
    query_marked_succeeded: bool = False
    pages_completed: int | None = None
    next_page_number: int | None = None
    pagination_completed: bool = False


@dataclass(frozen=True)
class DiscoveryPersistenceResult:
    outcome: PersistenceOutcome
    counts: DiscoveryPersistenceCounts = field(default_factory=DiscoveryPersistenceCounts)
    error_code: str | None = None
    lifecycle_reason: LifecyclePersistenceBlock | None = None
    query_job_id: str | None = None
    query_status: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def merge_crawl_node_classification(existing: str, incoming: str) -> str:
    """Conservative deterministic reconciliation for preliminary crawl-node labels.

    Never qualifies facilities. Never downgrades to unclassified. Does not flip
    official ↔ directory/profile/registry without identical classification.
    """
    if existing == incoming:
        return existing
    if existing == CrawlNodeSourceClassification.UNCLASSIFIED.value:
        return incoming
    if incoming == CrawlNodeSourceClassification.UNCLASSIFIED.value:
        return existing
    if frozenset({existing, incoming}) in _FLIP_PAIRS:
        return existing
    if (
        existing in _STRONG_CLASSIFICATIONS
        and incoming in _STRONG_CLASSIFICATIONS
        and existing != incoming
    ):
        return existing
    existing_strength = _CLASSIFICATION_STRENGTH.get(existing, 0)
    incoming_strength = _CLASSIFICATION_STRENGTH.get(incoming, 0)
    if incoming_strength > existing_strength:
        return incoming
    return existing


def evaluate_persistence_lifecycle(
    execution: ScrapingExecution | None,
) -> LifecyclePersistenceBlock | None:
    """Lifecycle gate for late result finalization (differs from claim gating).

    Documented semantics:
    - cancel / cancel_requested → block (``cancelled``); cancel supersedes pause
    - completed / failed → block (terminal)
    - paused / pause_requested → allow finalize of an already-started provider request
    - running → allow
    """
    if execution is None:
        return "not_found"
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
        if cancel_supersedes_pause(execution):
            return "cancelled"
        return None
    if status != ScrapingExecutionStatus.RUNNING:
        return "not_eligible"
    return None


def prepare_provider_results(
    claimed_job: ClaimedQueryJob,
    provider_execution_result: DiscoveryProviderExecutionResult,
    *,
    resolver: DiscoveryDnsResolver | None = None,
    require_dns: bool = False,
    clock: datetime | None = None,
) -> PreparedDiscoveryBatch:
    """Pure preparation. No DB session, no provider HTTP, no LLM."""
    prepared_at = clock or _utc_now()
    base_kwargs = _claimed_provenance(claimed_job, prepared_at)

    mismatch = _validate_preparation_inputs(claimed_job, provider_execution_result)
    if mismatch is not None:
        return PreparedDiscoveryBatch(
            outcome="rejected",
            error_code=mismatch,
            results=(),
            **base_kwargs,
        )

    if provider_execution_result.outcome != "succeeded":
        return PreparedDiscoveryBatch(
            outcome="rejected",
            error_code="invalid_provider_batch",
            raw_provider_count=int(provider_execution_result.raw_result_count or 0),
            malformed_provider_count=int(provider_execution_result.skipped_malformed_count or 0),
            **base_kwargs,
        )

    raw_count = int(provider_execution_result.raw_result_count or 0)
    malformed = int(provider_execution_result.skipped_malformed_count or 0)
    parsed = int(provider_execution_result.accepted_result_count or len(provider_execution_result.results))

    invalid_url = 0
    unsafe_url = 0
    duplicates = 0
    # canonical_url -> best prepared result
    best_by_canonical: dict[str, PreparedDiscoveryResult] = {}

    for item in provider_execution_result.results:
        item_error = _validate_result_item_provenance(claimed_job, item)
        if item_error is not None:
            return PreparedDiscoveryBatch(
                outcome="rejected",
                error_code=item_error,
                raw_provider_count=raw_count,
                parsed_provider_count=parsed,
                malformed_provider_count=malformed,
                **base_kwargs,
            )

        prepared = _prepare_one_result(
            item,
            resolver=resolver,
            require_dns=require_dns,
        )
        if prepared is None:
            # Malformed / unusable URL — never fabricate a candidate URL.
            invalid_url += 1
            continue
        if not prepared.persist_candidate and prepared.rejection_code == "invalid_result_url":
            invalid_url += 1
            continue
        if prepared.persist_candidate and not prepared.persist_crawl_node:
            unsafe_url += 1

        existing = best_by_canonical.get(prepared.canonical_url)
        if existing is None:
            best_by_canonical[prepared.canonical_url] = prepared
            continue
        duplicates += 1
        # Deterministic best-rank: lower rank wins; tie-break by original URL then title.
        if _rank_sort_key(prepared) < _rank_sort_key(existing):
            best_by_canonical[prepared.canonical_url] = prepared

    ordered = tuple(
        sorted(best_by_canonical.values(), key=_rank_sort_key)
    )
    continuation = provider_execution_result.continuation
    page_number = (
        provider_execution_result.page_number
        or (continuation.page_number if continuation is not None else None)
    )
    page_fingerprint = (
        provider_execution_result.page_fingerprint
        or (continuation.page_fingerprint if continuation is not None else None)
    )
    has_more = continuation.has_more if continuation is not None else None
    return PreparedDiscoveryBatch(
        outcome="ready",
        error_code=None,
        results=ordered,
        raw_provider_count=raw_count,
        parsed_provider_count=parsed,
        malformed_provider_count=malformed,
        invalid_url_count=invalid_url,
        unsafe_url_count=unsafe_url,
        duplicate_within_query_count=duplicates,
        provider_page_number=page_number,
        page_fingerprint=page_fingerprint,
        has_more=has_more,
        **base_kwargs,
    )


class SourceDiscoveryResultService:
    """Short transactional persistence of prepared discovery batches."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        *,
        now_factory: NowFactory | None = None,
    ) -> None:
        self._session_factory = session_factory or AsyncSessionLocal
        self._now_factory = now_factory or _utc_now

    async def persist_prepared_batch_and_succeed(
        self,
        prepared_batch: PreparedDiscoveryBatch,
        *,
        now: datetime | None = None,
    ) -> DiscoveryPersistenceResult:
        """Token-protected persistence + final-page success (legacy / terminal page)."""
        return await self.persist_final_page_and_succeed(prepared_batch, now=now)

    async def persist_page_and_continue(
        self,
        prepared_batch: PreparedDiscoveryBatch,
        *,
        now: datetime | None = None,
        next_attempt_at: datetime | None = None,
    ) -> DiscoveryPersistenceResult:
        """Persist one page, advance cursor, clear claim, return job to pending."""
        return await self._persist_page(
            prepared_batch,
            finalize=False,
            now=now,
            next_attempt_at=next_attempt_at,
        )

    async def persist_final_page_and_succeed(
        self,
        prepared_batch: PreparedDiscoveryBatch,
        *,
        now: datetime | None = None,
    ) -> DiscoveryPersistenceResult:
        """Persist final page and mark query succeeded with pagination_completed."""
        return await self._persist_page(
            prepared_batch,
            finalize=True,
            now=now,
            next_attempt_at=None,
        )

    async def _persist_page(
        self,
        prepared_batch: PreparedDiscoveryBatch,
        *,
        finalize: bool,
        now: datetime | None,
        next_attempt_at: datetime | None,
    ) -> DiscoveryPersistenceResult:
        clock = now or self._now_factory()
        counts_base = DiscoveryPersistenceCounts(
            raw_provider_count=prepared_batch.raw_provider_count,
            parsed_provider_count=prepared_batch.parsed_provider_count,
            malformed_provider_count=prepared_batch.malformed_provider_count,
            invalid_url_count=prepared_batch.invalid_url_count,
            unsafe_url_count=prepared_batch.unsafe_url_count,
            duplicate_within_query_count=prepared_batch.duplicate_within_query_count,
        )

        if not isinstance(prepared_batch, PreparedDiscoveryBatch) or not prepared_batch.ready:
            return DiscoveryPersistenceResult(
                outcome="rejected",
                error_code=prepared_batch.error_code or "invalid_provider_batch",
                counts=counts_base,
                query_job_id=getattr(prepared_batch, "query_job_id", None),
            )

        try:
            async with self._session_factory() as session:
                async with session.begin():
                    _require_postgresql(session)
                    return await self._persist_in_transaction(
                        session,
                        prepared_batch,
                        clock=clock,
                        counts_base=counts_base,
                        finalize=finalize,
                        next_attempt_at=next_attempt_at,
                    )
        except _HashCollisionError:
            return DiscoveryPersistenceResult(
                outcome="hash_collision",
                error_code="canonical_url_hash_collision",
                counts=counts_base,
                query_job_id=prepared_batch.query_job_id,
            )
        except Exception:
            return DiscoveryPersistenceResult(
                outcome="database_failure",
                error_code="database_failure",
                counts=counts_base,
                query_job_id=prepared_batch.query_job_id,
            )

    async def _persist_in_transaction(
        self,
        session: AsyncSession,
        batch: PreparedDiscoveryBatch,
        *,
        clock: datetime,
        counts_base: DiscoveryPersistenceCounts,
        finalize: bool = True,
        next_attempt_at: datetime | None = None,
    ) -> DiscoveryPersistenceResult:
        execution = await self._load_execution(
            session,
            organization_id=batch.organization_id,
            execution_id=batch.execution_id,
        )
        blocked = evaluate_persistence_lifecycle(execution)
        if blocked is not None:
            code = (
                "execution_cancelled"
                if blocked == "cancelled"
                else "execution_terminal"
                if blocked in {"completed", "failed"}
                else "execution_terminal"
            )
            return DiscoveryPersistenceResult(
                outcome="lifecycle_blocked",
                error_code=code,
                lifecycle_reason=blocked,
                counts=counts_base,
                query_job_id=batch.query_job_id,
                query_status=execution.status.value if execution is not None else None,
            )

        job = await self._load_job_for_update(
            session,
            organization_id=batch.organization_id,
            execution_id=batch.execution_id,
            query_job_id=batch.query_job_id,
        )
        if job is None:
            return DiscoveryPersistenceResult(
                outcome="rejected",
                error_code="query_job_mismatch",
                counts=counts_base,
                query_job_id=batch.query_job_id,
            )

        if job.status == SourceDiscoveryQueryStatus.SUCCEEDED and finalize:
            return DiscoveryPersistenceResult(
                outcome="idempotent_replay",
                error_code=None,
                counts=counts_base,
                query_job_id=batch.query_job_id,
                query_status=job.status.value,
            )

        if job.status != SourceDiscoveryQueryStatus.RUNNING:
            return DiscoveryPersistenceResult(
                outcome="rejected",
                error_code="invalid_provider_batch",
                counts=counts_base,
                query_job_id=batch.query_job_id,
                query_status=job.status.value,
            )

        if job.claim_token != batch.claim_token:
            return DiscoveryPersistenceResult(
                outcome="stale_claim",
                error_code="stale_claim",
                counts=counts_base,
                query_job_id=batch.query_job_id,
                query_status=job.status.value,
            )

        if (job.provider or "").strip().lower() != batch.provider.strip().lower():
            return DiscoveryPersistenceResult(
                outcome="rejected",
                error_code="provider_mismatch",
                counts=counts_base,
                query_job_id=batch.query_job_id,
                query_status=job.status.value,
            )

        page_number = int(
            batch.provider_page_number
            or getattr(job, "next_page_number", None)
            or 1
        )
        if page_number < 1:
            page_number = 1

        candidate_inserted = 0
        candidate_existing = 0
        node_created = 0
        node_existing = 0
        persisted = 0

        for prepared in batch.results:
            if not prepared.persist_candidate:
                continue

            crawl_node_id: str | None = None
            if prepared.persist_crawl_node:
                node, created = await self._upsert_crawl_node(
                    session,
                    organization_id=batch.organization_id,
                    execution_id=batch.execution_id,
                    prepared=prepared,
                    first_seen_at=prepared.discovered_at or clock,
                )
                if created:
                    node_created += 1
                else:
                    node_existing += 1
                crawl_node_id = node.id

            existing_candidate = await self._find_candidate(
                session,
                organization_id=batch.organization_id,
                discovery_query_id=batch.query_job_id,
                canonical_url=prepared.canonical_url,
            )
            if existing_candidate is not None:
                # Cross-page within-query uniqueness: keep first page occurrence.
                # Within-page ranks are not inventively remapped to absolute ranks.
                page_conflict = reconcile_candidate_page_provenance(
                    existing_candidate, page_number
                )
                if page_conflict is not None:
                    return DiscoveryPersistenceResult(
                        outcome="rejected",
                        error_code=page_conflict,
                        counts=counts_base,
                        query_job_id=batch.query_job_id,
                        query_status=job.status.value,
                    )
                candidate_existing += 1
                if (
                    crawl_node_id is not None
                    and existing_candidate.crawl_node_id is None
                ):
                    existing_candidate.crawl_node_id = crawl_node_id
                persisted += 1
                continue

            candidate = ScrapingSourceCandidate(
                organization_id=batch.organization_id,
                execution_id=batch.execution_id,
                coverage_cell_id=job.coverage_cell_id,
                discovery_query_id=batch.query_job_id,
                crawl_node_id=crawl_node_id,
                provider=batch.provider,
                provider_result_id=None,
                rank=prepared.rank,
                provider_page_number=(
                    prepared.provider_page_number
                    if prepared.provider_page_number is not None
                    else page_number
                ),
                url=_bounded(prepared.original_url, MAX_URL_LENGTH),
                canonical_url=_bounded(prepared.canonical_url, MAX_URL_LENGTH),
                domain=_bounded(prepared.domain, MAX_DOMAIN_LENGTH),
                title=_bounded(prepared.title, MAX_TITLE_LENGTH),
                snippet=_bounded(prepared.snippet, MAX_SNIPPET_LENGTH),
                country_code=batch.country_code or job.country_code,
                country_name=batch.country_name or job.country_name,
                region_code=batch.region_code
                if batch.region_code is not None
                else job.region_code,
                region_name=batch.region_name
                if batch.region_name is not None
                else job.region_name,
                language_code=batch.language_code or job.language_code,
                language_name=batch.language_name or job.language_name,
                source_category=batch.source_category or job.source_category,
                initial_relevance_score=Decimal(str(_relevance_for_rank(prepared.rank))),
                initial_trust_tier=_trust_tier(batch.source_category or job.source_category),
                status=(
                    SourceCandidateStatus.REJECTED
                    if not prepared.persist_crawl_node
                    else SourceCandidateStatus.DISCOVERED
                ),
                discovered_at=prepared.discovered_at or clock,
                metadata_json=_candidate_metadata(prepared),
            )
            try:
                async with session.begin_nested():
                    session.add(candidate)
                    await session.flush()
                candidate_inserted += 1
                persisted += 1
            except IntegrityError as exc:
                if not _is_unique_violation(exc, CANDIDATE_QUERY_URL_UNIQUE):
                    raise
                raced = await self._find_candidate(
                    session,
                    organization_id=batch.organization_id,
                    discovery_query_id=batch.query_job_id,
                    canonical_url=prepared.canonical_url,
                )
                if raced is None:
                    raise
                page_conflict = reconcile_candidate_page_provenance(raced, page_number)
                if page_conflict is not None:
                    return DiscoveryPersistenceResult(
                        outcome="rejected",
                        error_code=page_conflict,
                        counts=counts_base,
                        query_job_id=batch.query_job_id,
                        query_status=job.status.value,
                    )
                candidate_existing += 1
                persisted += 1

        pages_completed = int(getattr(job, "pages_completed", 0) or 0) + 1
        job.pages_completed = pages_completed
        job.last_page_result_count = int(batch.raw_provider_count or 0)
        if batch.page_fingerprint:
            job.last_page_fingerprint = batch.page_fingerprint
        job.result_count = int(job.result_count or 0) + candidate_inserted
        # Clear claim fields for both continue and succeed paths.
        job.claim_token = None
        job.claimed_at = None
        job.lease_expires_at = None
        job.last_error_code = None
        job.last_error_at = None
        job.error_code = None
        job.error_message = None

        if finalize:
            job.status = SourceDiscoveryQueryStatus.SUCCEEDED
            job.completed_at = clock
            job.next_attempt_at = None
            job.pagination_completed = True
            job.pagination_completed_at = clock
            # Leave next_page_number at the completed page (no invented advance).
            job.next_page_number = page_number
            return DiscoveryPersistenceResult(
                outcome="applied",
                error_code=None,
                query_job_id=batch.query_job_id,
                query_status=SourceDiscoveryQueryStatus.SUCCEEDED.value,
                counts=DiscoveryPersistenceCounts(
                    raw_provider_count=counts_base.raw_provider_count,
                    parsed_provider_count=counts_base.parsed_provider_count,
                    malformed_provider_count=counts_base.malformed_provider_count,
                    invalid_url_count=counts_base.invalid_url_count,
                    unsafe_url_count=counts_base.unsafe_url_count,
                    duplicate_within_query_count=counts_base.duplicate_within_query_count,
                    candidate_inserted_count=candidate_inserted,
                    candidate_existing_count=candidate_existing,
                    crawl_node_created_count=node_created,
                    crawl_node_existing_count=node_existing,
                    crawl_edge_created_count=0,
                    persisted_count=persisted,
                    query_marked_succeeded=True,
                    pages_completed=pages_completed,
                    next_page_number=page_number,
                    pagination_completed=True,
                ),
            )

        # Intermediate page: return to pending for next page (immediate or small delay).
        next_page = page_number + 1
        job.status = SourceDiscoveryQueryStatus.PENDING
        job.next_page_number = next_page
        job.pagination_completed = False
        job.pagination_completed_at = None
        job.completed_at = None
        job.next_attempt_at = next_attempt_at if next_attempt_at is not None else clock
        return DiscoveryPersistenceResult(
            outcome="page_continued",
            error_code=None,
            query_job_id=batch.query_job_id,
            query_status=SourceDiscoveryQueryStatus.PENDING.value,
            counts=DiscoveryPersistenceCounts(
                raw_provider_count=counts_base.raw_provider_count,
                parsed_provider_count=counts_base.parsed_provider_count,
                malformed_provider_count=counts_base.malformed_provider_count,
                invalid_url_count=counts_base.invalid_url_count,
                unsafe_url_count=counts_base.unsafe_url_count,
                duplicate_within_query_count=counts_base.duplicate_within_query_count,
                candidate_inserted_count=candidate_inserted,
                candidate_existing_count=candidate_existing,
                crawl_node_created_count=node_created,
                crawl_node_existing_count=node_existing,
                crawl_edge_created_count=0,
                persisted_count=persisted,
                query_marked_succeeded=False,
                pages_completed=pages_completed,
                next_page_number=next_page,
                pagination_completed=False,
            ),
        )

    async def _upsert_crawl_node(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        execution_id: str,
        prepared: PreparedDiscoveryResult,
        first_seen_at: datetime,
    ) -> tuple[ScrapingCrawlNode, bool]:
        existing = await self._find_crawl_node_by_hash(
            session,
            organization_id=organization_id,
            execution_id=execution_id,
            canonical_url_hash=prepared.canonical_url_hash,
        )
        if existing is not None:
            _assert_canonical_url_match(existing, prepared.canonical_url)
            merged = merge_crawl_node_classification(
                existing.source_classification.value
                if isinstance(existing.source_classification, CrawlNodeSourceClassification)
                else str(existing.source_classification),
                prepared.source_classification,
            )
            if merged != (
                existing.source_classification.value
                if isinstance(existing.source_classification, CrawlNodeSourceClassification)
                else str(existing.source_classification)
            ):
                existing.source_classification = CrawlNodeSourceClassification(merged)
            return existing, False

        node = ScrapingCrawlNode(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            execution_id=execution_id,
            canonical_url=prepared.canonical_url,
            canonical_url_hash=prepared.canonical_url_hash,
            hostname=_bounded(prepared.hostname, MAX_DOMAIN_LENGTH),
            domain=_bounded(prepared.domain, MAX_DOMAIN_LENGTH),
            source_classification=CrawlNodeSourceClassification(prepared.source_classification),
            first_seen_at=first_seen_at,
        )
        try:
            async with session.begin_nested():
                session.add(node)
                await session.flush()
            return node, True
        except IntegrityError as exc:
            if not _is_unique_violation(exc, CRAWL_NODE_HASH_UNIQUE):
                raise
            raced = await self._find_crawl_node_by_hash(
                session,
                organization_id=organization_id,
                execution_id=execution_id,
                canonical_url_hash=prepared.canonical_url_hash,
            )
            if raced is None:
                raise
            _assert_canonical_url_match(raced, prepared.canonical_url)
            merged = merge_crawl_node_classification(
                raced.source_classification.value
                if isinstance(raced.source_classification, CrawlNodeSourceClassification)
                else str(raced.source_classification),
                prepared.source_classification,
            )
            if merged != (
                raced.source_classification.value
                if isinstance(raced.source_classification, CrawlNodeSourceClassification)
                else str(raced.source_classification)
            ):
                raced.source_classification = CrawlNodeSourceClassification(merged)
            return raced, False

    async def _load_execution(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        execution_id: str,
    ) -> ScrapingExecution | None:
        result = await session.execute(
            select(ScrapingExecution).where(
                ScrapingExecution.id == execution_id,
                ScrapingExecution.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def _load_job_for_update(
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

    async def _find_crawl_node_by_hash(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        execution_id: str,
        canonical_url_hash: str,
    ) -> ScrapingCrawlNode | None:
        result = await session.execute(
            select(ScrapingCrawlNode).where(
                ScrapingCrawlNode.organization_id == organization_id,
                ScrapingCrawlNode.execution_id == execution_id,
                ScrapingCrawlNode.canonical_url_hash == canonical_url_hash,
            )
        )
        return result.scalar_one_or_none()

    async def _find_candidate(
        self,
        session: AsyncSession,
        *,
        organization_id: str,
        discovery_query_id: str,
        canonical_url: str,
    ) -> ScrapingSourceCandidate | None:
        result = await session.execute(
            select(ScrapingSourceCandidate).where(
                ScrapingSourceCandidate.organization_id == organization_id,
                ScrapingSourceCandidate.discovery_query_id == discovery_query_id,
                ScrapingSourceCandidate.canonical_url == canonical_url,
            )
        )
        return result.scalar_one_or_none()


class _HashCollisionError(RuntimeError):
    """Internal signal: scoped hash matched a different canonical URL."""


def _assert_canonical_url_match(node: ScrapingCrawlNode, canonical_url: str) -> None:
    if node.canonical_url != canonical_url:
        raise _HashCollisionError("canonical_url_hash_collision")


def _require_postgresql(session: AsyncSession) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError(
            "source_discovery_result_service requires PostgreSQL "
            "(FOR UPDATE / savepoints). SQLite is not supported for persistence."
        )


def _claimed_provenance(claimed_job: ClaimedQueryJob, prepared_at: datetime) -> dict[str, Any]:
    return {
        "organization_id": claimed_job.organization_id,
        "execution_id": claimed_job.execution_id,
        "query_job_id": claimed_job.id,
        "claim_token": claimed_job.claim_token,
        "provider": (claimed_job.provider or "").strip().lower(),
        "prepared_at": prepared_at,
        "country_code": claimed_job.country_code,
        "country_name": claimed_job.country_name,
        "region_code": claimed_job.region_code,
        "region_name": claimed_job.region_name,
        "language_code": claimed_job.language_code,
        "language_name": claimed_job.language_name,
        "source_category": claimed_job.source_category,
        "scope_level": claimed_job.scope_level,
        "important_city": claimed_job.important_city,
        "purpose": claimed_job.purpose,
    }


def _validate_preparation_inputs(
    claimed_job: ClaimedQueryJob,
    result: DiscoveryProviderExecutionResult,
) -> str | None:
    if not isinstance(claimed_job, ClaimedQueryJob):
        return "invalid_provider_batch"
    if not isinstance(result, DiscoveryProviderExecutionResult):
        return "invalid_provider_batch"
    if result.organization_id != claimed_job.organization_id:
        return "organization_mismatch"
    if result.execution_id != claimed_job.execution_id:
        return "execution_mismatch"
    if result.query_job_id != claimed_job.id:
        return "query_job_mismatch"
    if result.claim_token != claimed_job.claim_token:
        return "stale_claim"
    claimed_provider = (claimed_job.provider or "").strip().lower()
    result_provider = (result.provider or "").strip().lower()
    if not claimed_provider or claimed_provider != result_provider:
        return "provider_mismatch"
    return None


def _validate_result_item_provenance(
    claimed_job: ClaimedQueryJob,
    item: DiscoveryProviderResultItem,
) -> str | None:
    if not isinstance(item, DiscoveryProviderResultItem):
        return "invalid_provider_batch"
    if item.organization_id != claimed_job.organization_id:
        return "organization_mismatch"
    if item.execution_id != claimed_job.execution_id:
        return "execution_mismatch"
    if item.query_job_id != claimed_job.id:
        return "query_job_mismatch"
    if item.claim_token != claimed_job.claim_token:
        return "stale_claim"
    if (item.provider or "").strip().lower() != (claimed_job.provider or "").strip().lower():
        return "provider_mismatch"
    if item.scope_level != claimed_job.scope_level:
        return "invalid_provider_batch"
    if item.language_code != claimed_job.language_code:
        return "invalid_provider_batch"
    if item.country_code != claimed_job.country_code:
        return "invalid_provider_batch"
    if item.region_code != claimed_job.region_code:
        return "invalid_provider_batch"
    if item.region_name != claimed_job.region_name:
        return "invalid_provider_batch"
    if item.important_city != claimed_job.important_city:
        return "invalid_provider_batch"
    return None


def _prepare_one_result(
    item: DiscoveryProviderResultItem,
    *,
    resolver: DiscoveryDnsResolver | None,
    require_dns: bool,
) -> PreparedDiscoveryResult | None:
    original = item.original_url if isinstance(item.original_url, str) else ""
    if not original.strip():
        return None

    canonicalization = canonicalize_discovery_target(original)
    if not canonicalization.is_valid or not canonicalization.canonical_url:
        return PreparedDiscoveryResult(
            original_url=original.strip() or original,
            canonical_url="",
            canonical_url_hash="",
            hostname="",
            domain="",
            source_classification=CrawlNodeSourceClassification.IRRELEVANT.value,
            classification_reason_code="invalid_or_unsafe_target",
            title=_normalize_text(item.title, MAX_TITLE_LENGTH),
            snippet=_normalize_text(item.snippet, MAX_SNIPPET_LENGTH),
            rank=max(int(item.rank), 1),
            provider=(item.provider or "").strip().lower(),
            provider_result_type=item.provider_result_type,
            discovered_at=item.discovered_at,
            is_safe=False,
            safety_error_code=canonicalization.error_code or "invalid_result_url",
            persist_candidate=False,
            persist_crawl_node=False,
            rejection_code="invalid_result_url",
            provider_page_number=getattr(item, "provider_page_number", None),
        )

    hostname = canonicalization.hostname or ""
    safety = validate_discovery_target_safety(
        hostname,
        resolver=resolver,
        require_dns=require_dns,
    )
    # Static safety from canonicalization also applies (literal private IPs etc.).
    is_safe = bool(canonicalization.is_statically_safe and safety.is_safe)
    safety_code = None if is_safe else (safety.error_code or canonicalization.error_code or "unsafe_result_url")

    classification = classify_discovery_source(
        canonical_url=canonicalization.canonical_url,
        hostname=hostname,
        path=canonicalization.path,
        title=item.title,
        snippet=item.snippet,
        is_valid=True,
        is_safe=is_safe,
        error_code=safety_code,
    )
    domain = canonicalization.normalized_domain or hostname
    url_hash = compute_canonical_url_hash(canonicalization.canonical_url)

    return PreparedDiscoveryResult(
        original_url=canonicalization.original_url,
        canonical_url=canonicalization.canonical_url,
        canonical_url_hash=url_hash,
        hostname=hostname,
        domain=domain,
        source_classification=classification.classification,
        classification_reason_code=classification.reason_code,
        title=_normalize_text(item.title, MAX_TITLE_LENGTH),
        snippet=_normalize_text(item.snippet, MAX_SNIPPET_LENGTH),
        rank=max(int(item.rank), 1),
        provider=(item.provider or "").strip().lower(),
        provider_result_type=item.provider_result_type,
        discovered_at=item.discovered_at,
        is_safe=is_safe,
        safety_error_code=safety_code,
        persist_candidate=True,
        persist_crawl_node=is_safe,
        rejection_code=None if is_safe else "unsafe_result_url",
        provider_page_number=getattr(item, "provider_page_number", None),
    )


def _rank_sort_key(prepared: PreparedDiscoveryResult) -> tuple[int, str, str]:
    return (prepared.rank, prepared.original_url, prepared.title)


def _normalize_text(value: Any, max_len: int) -> str:
    """Preserve blank for missing title/snippet — never fabricate content."""
    if value is None:
        return ""
    if not isinstance(value, str):
        return ""
    return value[:max_len]


def _bounded(value: str, max_len: int) -> str:
    return (value or "")[:max_len]


def _relevance_for_rank(rank: int) -> float:
    return max(0.1, min(1.0, 1.0 - ((max(rank, 1) - 1) * 0.05)))


def _trust_tier(source_category: str) -> str:
    normalized = (source_category or "").casefold()
    if any(
        term in normalized
        for term in ("official", "government", "registry", "license", "regulator", "ministry")
    ):
        return "high"
    if any(term in normalized for term in ("directory", "association", "hospital", "ngo")):
        return "medium"
    return "unknown"


def _candidate_metadata(prepared: PreparedDiscoveryResult) -> dict[str, Any]:
    """Safe normalized metadata only — never private IPs, hashes, or secrets."""
    meta: dict[str, Any] = {}
    if prepared.provider_result_type:
        meta["provider_result_type"] = str(prepared.provider_result_type)[:64]
    if prepared.persist_crawl_node:
        meta["source_classification"] = prepared.source_classification
        meta["classification_reason_code"] = prepared.classification_reason_code
    elif prepared.rejection_code:
        meta["discovery_rejection"] = prepared.rejection_code
    return meta


def reconcile_candidate_page_provenance(
    existing: ScrapingSourceCandidate,
    page_number: int,
) -> str | None:
    """Apply or validate provider page provenance on an existing query-owned candidate.

    ``provider_page_number`` stores the FIRST provider page where this URL was
    discovered within the query. Search providers commonly repeat URLs across pages;
    later pages preserve the earlier first-seen page rather than overwriting it.

    Returns ``provider_page_provenance_conflict`` when existing provenance is later
    than the requested page (impossible backward provenance). Repairs null provenance
    when query ownership makes the page unambiguous.
    """
    existing_page = existing.provider_page_number
    if existing_page is None:
        existing.provider_page_number = page_number
        return None
    if existing_page > page_number:
        return "provider_page_provenance_conflict"
    # existing_page == page_number: same-page idempotent replay
    # existing_page < page_number: legitimate cross-page duplicate — preserve first-seen
    return None


def _is_unique_violation(exc: IntegrityError, constraint_name: str) -> bool:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    if diag is not None:
        name = getattr(diag, "constraint_name", None)
        if name == constraint_name:
            return True
    text = str(orig or exc)
    if _SECRETISH.search(text):
        # Still allow constraint-name match without exposing details upstream.
        pass
    return constraint_name in text


__all__ = [
    "PreparedDiscoveryResult",
    "PreparedDiscoveryBatch",
    "DiscoveryPersistenceCounts",
    "DiscoveryPersistenceResult",
    "SourceDiscoveryResultService",
    "prepare_provider_results",
    "merge_crawl_node_classification",
    "evaluate_persistence_lifecycle",
    "reconcile_candidate_page_provenance",
]
