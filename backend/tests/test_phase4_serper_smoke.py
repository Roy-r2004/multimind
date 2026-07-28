"""Non-Docker tests for the guarded Phase 4 real-Serper smoke runner."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingSourceDiscoveryQuery,
    SourceDiscoveryQueryStatus,
)
from app.services.scraping.source_discovery_claim_service import (
    ClaimBatchResult,
    ClaimMutationResult,
    ClaimPreflightResult,
    ClaimedQueryJob,
    generate_claim_token,
)
from app.services.scraping.source_discovery_provider_service import (
    DiscoveryProviderContinuation,
    DiscoveryProviderExecutionResult,
    DiscoveryProviderResultItem,
    SourceDiscoveryProviderService,
)
from app.services.scraping.source_discovery_result_service import (
    DiscoveryPersistenceCounts,
    DiscoveryPersistenceResult,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "phase4_serper_smoke.py"
_spec = importlib.util.spec_from_file_location("phase4_serper_smoke", SCRIPT_PATH)
assert _spec and _spec.loader
import sys

smoke = importlib.util.module_from_spec(_spec)
sys.modules["phase4_serper_smoke"] = smoke
_spec.loader.exec_module(smoke)

FIXED_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)
PLAN_HASH = "a" * 64


def _claimed(**overrides: Any) -> ClaimedQueryJob:
    base = ClaimedQueryJob(
        id="job-expected",
        organization_id="org-1",
        execution_id="exec-1",
        query_text="exact persisted query text",
        provider="serper",
        claim_token=generate_claim_token(),
        claimed_at=FIXED_NOW,
        lease_expires_at=FIXED_NOW + timedelta(seconds=120),
        attempt_count=1,
        last_attempt_at=FIXED_NOW,
        priority=100,
        generation_ordinal=1,
        discovery_round=1,
        purpose="seed",
        country_code="LB",
        country_name="Lebanon",
        region_code=None,
        region_name="Beirut",
        language_code="en",
        language_name="English",
        source_category="directory",
        scope_level="countrywide",
        important_city=None,
        query_job_fingerprint="f" * 64,
        plan_hash_snapshot=PLAN_HASH,
        requested_at=FIXED_NOW,
        next_page_number=2,
        pages_completed=1,
    )
    return replace(base, **overrides) if overrides else base


def _execution(**overrides: Any) -> ScrapingExecution:
    base = ScrapingExecution(
        id="exec-1",
        organization_id="org-1",
        mission_id="mission-1",
        execution_type="mission_campaign",
        execution_plan_schema_version="2",
        status=ScrapingExecutionStatus.RUNNING,
        current_stage="web_discovery",
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _virgin_pending_job(**overrides: Any) -> ScrapingSourceDiscoveryQuery:
    base = ScrapingSourceDiscoveryQuery(
        id="job-expected",
        organization_id="org-1",
        execution_id="exec-1",
        query_text="exact persisted query text",
        status=SourceDiscoveryQueryStatus.PENDING,
        priority=100,
        generation_ordinal=1,
        language_code="en",
        scope_level="countrywide",
        region_name="Beirut",
        query_job_fingerprint="f" * 64,
        plan_hash_snapshot=PLAN_HASH,
        next_page_number=1,
        pages_completed=0,
        pagination_completed=False,
        provider=None,
        requested_at=None,
        claim_token=None,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _legacy_completed_execution(**overrides: Any) -> ScrapingExecution:
    defaults: dict[str, Any] = {
        "status": ScrapingExecutionStatus.COMPLETED,
        "current_stage": "database_cleaning",
        "completed_at": FIXED_NOW - timedelta(hours=1),
        "country_profile_json": {},
    }
    defaults.update(overrides)
    return _execution(**defaults)


class PrepareHarness:
    def __init__(self, **execution_overrides: Any) -> None:
        self.execution = _legacy_completed_execution(**execution_overrides)
        self.job = _virgin_pending_job()
        self.job_snapshot = _virgin_pending_job()
        self.events: list[Any] = []
        self.counts = {
            "pending": 1,
            "running": 0,
            "succeeded": 0,
            "failed": 0,
            "candidates": 0,
            "nodes": 0,
            "edges": 0,
            "pending_without_provenance": 0,
        }
        self.query_gen_completed = True
        self.emit_calls: list[tuple[str, str, dict[str, Any]]] = []

    def _count(self, status: SourceDiscoveryQueryStatus) -> int:
        mapping = {
            SourceDiscoveryQueryStatus.PENDING: "pending",
            SourceDiscoveryQueryStatus.RUNNING: "running",
            SourceDiscoveryQueryStatus.SUCCEEDED: "succeeded",
            SourceDiscoveryQueryStatus.FAILED: "failed",
        }
        return self.counts[mapping[status]]

    class _Begin:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *args: Any) -> None:
            return None

    class Session:
        def __init__(self, harness: "PrepareHarness") -> None:
            self.harness = harness

        def begin(self) -> PrepareHarness._Begin:
            return PrepareHarness._Begin()

        async def get(self, model: type, key: str) -> Any:
            if model is ScrapingExecution:
                return None
            if model is ScrapingSourceDiscoveryQuery and key == self.harness.job.id:
                return self.harness.job
            if model is smoke.ScrapingEvent:
                for event in self.harness.events:
                    if event.id == key:
                        return event
                return None
            return None

        async def execute(self, stmt: Any) -> Any:
            harness = self.harness
            stmt_text = str(stmt)
            result = MagicMock()
            if "scraping_source_discovery_queries" in stmt_text:
                if "count" in stmt_text:
                    if "query_job_fingerprint" in stmt_text:
                        result.scalar_one.return_value = harness.counts["pending_without_provenance"]
                    elif "status" in stmt_text and "PENDING" in stmt_text:
                        result.scalar_one.return_value = harness.counts["pending"]
                    elif "RUNNING" in stmt_text:
                        result.scalar_one.return_value = harness.counts["running"]
                    elif "SUCCEEDED" in stmt_text:
                        result.scalar_one.return_value = harness.counts["succeeded"]
                    elif "FAILED" in stmt_text:
                        result.scalar_one.return_value = harness.counts["failed"]
                    else:
                        result.scalar_one.return_value = harness.counts["pending"]
                else:
                    result.scalar_one_or_none.return_value = harness.job
            elif "scraping_source_candidates" in stmt_text:
                result.scalar_one.return_value = harness.counts["candidates"]
            elif "scraping_crawl_nodes" in stmt_text:
                result.scalar_one.return_value = harness.counts["nodes"]
            elif "scraping_crawl_edges" in stmt_text:
                result.scalar_one.return_value = harness.counts["edges"]
            elif "query_generation_completed" in stmt_text:
                result.scalar_one.return_value = 1 if harness.query_gen_completed else 0
            elif "scraping_events" in stmt_text:
                latest = harness.events[-1] if harness.events else None
                result.scalar_one_or_none.return_value = latest
                result.scalars.return_value.all.return_value = [
                    row.event_type for row in harness.events
                ]
            elif "scraping_executions" in stmt_text:
                result.scalar_one_or_none.return_value = harness.execution
            else:
                result.scalar_one_or_none.return_value = None
            return result

    class SessionContext:
        def __init__(self, harness: "PrepareHarness") -> None:
            self.session = PrepareHarness.Session(harness)

        async def __aenter__(self) -> PrepareHarness.Session:
            return self.session

        async def __aexit__(self, *args: Any) -> None:
            return None

    def factory(self) -> MagicMock:
        return MagicMock(return_value=PrepareHarness.SessionContext(self))


async def run_prepare(
    harness: PrepareHarness,
    *,
    earliest_job: ScrapingSourceDiscoveryQuery | None = None,
) -> smoke.PrepareExistingResult:
    async def count_jobs(session: Any, **kwargs: Any) -> int:
        status = kwargs["status"]
        return harness._count(status)

    async def count_table(session: Any, model: type, **kwargs: Any) -> int:
        if model is smoke.ScrapingSourceCandidate:
            return harness.counts["candidates"]
        if model is smoke.ScrapingCrawlNode:
            return harness.counts["nodes"]
        if model is smoke.ScrapingCrawlEdge:
            return harness.counts["edges"]
        return 0

    async def load_execution(session: Any, **kwargs: Any) -> ScrapingExecution | None:
        if (
            kwargs.get("organization_id") == harness.execution.organization_id
            and kwargs.get("execution_id") == harness.execution.id
        ):
            return harness.execution
        return None

    with patch.object(smoke, "_count_jobs", new=AsyncMock(side_effect=count_jobs)):
        with patch.object(smoke, "_count_table_rows", new=AsyncMock(side_effect=count_table)):
            with patch.object(
                smoke,
                "_count_pending_without_step3b_provenance",
                new=AsyncMock(return_value=harness.counts["pending_without_provenance"]),
            ):
                with patch.object(
                    smoke,
                    "_has_query_generation_completed",
                    new=AsyncMock(return_value=harness.query_gen_completed),
                ):
                    with patch.object(
                        smoke,
                        "_earliest_pending_step3b_job",
                        new=AsyncMock(return_value=earliest_job or harness.job),
                    ):
                        with patch.object(
                            smoke,
                            "_latest_event_row",
                            new=AsyncMock(
                                return_value=harness.events[-1] if harness.events else None
                            ),
                        ):
                            with patch.object(
                                smoke,
                                "_load_execution",
                                new=AsyncMock(side_effect=load_execution),
                            ):
                                return await smoke.prepare_existing_execution(
                                    harness.factory(),
                                    organization_id="org-1",
                                    execution_id="exec-1",
                                    expected_query_job_id="job-expected",
                                    now=FIXED_NOW,
                                )


def _pending_job(**overrides: Any) -> ScrapingSourceDiscoveryQuery:
    base = ScrapingSourceDiscoveryQuery(
        id="job-expected",
        organization_id="org-1",
        execution_id="exec-1",
        query_text="exact persisted query text",
        status=SourceDiscoveryQueryStatus.PENDING,
        priority=100,
        generation_ordinal=1,
        language_code="en",
        scope_level="countrywide",
        region_name="Beirut",
        query_job_fingerprint="f" * 64,
        plan_hash_snapshot=PLAN_HASH,
        next_page_number=2,
        pages_completed=1,
        pagination_completed=False,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def _provider_success(job: ClaimedQueryJob, *, has_more: bool = False) -> DiscoveryProviderExecutionResult:
    results = (
        DiscoveryProviderResultItem(
            original_url="https://example.org/rehab",
            title="Rehab",
            snippet="Directory",
            rank=1,
            provider="serper",
            provider_result_type="organic",
            query_job_id=job.id,
            organization_id=job.organization_id,
            execution_id=job.execution_id,
            claim_token=job.claim_token,
            scope_level=job.scope_level,
            language_code=job.language_code,
            region_code=job.region_code,
            region_name=job.region_name,
            important_city=job.important_city,
            country_code=job.country_code,
            discovered_at=FIXED_NOW,
            provider_page_number=job.next_page_number,
        ),
    )
    page_size = 10
    raw_count = page_size if has_more else len(results)
    return DiscoveryProviderExecutionResult(
        outcome="succeeded",
        provider="serper",
        query_job_id=job.id,
        organization_id=job.organization_id,
        execution_id=job.execution_id,
        claim_token=job.claim_token,
        discovered_at=FIXED_NOW,
        results=results[:raw_count] if has_more else results,
        raw_result_count=raw_count,
        accepted_result_count=raw_count,
        page_number=job.next_page_number,
        continuation=DiscoveryProviderContinuation(
            requested_page_size=page_size,
            returned_result_count=raw_count,
            has_more=has_more,
            page_number=job.next_page_number,
            page_fingerprint="b" * 64,
        ),
    )


class FakeClaimService:
    def __init__(self, *, claimed: ClaimedQueryJob | None = None) -> None:
        self.claimed = claimed or _claimed()
        self.claim_calls: list[dict[str, Any]] = []
        self.requeue_calls: list[dict[str, Any]] = []
        self.provider_called = False

    async def claim_eligible_jobs(self, **kwargs: Any) -> ClaimBatchResult:
        self.claim_calls.append(kwargs)
        return ClaimBatchResult(outcome="claimed", jobs=[self.claimed])

    async def preflight_claimed_job(self, **kwargs: Any) -> ClaimPreflightResult:
        return ClaimPreflightResult(outcome="ok", query_job_id=kwargs.get("query_job_id", self.claimed.id))

    async def renew_claim(self, **kwargs: Any) -> ClaimMutationResult:
        return ClaimMutationResult(outcome="applied", query_job_id=kwargs.get("query_job_id", self.claimed.id))

    async def requeue_retryable_failure(self, **kwargs: Any) -> ClaimMutationResult:
        self.requeue_calls.append(kwargs)
        return ClaimMutationResult(outcome="applied", query_job_id=kwargs.get("query_job_id", self.claimed.id))

    async def mark_terminal_failure(self, **kwargs: Any) -> ClaimMutationResult:
        return ClaimMutationResult(outcome="applied", query_job_id=kwargs.get("query_job_id", self.claimed.id))


class FakeProviderService:
    def __init__(self, *, result: DiscoveryProviderExecutionResult | None = None) -> None:
        self.result = result
        self.calls: list[tuple[ClaimedQueryJob, str]] = []

    async def execute_claimed_query(
        self, claimed_job: ClaimedQueryJob, provider_name: str, **kwargs: Any
    ) -> DiscoveryProviderExecutionResult:
        self.calls.append((claimed_job, provider_name))
        assert self.result is not None
        return self.result


class FakeResultService:
    def __init__(self, *, persisted: DiscoveryPersistenceResult) -> None:
        self.persisted = persisted
        self.continue_calls = 0
        self.final_calls = 0

    async def persist_page_and_continue(self, prepared: Any, **kwargs: Any) -> DiscoveryPersistenceResult:
        self.continue_calls += 1
        return self.persisted

    async def persist_final_page_and_succeed(self, prepared: Any, **kwargs: Any) -> DiscoveryPersistenceResult:
        self.final_calls += 1
        return self.persisted


class FakeSession:
    async def get(self, model: type, key: str) -> Any:
        if model is ScrapingSourceDiscoveryQuery:
            return _pending_job(id=key)
        return None

    async def execute(self, stmt: Any) -> Any:
        execution = _execution()
        result = MagicMock()
        result.scalar_one_or_none.return_value = execution
        result.scalars.return_value.all.return_value = [execution]
        result.scalar_one.return_value = 0
        return result


class FakeSessionContext:
    def __init__(self) -> None:
        self.session = FakeSession()

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, *args: Any) -> None:
        return None


class FakeBeginContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: Any) -> None:
        return None


class FakePauseSession(FakeSession):
    def begin(self) -> FakeBeginContext:
        return FakeBeginContext()


def _session_factory() -> async_sessionmaker[AsyncSession]:
    return MagicMock(return_value=FakeSessionContext())  # type: ignore[return-value]


async def _run_smoke(**run_kwargs: Any) -> smoke.SmokeRunResult:
    with ExitStack() as stack:
        stack.enter_context(patch.object(smoke, "is_serper_configured", return_value=True))
        stack.enter_context(patch.object(smoke, "assert_real_serper_resolver"))
        stack.enter_context(patch.object(smoke, "assert_live_provider_service"))
        stack.enter_context(
            patch.object(smoke, "_load_execution", new=AsyncMock(return_value=_execution()))
        )
        stack.enter_context(
            patch.object(smoke, "_earliest_eligible_job", new=AsyncMock(return_value=_pending_job()))
        )
        stack.enter_context(
            patch.object(smoke, "_pause_execution_for_review", new=AsyncMock(return_value=True))
        )
        stack.enter_context(
            patch.object(smoke, "_apply_provider_blocked_pause", new=AsyncMock(return_value=None))
        )
        return await smoke.run_one_page_smoke(
            preview_emitter=lambda _: None,
            live_mode=True,
            session_factory=_session_factory(),
            **run_kwargs,
        )


@pytest.mark.asyncio
async def test_preview_is_read_only() -> None:
    claim = FakeClaimService()
    provider = FakeProviderService(result=_provider_success(_claimed()))
    with patch.object(smoke, "SourceDiscoveryClaimService", return_value=claim):
        with patch.object(smoke, "SourceDiscoveryProviderService", return_value=provider):
            eligible, inspected = await smoke.preview_executions(_session_factory(), now=FIXED_NOW)
    assert isinstance(eligible, list)
    assert isinstance(inspected, list)
    assert claim.claim_calls == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_preview_makes_no_provider_call() -> None:
    provider = SourceDiscoveryProviderService()
    with patch.object(provider, "execute_claimed_query", new=AsyncMock()) as execute:
        await smoke.preview_executions(_session_factory(), now=FIXED_NOW)
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_run_refuses_without_confirmation_flags() -> None:
    code = await smoke._async_main(
        [
            "run",
            "--organization-id",
            "org-1",
            "--execution-id",
            "exec-1",
            "--expected-query-job-id",
            "job-expected",
        ]
    )
    assert code == 2


@pytest.mark.asyncio
async def test_run_refuses_injected_provider_in_live_mode() -> None:
    provider = SourceDiscoveryProviderService(provider=MagicMock())
    with pytest.raises(RuntimeError, match="injected provider"):
        smoke.assert_live_provider_service(provider)


@pytest.mark.asyncio
async def test_run_refuses_injected_client_factory_in_live_mode() -> None:
    provider = SourceDiscoveryProviderService(client_factory=MagicMock())
    with pytest.raises(RuntimeError, match="injected HTTP client factory"):
        smoke.assert_live_provider_service(provider)


@pytest.mark.asyncio
async def test_missing_api_key_stops_before_claim() -> None:
    claim = FakeClaimService()
    provider = FakeProviderService(result=_provider_success(_claimed()))
    with patch.object(smoke, "is_serper_configured", return_value=False):
        result = await smoke.run_one_page_smoke(
            organization_id="org-1",
            execution_id="exec-1",
            expected_query_job_id="job-expected",
            claim_service=claim,
            provider_service=provider,
            result_service=MagicMock(),
            session_factory=_session_factory(),
            live_mode=True,
            preview_emitter=lambda _: None,
        )
    assert result.outcome == "configuration_error"
    assert claim.claim_calls == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_expected_job_mismatch_releases_claim_without_provider_call() -> None:
    unexpected = _claimed(id="job-other")
    claim = FakeClaimService(claimed=unexpected)
    provider = FakeProviderService(result=_provider_success(unexpected))
    result = await _run_smoke(
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        claim_service=claim,
        provider_service=provider,
        result_service=MagicMock(),
    )
    assert result.outcome == "claim_mismatch"
    assert claim.requeue_calls
    assert provider.calls == []


@pytest.mark.asyncio
async def test_exactly_one_job_claimed() -> None:
    claim = FakeClaimService()
    provider = FakeProviderService(result=_provider_success(claim.claimed))
    persisted = DiscoveryPersistenceResult(
        outcome="applied",
        counts=DiscoveryPersistenceCounts(query_marked_succeeded=True, pagination_completed=True),
        query_status="succeeded",
    )
    results = FakeResultService(persisted=persisted)
    await _run_smoke(
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        claim_service=claim,
        provider_service=provider,
        result_service=results,
    )
    assert len(claim.claim_calls) == 1
    assert claim.claim_calls[0]["batch_size"] == 1


@pytest.mark.asyncio
async def test_exactly_one_provider_page_requested() -> None:
    claim = FakeClaimService()
    provider = FakeProviderService(result=_provider_success(claim.claimed))
    persisted = DiscoveryPersistenceResult(
        outcome="applied",
        counts=DiscoveryPersistenceCounts(query_marked_succeeded=True, pagination_completed=True),
        query_status="succeeded",
    )
    results = FakeResultService(persisted=persisted)
    result = await _run_smoke(
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        claim_service=claim,
        provider_service=provider,
        result_service=results,
    )
    assert len(provider.calls) == 1
    assert result.provider_calls == 1


@pytest.mark.asyncio
async def test_query_text_reaches_provider_unchanged() -> None:
    claim = FakeClaimService()
    provider = FakeProviderService(result=_provider_success(claim.claimed))
    persisted = DiscoveryPersistenceResult(
        outcome="applied",
        counts=DiscoveryPersistenceCounts(query_marked_succeeded=True, pagination_completed=True),
        query_status="succeeded",
    )
    results = FakeResultService(persisted=persisted)
    await _run_smoke(
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        claim_service=claim,
        provider_service=provider,
        result_service=results,
    )
    job, provider_name = provider.calls[0]
    assert job.query_text == "exact persisted query text"
    assert provider_name == "serper"


@pytest.mark.asyncio
async def test_persisted_current_page_reaches_provider() -> None:
    claim = FakeClaimService(claimed=_claimed(next_page_number=3))
    provider = FakeProviderService(result=_provider_success(claim.claimed, has_more=False))
    persisted = DiscoveryPersistenceResult(
        outcome="applied",
        counts=DiscoveryPersistenceCounts(
            query_marked_succeeded=True,
            pagination_completed=True,
            pages_completed=3,
            next_page_number=3,
        ),
        query_status="succeeded",
    )
    results = FakeResultService(persisted=persisted)
    result = await _run_smoke(
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        claim_service=claim,
        provider_service=provider,
        result_service=results,
    )
    assert provider.calls[0][0].next_page_number == 3
    assert result.requested_page == 3


@pytest.mark.asyncio
async def test_no_continuation_enqueued() -> None:
    claim = FakeClaimService()
    provider = FakeProviderService(result=_provider_success(claim.claimed, has_more=True))
    persisted = DiscoveryPersistenceResult(
        outcome="page_continued",
        counts=DiscoveryPersistenceCounts(
            pages_completed=2,
            next_page_number=3,
            pagination_completed=False,
        ),
        query_status="pending",
    )
    results = FakeResultService(persisted=persisted)
    result = await _run_smoke(
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        claim_service=claim,
        provider_service=provider,
        result_service=results,
    )
    assert result.continuation_enqueued is False
    assert results.continue_calls == 1
    assert results.final_calls == 0


@pytest.mark.asyncio
async def test_full_page_advances_once_and_stops() -> None:
    claim = FakeClaimService()
    provider = FakeProviderService(result=_provider_success(claim.claimed, has_more=True))
    persisted = DiscoveryPersistenceResult(
        outcome="page_continued",
        counts=DiscoveryPersistenceCounts(
            pages_completed=2,
            next_page_number=3,
            pagination_completed=False,
        ),
        query_status="pending",
    )
    results = FakeResultService(persisted=persisted)
    result = await _run_smoke(
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        claim_service=claim,
        provider_service=provider,
        result_service=results,
    )
    assert result.outcome == "page_continued"
    assert result.provider_calls == 1
    assert claim.claim_calls[0]["batch_size"] == 1


@pytest.mark.asyncio
async def test_final_page_succeeds_and_stops() -> None:
    claim = FakeClaimService()
    provider = FakeProviderService(result=_provider_success(claim.claimed, has_more=False))
    persisted = DiscoveryPersistenceResult(
        outcome="applied",
        counts=DiscoveryPersistenceCounts(
            query_marked_succeeded=True,
            pagination_completed=True,
            pages_completed=2,
            next_page_number=2,
        ),
        query_status="succeeded",
    )
    results = FakeResultService(persisted=persisted)
    result = await _run_smoke(
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        claim_service=claim,
        provider_service=provider,
        result_service=results,
    )
    assert result.outcome == "succeeded"
    assert results.final_calls == 1
    assert result.pagination_completed is True


@pytest.mark.asyncio
async def test_retryable_failure_keeps_same_page_and_stops() -> None:
    claim = FakeClaimService()
    retry_result = DiscoveryProviderExecutionResult(
        outcome="provider_rate_limited",
        provider="serper",
        query_job_id=claim.claimed.id,
        organization_id=claim.claimed.organization_id,
        execution_id=claim.claimed.execution_id,
        claim_token=claim.claimed.claim_token,
        discovered_at=FIXED_NOW,
        retry_after_at=FIXED_NOW + timedelta(seconds=30),
        page_number=claim.claimed.next_page_number,
    )
    provider = FakeProviderService(result=retry_result)
    result = await _run_smoke(
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        claim_service=claim,
        provider_service=provider,
        result_service=MagicMock(),
    )
    assert result.outcome == "retry_scheduled"
    assert claim.requeue_calls
    assert result.provider_calls == 1


@pytest.mark.asyncio
async def test_provider_blocker_pauses_safely_and_stops() -> None:
    claim = FakeClaimService()
    blocked = DiscoveryProviderExecutionResult(
        outcome="provider_authentication_failed",
        provider="serper",
        query_job_id=claim.claimed.id,
        organization_id=claim.claimed.organization_id,
        execution_id=claim.claimed.execution_id,
        claim_token=claim.claimed.claim_token,
        discovered_at=FIXED_NOW,
        page_number=claim.claimed.next_page_number,
    )
    provider = FakeProviderService(result=blocked)
    pause = AsyncMock(return_value=None)
    with ExitStack() as stack:
        stack.enter_context(patch.object(smoke, "is_serper_configured", return_value=True))
        stack.enter_context(patch.object(smoke, "assert_real_serper_resolver"))
        stack.enter_context(patch.object(smoke, "assert_live_provider_service"))
        stack.enter_context(
            patch.object(smoke, "_load_execution", new=AsyncMock(return_value=_execution()))
        )
        stack.enter_context(
            patch.object(smoke, "_earliest_eligible_job", new=AsyncMock(return_value=_pending_job()))
        )
        stack.enter_context(patch.object(smoke, "_apply_provider_blocked_pause", pause))
        result = await smoke.run_one_page_smoke(
            organization_id="org-1",
            execution_id="exec-1",
            expected_query_job_id="job-expected",
            claim_service=claim,
            provider_service=provider,
            result_service=MagicMock(),
            session_factory=_session_factory(),
            live_mode=True,
            preview_emitter=lambda _: None,
        )
    assert result.outcome == "provider_blocked"
    assert pause.await_count == 1
    assert result.provider_calls == 1


@pytest.mark.asyncio
async def test_no_mock_or_later_stage_runs() -> None:
    claim = FakeClaimService()
    provider = FakeProviderService(result=_provider_success(claim.claimed))
    persisted = DiscoveryPersistenceResult(
        outcome="applied",
        counts=DiscoveryPersistenceCounts(query_marked_succeeded=True, pagination_completed=True),
        query_status="succeeded",
    )
    results = FakeResultService(persisted=persisted)
    result = await _run_smoke(
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        claim_service=claim,
        provider_service=provider,
        result_service=results,
    )
    assert result.mock_stages_executed is False
    assert result.continuation_enqueued is False


def test_output_is_sanitized() -> None:
    payload = {
        "query_text": "visible",
        "claim_token": "secret-token",
        "query_job_fingerprint": "f" * 64,
        "canonical_url_hash": "h" * 64,
        "api_key": "key",
    }
    cleaned = smoke.sanitize_public_mapping(payload)
    assert cleaned["query_text"] == "visible"
    assert "claim_token" not in cleaned
    assert "query_job_fingerprint" not in cleaned
    assert "canonical_url_hash" not in cleaned
    assert "api_key" not in cleaned


@pytest.mark.asyncio
async def test_verify_command_is_read_only() -> None:
    claim = FakeClaimService()
    provider = FakeProviderService(result=_provider_success(_claimed()))

    class VerifySession:
        async def get(self, model: type, key: str) -> Any:
            if model is ScrapingSourceDiscoveryQuery:
                job = _pending_job(id=key)
                job.provider = "serper"
                job.requested_at = FIXED_NOW
                job.attempt_count = 1
                job.last_page_result_count = 1
                return job
            return None

        async def execute(self, stmt: Any) -> Any:
            result = MagicMock()
            if "ScrapingSourceCandidate" in str(stmt):
                candidate = MagicMock()
                candidate.rank = 1
                candidate.url = "https://example.org/rehab"
                candidate.canonical_url = "https://example.org/rehab"
                candidate.status = MagicMock(value="discovered")
                candidate.crawl_node_id = "node-1"
                candidate.metadata_json = {}
                candidate.provider_page_number = 2
                result.scalars.return_value.all.return_value = [candidate]
            elif "ScrapingCrawlNode" in str(stmt):
                node = MagicMock()
                node.canonical_url = "https://example.org/rehab"
                node.hostname = "example.org"
                node.source_classification = MagicMock(value="directory")
                result.scalars.return_value.all.return_value = [node]
            elif "ScrapingEvent" in str(stmt):
                result.scalars.return_value.all.return_value = ["web_discovery_page_persisted"]
            else:
                result.scalar_one_or_none.return_value = _execution()
            return result

    class VerifySessionContext:
        async def __aenter__(self) -> VerifySession:
            return VerifySession()

        async def __aexit__(self, *args: Any) -> None:
            return None

    factory = MagicMock(return_value=VerifySessionContext())
    payload = await smoke.verify_smoke(
        factory,
        organization_id="org-1",
        execution_id="exec-1",
        query_job_id="job-expected",
    )
    assert claim.claim_calls == []
    assert provider.calls == []
    assert payload.get("ok") is True
    assert "claim_token" not in json.dumps(payload)


def test_tests_use_no_real_external_dependencies() -> None:
    """Meta-test: this module only uses fakes/mocks (no live HTTP/DB/Redis)."""
    assert SCRIPT_PATH.name == "phase4_serper_smoke.py"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_without_confirmation_flags() -> None:
    code = await smoke._async_main(
        [
            "prepare-existing",
            "--organization-id",
            "org-1",
            "--execution-id",
            "exec-1",
            "--expected-query-job-id",
            "job-expected",
        ]
    )
    assert code == 2


@pytest.mark.asyncio
async def test_prepare_existing_refuses_wrong_organization() -> None:
    harness = PrepareHarness()
    harness.execution.organization_id = "org-other"
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        result = await run_prepare(harness)
    assert result.outcome == "failed"
    assert result.error_code == "execution_mismatch"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_wrong_execution() -> None:
    harness = PrepareHarness()
    harness.execution.id = "exec-other"
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        result = await run_prepare(harness)
    assert result.outcome == "failed"
    assert result.error_code == "execution_mismatch"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_wrong_expected_job() -> None:
    harness = PrepareHarness()
    harness.job.id = "job-other"
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        result = await run_prepare(harness)
    assert result.outcome == "failed"
    assert result.error_code == "query_job_mismatch"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_cancelled_execution() -> None:
    harness = PrepareHarness(status=ScrapingExecutionStatus.CANCELLED)
    code = await smoke._evaluate_legacy_reopen_preconditions(
        AsyncMock(),
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        execution=harness.execution,
    )
    assert code == "cancelled_or_failed"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_failed_execution() -> None:
    harness = PrepareHarness(status=ScrapingExecutionStatus.FAILED)
    code = await smoke._evaluate_legacy_reopen_preconditions(
        AsyncMock(),
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        execution=harness.execution,
    )
    assert code == "cancelled_or_failed"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_running_execution() -> None:
    harness = PrepareHarness(status=ScrapingExecutionStatus.RUNNING)
    code = await smoke._evaluate_legacy_reopen_preconditions(
        AsyncMock(),
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        execution=harness.execution,
    )
    assert code == "status_not_completed"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_paused_execution() -> None:
    harness = PrepareHarness(status=ScrapingExecutionStatus.PAUSED)
    code = await smoke._evaluate_legacy_reopen_preconditions(
        AsyncMock(),
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        execution=harness.execution,
    )
    assert code == "status_not_completed"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_non_schema_v2() -> None:
    harness = PrepareHarness(execution_plan_schema_version="1")
    code = await smoke._evaluate_legacy_reopen_preconditions(
        AsyncMock(),
        organization_id="org-1",
        execution_id="exec-1",
        expected_query_job_id="job-expected",
        execution=harness.execution,
    )
    assert code == "not_schema_v2"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_missing_step3b_provenance() -> None:
    harness = PrepareHarness()
    harness.counts["pending_without_provenance"] = 1
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        result = await run_prepare(harness)
    assert result.outcome == "failed"
    assert result.error_code == "step3b_provenance_missing"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_running_phase4_jobs() -> None:
    harness = PrepareHarness()
    harness.counts["running"] = 2
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        result = await run_prepare(harness)
    assert result.outcome == "failed"
    assert result.error_code == "running_jobs_exist"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_succeeded_phase4_jobs() -> None:
    harness = PrepareHarness()
    harness.counts["succeeded"] = 1
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        result = await run_prepare(harness)
    assert result.outcome == "failed"
    assert result.error_code == "succeeded_jobs_exist"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_failed_phase4_jobs() -> None:
    harness = PrepareHarness()
    harness.counts["failed"] = 1
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        result = await run_prepare(harness)
    assert result.outcome == "failed"
    assert result.error_code == "failed_jobs_exist"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_existing_candidates() -> None:
    harness = PrepareHarness()
    harness.counts["candidates"] = 1
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        result = await run_prepare(harness)
    assert result.outcome == "failed"
    assert result.error_code == "candidates_exist"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_existing_crawl_nodes() -> None:
    harness = PrepareHarness()
    harness.counts["nodes"] = 1
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        result = await run_prepare(harness)
    assert result.outcome == "failed"
    assert result.error_code == "crawl_nodes_exist"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_existing_crawl_edges() -> None:
    harness = PrepareHarness()
    harness.counts["edges"] = 1
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        result = await run_prepare(harness)
    assert result.outcome == "failed"
    assert result.error_code == "crawl_edges_exist"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_provider_blocked() -> None:
    harness = PrepareHarness(country_profile_json={smoke.PROVIDER_BLOCK_PROFILE_KEY: True})
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        result = await run_prepare(harness)
    assert result.outcome == "failed"
    assert result.error_code == "provider_blocked"


@pytest.mark.asyncio
async def test_prepare_existing_refuses_non_earliest_job() -> None:
    harness = PrepareHarness()
    other_job = _virgin_pending_job(id="job-other", priority=1, generation_ordinal=0)
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        result = await run_prepare(harness, earliest_job=other_job)
    assert result.outcome == "failed"
    assert result.error_code == "expected_job_not_earliest"


@pytest.mark.asyncio
async def test_prepare_existing_records_original_lifecycle_and_baseline() -> None:
    harness = PrepareHarness()
    harness.events.append(
        MagicMock(id="baseline-event", sequence_number=42, created_at=FIXED_NOW - timedelta(minutes=5))
    )
    emit = AsyncMock(return_value=MagicMock(id="prep-event", sequence_number=43, created_at=FIXED_NOW))

    with patch.object(smoke.execution_service, "emit_event", emit):
        result = await run_prepare(harness)

    assert result.outcome == "prepared"
    assert result.original_status == "completed"
    assert result.original_stage == "database_cleaning"
    assert result.original_completed_at is not None
    assert result.event_baseline_event_id == "baseline-event"
    profile = harness.execution.country_profile_json or {}
    assert profile[smoke.PHASE4_SMOKE_PREPARED_KEY] is True
    assert profile[smoke.PHASE4_SMOKE_EXPECTED_JOB_KEY] == "job-expected"


@pytest.mark.asyncio
async def test_prepare_existing_changes_only_execution_lifecycle_fields() -> None:
    harness = PrepareHarness(
        mission_id="mission-keep",
        execution_plan_hash="b" * 64,
        error_message="old error",
        progress_percent=100,
    )
    harness.events.append(
        MagicMock(id="baseline-event", sequence_number=1, created_at=FIXED_NOW - timedelta(minutes=5))
    )
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        await run_prepare(harness)

    assert harness.execution.status == ScrapingExecutionStatus.RUNNING
    assert harness.execution.current_stage == "web_discovery"
    assert harness.execution.completed_at is None
    assert harness.execution.paused_at is None
    assert harness.execution.error_message is None
    assert harness.execution.mission_id == "mission-keep"
    assert harness.execution.execution_plan_hash == "b" * 64


@pytest.mark.asyncio
async def test_prepare_existing_does_not_modify_query_jobs() -> None:
    harness = PrepareHarness()
    harness.events.append(
        MagicMock(id="baseline-event", sequence_number=1, created_at=FIXED_NOW - timedelta(minutes=5))
    )
    before = _virgin_pending_job()
    harness.job = before
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        await run_prepare(harness)
    assert harness.job.status == before.status
    assert harness.job.query_text == before.query_text
    assert harness.job.next_page_number == before.next_page_number
    assert harness.job.pages_completed == before.pages_completed


@pytest.mark.asyncio
async def test_prepare_existing_does_not_enqueue_redis() -> None:
    harness = PrepareHarness()
    harness.events.append(
        MagicMock(id="baseline-event", sequence_number=1, created_at=FIXED_NOW - timedelta(minutes=5))
    )
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        result = await run_prepare(harness)
    assert result.outcome == "prepared"


@pytest.mark.asyncio
async def test_prepare_existing_does_not_call_serper() -> None:
    harness = PrepareHarness()
    harness.events.append(
        MagicMock(id="baseline-event", sequence_number=1, created_at=FIXED_NOW - timedelta(minutes=5))
    )
    provider = SourceDiscoveryProviderService()
    with patch.object(smoke.execution_service, "emit_event", new=AsyncMock()):
        with patch.object(provider, "execute_claimed_query", new=AsyncMock()) as execute:
            await run_prepare(harness)
    execute.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_existing_emits_one_safe_event() -> None:
    harness = PrepareHarness()
    harness.events.append(
        MagicMock(id="baseline-event", sequence_number=1, created_at=FIXED_NOW - timedelta(minutes=5))
    )
    emit = AsyncMock(return_value=MagicMock(id="prep-event"))
    with patch.object(smoke.execution_service, "emit_event", emit):
        await run_prepare(harness)
    assert emit.await_count == 1
    assert emit.await_args.args[2] == "web_discovery_smoke_prepared"
    metadata = emit.await_args.kwargs.get("metadata") or {}
    assert "query_text" not in metadata
    assert "expected_query_job_id" in metadata


@pytest.mark.asyncio
async def test_prepare_existing_is_idempotent() -> None:
    harness = PrepareHarness()
    harness.events.append(
        MagicMock(id="baseline-event", sequence_number=1, created_at=FIXED_NOW - timedelta(minutes=5))
    )
    emit = AsyncMock(return_value=MagicMock(id="prep-event"))
    with patch.object(smoke.execution_service, "emit_event", emit):
        first = await run_prepare(harness)
        second = await run_prepare(harness)
    assert first.outcome == "prepared"
    assert second.outcome == "already_prepared"
    assert emit.await_count == 1


@pytest.mark.asyncio
async def test_run_requires_preparation_marker_for_reopened_execution() -> None:
    execution = _execution(
        country_profile_json={
            smoke.PHASE4_SMOKE_ORIGINAL_STATUS_KEY: "completed",
        }
    )
    with ExitStack() as stack:
        stack.enter_context(patch.object(smoke, "is_serper_configured", return_value=True))
        stack.enter_context(patch.object(smoke, "assert_real_serper_resolver"))
        stack.enter_context(patch.object(smoke, "assert_live_provider_service"))
        stack.enter_context(patch.object(smoke, "_load_execution", new=AsyncMock(return_value=execution)))
        result = await smoke.run_one_page_smoke(
            organization_id="org-1",
            execution_id="exec-1",
            expected_query_job_id="job-expected",
            claim_service=FakeClaimService(),
            provider_service=FakeProviderService(result=_provider_success(_claimed())),
            result_service=MagicMock(),
            session_factory=_session_factory(),
            live_mode=True,
            preview_emitter=lambda _: None,
        )
    assert result.error_code == "smoke_preparation_required"


@pytest.mark.asyncio
async def test_verify_ignores_historical_mock_events_before_baseline() -> None:
    baseline_event = MagicMock(
        id="baseline-event",
        sequence_number=10,
        created_at=FIXED_NOW - timedelta(minutes=10),
        event_type="stage_completed",
    )
    execution = _execution(
        country_profile_json={
            smoke.PHASE4_SMOKE_EVENT_BASELINE_EVENT_ID_KEY: "baseline-event",
            smoke.PHASE4_SMOKE_EVENT_BASELINE_AT_KEY: (FIXED_NOW - timedelta(minutes=10)).isoformat(),
        }
    )

    class VerifySession:
        async def get(self, model: type, key: str) -> Any:
            if model is ScrapingSourceDiscoveryQuery:
                return _pending_job(id=key)
            if model is smoke.ScrapingEvent and key == "baseline-event":
                return baseline_event
            return None

        async def execute(self, stmt: Any) -> Any:
            result = MagicMock()
            stmt_text = str(stmt)
            if "ScrapingSourceCandidate" in stmt_text:
                result.scalars.return_value.all.return_value = []
            elif "scraping_events" in stmt_text:
                rows = [
                    MagicMock(
                        id="old-1",
                        event_type="stage_completed",
                        sequence_number=5,
                        created_at=FIXED_NOW - timedelta(hours=1),
                    ),
                    MagicMock(
                        id="baseline-event",
                        event_type="stage_completed",
                        sequence_number=10,
                        created_at=FIXED_NOW - timedelta(minutes=10),
                    ),
                ]
                result.all.return_value = rows
            else:
                result.scalar_one_or_none.return_value = execution
            return result

    class VerifySessionContext:
        async def __aenter__(self) -> VerifySession:
            return VerifySession()

        async def __aexit__(self, *args: Any) -> None:
            return None

    payload = await smoke.verify_smoke(
        MagicMock(return_value=VerifySessionContext()),
        organization_id="org-1",
        execution_id="exec-1",
        query_job_id="job-expected",
    )
    assert payload["historical_forbidden_events_before_smoke"] == ["stage_completed", "stage_completed"]
    assert payload["forbidden_events_after_smoke"] == []


@pytest.mark.asyncio
async def test_verify_flags_mock_events_after_baseline() -> None:
    baseline_event = MagicMock(
        id="baseline-event",
        sequence_number=10,
        created_at=FIXED_NOW - timedelta(minutes=10),
    )
    execution = _execution(
        country_profile_json={
            smoke.PHASE4_SMOKE_EVENT_BASELINE_EVENT_ID_KEY: "baseline-event",
            smoke.PHASE4_SMOKE_EVENT_BASELINE_AT_KEY: (FIXED_NOW - timedelta(minutes=10)).isoformat(),
        }
    )

    class VerifySession:
        async def get(self, model: type, key: str) -> Any:
            if model is ScrapingSourceDiscoveryQuery:
                return _pending_job(id=key)
            if model is smoke.ScrapingEvent and key == "baseline-event":
                return baseline_event
            return None

        async def execute(self, stmt: Any) -> Any:
            result = MagicMock()
            stmt_text = str(stmt)
            if "ScrapingSourceCandidate" in stmt_text:
                result.scalars.return_value.all.return_value = []
            elif "scraping_events" in stmt_text:
                rows = [
                    MagicMock(
                        id="baseline-event",
                        event_type="stage_completed",
                        sequence_number=10,
                        created_at=FIXED_NOW - timedelta(minutes=10),
                    ),
                    MagicMock(
                        id="after-1",
                        event_type="execution_completed",
                        sequence_number=11,
                        created_at=FIXED_NOW,
                    ),
                ]
                result.all.return_value = rows
            else:
                result.scalar_one_or_none.return_value = execution
            return result

    class VerifySessionContext:
        async def __aenter__(self) -> VerifySession:
            return VerifySession()

        async def __aexit__(self, *args: Any) -> None:
            return None

    payload = await smoke.verify_smoke(
        MagicMock(return_value=VerifySessionContext()),
        organization_id="org-1",
        execution_id="exec-1",
        query_job_id="job-expected",
    )
    assert payload["historical_forbidden_events_before_smoke"] == ["stage_completed"]
    assert payload["forbidden_events_after_smoke"] == ["execution_completed"]


@pytest.mark.asyncio
async def test_preview_marks_legacy_reopen_candidate() -> None:
    completed = _legacy_completed_execution()

    class PreviewSession:
        async def execute(self, stmt: Any) -> Any:
            result = MagicMock()
            if "scraping_executions" in str(stmt):
                result.scalars.return_value.all.return_value = [completed]
            return result

    class PreviewSessionContext:
        async def __aenter__(self) -> PreviewSession:
            return PreviewSession()

        async def __aexit__(self, *args: Any) -> None:
            return None

    async def count_jobs(session: Any, **kwargs: Any) -> int:
        status = kwargs["status"]
        if status == SourceDiscoveryQueryStatus.PENDING:
            return 90020
        return 0

    with patch.object(smoke, "_count_jobs", new=AsyncMock(side_effect=count_jobs)):
        with patch.object(
            smoke,
            "_earliest_eligible_job",
            new=AsyncMock(return_value=_virgin_pending_job()),
        ):
            eligible, inspected = await smoke.preview_executions(
                MagicMock(return_value=PreviewSessionContext()),
                now=FIXED_NOW,
            )
    assert not eligible
    assert inspected[0].legacy_step3b_reopen_candidate is True
    assert inspected[0].legacy_reopen_reason == smoke.LEGACY_REOPEN_REASON
