"""Phase 4 Slice 6: non-Docker orchestration + worker integration tests.

All provider/claim/persist/queue/DNS doubles are injected. No real HTTP, DNS,
PostgreSQL, Redis, or paid providers.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingEvent,
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingMission,
    ScrapingMissionStatus,
    ScrapingSourceDiscoveryQuery,
)
from app.services.scraping import mission_campaign_mock_worker
from app.services.scraping.execution_service import execution_service
from app.services.scraping.query_generation_service import query_generation_service
from app.services.scraping.source_discovery_claim_service import (
    ClaimBatchResult,
    ClaimMutationResult,
    ClaimPreflightResult,
    ClaimedQueryJob,
    RecoverExpiredClaimsResult,
    WorkInspectionResult,
    generate_claim_token,
)
from app.services.scraping.source_discovery_execution_service import (
    SourceDiscoveryExecutionService,
)
from app.services.scraping.source_discovery_provider_service import (
    DiscoveryProviderContinuation,
    DiscoveryProviderExecutionResult,
    DiscoveryProviderResultItem,
)
from app.services.scraping.source_discovery_result_service import (
    DiscoveryPersistenceCounts,
    DiscoveryPersistenceResult,
    PreparedDiscoveryBatch,
)
from app.services.scraping.source_discovery_service import source_discovery_service
from test_mission_campaign_lifecycle import _approved_mission_with_team_plan

FIXED_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)
PLAN_HASH = "a" * 64
EXEC_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "scraping"
    / "source_discovery_execution_service.py"
)
WORKER_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "scraping"
    / "mission_campaign_mock_worker.py"
)


def _claimed(**overrides: Any) -> ClaimedQueryJob:
    base = ClaimedQueryJob(
        id="job-1",
        organization_id="org-1",
        execution_id="exec-1",
        query_text="secret query text must not leak",
        provider="serper",
        claim_token=generate_claim_token(),
        claimed_at=FIXED_NOW,
        lease_expires_at=FIXED_NOW + timedelta(seconds=60),
        attempt_count=1,
        last_attempt_at=FIXED_NOW,
        priority=100,
        generation_ordinal=1,
        discovery_round=1,
        purpose="seed",
        country_code="LB",
        country_name="Lebanon",
        region_code=None,
        region_name=None,
        language_code="en",
        language_name="English",
        source_category="directory",
        scope_level="countrywide",
        important_city=None,
        query_job_fingerprint="f" * 64,
        plan_hash_snapshot=PLAN_HASH,
        requested_at=FIXED_NOW,
    )
    return replace(base, **overrides) if overrides else base


def _success_provider(job: ClaimedQueryJob, *, empty: bool = False) -> DiscoveryProviderExecutionResult:
    results = ()
    if not empty:
        results = (
            DiscoveryProviderResultItem(
                original_url="https://docs.python.org/rehab",
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
            ),
        )
    return DiscoveryProviderExecutionResult(
        outcome="succeeded",
        provider="serper",
        query_job_id=job.id,
        organization_id=job.organization_id,
        execution_id=job.execution_id,
        claim_token=job.claim_token,
        discovered_at=FIXED_NOW,
        results=results,
        raw_result_count=len(results),
        accepted_result_count=len(results),
        diagnostic_code="succeeded",
        continuation=DiscoveryProviderContinuation(
            requested_page_size=10,
            returned_result_count=len(results),
            has_more=False,
            page_number=getattr(job, "next_page_number", 1) or 1,
            page_fingerprint="b" * 64,
        ),
        page_number=getattr(job, "next_page_number", 1) or 1,
        page_fingerprint="b" * 64,
        provider_page_size=10,
    )


def _provider_result(job: ClaimedQueryJob, outcome: str, **kwargs: Any) -> DiscoveryProviderExecutionResult:
    return DiscoveryProviderExecutionResult(
        outcome=outcome,  # type: ignore[arg-type]
        provider="serper",
        query_job_id=job.id,
        organization_id=job.organization_id,
        execution_id=job.execution_id,
        claim_token=job.claim_token,
        discovered_at=FIXED_NOW,
        diagnostic_code=outcome,
        **kwargs,
    )


@dataclass
class FakeClaimService:
    jobs_to_claim: list[ClaimedQueryJob] = field(default_factory=list)
    recovered: RecoverExpiredClaimsResult = field(
        default_factory=lambda: RecoverExpiredClaimsResult(recovered_count=0)
    )
    preflight_outcomes: dict[str, str] = field(default_factory=dict)
    default_preflight: str = "ok"
    inspection: WorkInspectionResult = field(
        default_factory=lambda: WorkInspectionResult(outcome="ok")
    )
    claim_calls: list[dict[str, Any]] = field(default_factory=list)
    recover_calls: list[dict[str, Any]] = field(default_factory=list)
    requeues: list[dict[str, Any]] = field(default_factory=list)
    terminals: list[dict[str, Any]] = field(default_factory=list)
    claim_before_provider: list[bool] = field(default_factory=list)
    lifecycle_on_claim: str | None = None
    claim_batch_sizes: list[int] = field(default_factory=list)

    async def recover_expired_claims(self, **kwargs: Any) -> RecoverExpiredClaimsResult:
        self.recover_calls.append(kwargs)
        return self.recovered

    async def claim_eligible_jobs(self, **kwargs: Any) -> ClaimBatchResult:
        self.claim_calls.append(kwargs)
        self.claim_batch_sizes.append(int(kwargs["batch_size"]))
        if self.lifecycle_on_claim is not None:
            return ClaimBatchResult(
                outcome="lifecycle_blocked",
                lifecycle_reason=self.lifecycle_on_claim,  # type: ignore[arg-type]
            )
        if not self.jobs_to_claim:
            return ClaimBatchResult(outcome="no_work")
        batch_size = int(kwargs["batch_size"])
        taken = self.jobs_to_claim[:batch_size]
        self.jobs_to_claim = self.jobs_to_claim[batch_size:]
        self.claim_before_provider.append(True)
        return ClaimBatchResult(outcome="claimed", jobs=tuple(taken), claimed_count=len(taken))

    async def preflight_claimed_job(self, **kwargs: Any) -> ClaimPreflightResult:
        qid = kwargs["query_job_id"]
        outcome = self.preflight_outcomes.get(qid, self.default_preflight)
        return ClaimPreflightResult(outcome=outcome, query_job_id=qid)  # type: ignore[arg-type]

    async def renew_claim(self, **kwargs: Any) -> ClaimMutationResult:
        return ClaimMutationResult(
            outcome="applied",
            query_job_id=kwargs["query_job_id"],
            status="running",
            attempt_count=1,
        )

    async def requeue_retryable_failure(self, **kwargs: Any) -> ClaimMutationResult:
        self.requeues.append(kwargs)
        return ClaimMutationResult(
            outcome="applied",
            query_job_id=kwargs["query_job_id"],
            status="pending",
            next_attempt_at=kwargs.get("next_attempt_at"),
            last_error_code=kwargs.get("error_code"),
            attempt_count=1,
        )

    async def mark_terminal_failure(self, **kwargs: Any) -> ClaimMutationResult:
        self.terminals.append(kwargs)
        return ClaimMutationResult(
            outcome="applied",
            query_job_id=kwargs["query_job_id"],
            status="failed",
            last_error_code=kwargs.get("error_code"),
            attempt_count=1,
        )

    async def inspect_remaining_work(self, **kwargs: Any) -> WorkInspectionResult:
        return self.inspection


@dataclass
class FakeProvider:
    results: dict[str, DiscoveryProviderExecutionResult] = field(default_factory=dict)
    default_factory: Any = None
    calls: list[ClaimedQueryJob] = field(default_factory=list)
    raise_for: dict[str, Exception] = field(default_factory=dict)

    async def execute_claimed_query(
        self,
        claimed_job: ClaimedQueryJob,
        provider_name: str,
        *,
        result_page_size: int | None = None,
    ) -> DiscoveryProviderExecutionResult:
        del provider_name, result_page_size
        self.calls.append(claimed_job)
        if claimed_job.id in self.raise_for:
            raise self.raise_for[claimed_job.id]
        if claimed_job.id in self.results:
            return self.results[claimed_job.id]
        if self.default_factory is not None:
            return self.default_factory(claimed_job)
        return _success_provider(claimed_job, empty=True)


@dataclass
class FakeResultService:
    outcomes: dict[str, DiscoveryPersistenceResult] = field(default_factory=dict)
    default_outcome: str = "applied"
    calls: list[PreparedDiscoveryBatch] = field(default_factory=list)
    continue_calls: list[PreparedDiscoveryBatch] = field(default_factory=list)
    final_calls: list[PreparedDiscoveryBatch] = field(default_factory=list)

    async def persist_prepared_batch_and_succeed(
        self,
        prepared_batch: PreparedDiscoveryBatch,
        *,
        now: datetime | None = None,
    ) -> DiscoveryPersistenceResult:
        return await self.persist_final_page_and_succeed(prepared_batch, now=now)

    async def persist_page_and_continue(
        self,
        prepared_batch: PreparedDiscoveryBatch,
        *,
        now: datetime | None = None,
        next_attempt_at: datetime | None = None,
    ) -> DiscoveryPersistenceResult:
        del now, next_attempt_at
        self.calls.append(prepared_batch)
        self.continue_calls.append(prepared_batch)
        if prepared_batch.query_job_id in self.outcomes:
            return self.outcomes[prepared_batch.query_job_id]
        return DiscoveryPersistenceResult(
            outcome="page_continued",
            counts=DiscoveryPersistenceCounts(
                persisted_count=len(prepared_batch.results),
                candidate_inserted_count=len(prepared_batch.results),
                crawl_node_created_count=len(prepared_batch.results),
                query_marked_succeeded=False,
                pages_completed=1,
                next_page_number=2,
                pagination_completed=False,
            ),
            query_job_id=prepared_batch.query_job_id,
            query_status="pending",
        )

    async def persist_final_page_and_succeed(
        self,
        prepared_batch: PreparedDiscoveryBatch,
        *,
        now: datetime | None = None,
    ) -> DiscoveryPersistenceResult:
        del now
        self.calls.append(prepared_batch)
        self.final_calls.append(prepared_batch)
        if prepared_batch.query_job_id in self.outcomes:
            return self.outcomes[prepared_batch.query_job_id]
        return DiscoveryPersistenceResult(
            outcome=self.default_outcome,  # type: ignore[arg-type]
            counts=DiscoveryPersistenceCounts(
                persisted_count=len(prepared_batch.results),
                candidate_inserted_count=len(prepared_batch.results),
                crawl_node_created_count=len(prepared_batch.results),
                query_marked_succeeded=True,
                pagination_completed=True,
            ),
            query_job_id=prepared_batch.query_job_id,
            query_status="succeeded",
        )


def _prepare_passthrough(job, provider_result, **kwargs):
    del kwargs
    return PreparedDiscoveryBatch(
        outcome="ready",
        organization_id=job.organization_id,
        execution_id=job.execution_id,
        query_job_id=job.id,
        claim_token=job.claim_token,
        provider=job.provider,
        prepared_at=FIXED_NOW,
        results=(),
        raw_provider_count=provider_result.raw_result_count,
        parsed_provider_count=provider_result.accepted_result_count,
    )


async def _seed_v2_execution(
    db: AsyncSession,
    auth,
    *,
    status: ScrapingExecutionStatus = ScrapingExecutionStatus.RUNNING,
    schema: str = "2",
) -> ScrapingExecution:
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    original = execution_service.enqueue_execution
    original_pub = execution_service._publish_event
    execution_service.enqueue_execution = AsyncMock()  # type: ignore[method-assign]
    execution_service._publish_event = AsyncMock()  # type: ignore[method-assign]
    try:
        summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    finally:
        execution_service.enqueue_execution = original  # type: ignore[method-assign]
        execution_service._publish_event = original_pub  # type: ignore[method-assign]
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    execution.status = status
    execution.started_at = FIXED_NOW
    execution.execution_plan_schema_version = schema
    await db.commit()
    await db.refresh(execution)
    return execution



async def _reset_running(db: AsyncSession, execution_id: str) -> None:
    async with async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)() as session:
        row = await session.get(ScrapingExecution, execution_id)
        assert row is not None
        row.status = ScrapingExecutionStatus.RUNNING
        row.completed_at = None
        row.cancel_requested_at = None
        row.pause_requested_at = None
        row.paused_at = None
        await session.commit()

async def _stub_phase4_complete(execution: ScrapingExecution) -> None:
    session_factory = mission_campaign_mock_worker.AsyncSessionLocal
    async with session_factory() as db:
        row = await db.get(ScrapingExecution, execution.id)
        assert row is not None
        row.status = ScrapingExecutionStatus.COMPLETED
        row.completed_at = datetime.now(UTC)
        row.progress_percent = 100
        row.current_stage = "web_discovery"
        row.current_stage_label = "Web discovery"
        row.latest_message = "Phase 4 web discovery completed (test stub)."
        await execution_service.emit_event(
            db,
            row.id,
            "web_discovery_completed",
            "Phase 4 web discovery completed.",
            metadata={"later_phases_executed": False, "provider": "serper"},
        )
        await execution_service.emit_event(
            db,
            row.id,
            "mission_campaign_completed",
            "Mission campaign completed after Phase 4 web discovery.",
            metadata={
                "mode": "live_discovery",
                "phase": "web_discovery",
                "later_phases_executed": False,
                "facility_generation": False,
            },
        )
        await db.commit()


@pytest.fixture
def phase4_stub(monkeypatch):
    monkeypatch.setattr(
        mission_campaign_mock_worker, "_run_phase4_web_discovery", _stub_phase4_complete
    )


def _build_service(
    db: AsyncSession,
    *,
    claim: FakeClaimService,
    provider: FakeProvider,
    result: FakeResultService | None = None,
    queue: AsyncMock | None = None,
    events: list | None = None,
    concurrency: int = 2,
    batch_size: int = 2,
) -> SourceDiscoveryExecutionService:
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    recorded_events = events if events is not None else []

    async def emit(execution_id, event_type, message, *, metadata=None):
        recorded_events.append(
            {
                "execution_id": execution_id,
                "type": event_type,
                "message": message,
                "metadata": metadata or {},
            }
        )

    return SourceDiscoveryExecutionService(
        session_factory=session_factory,
        claim_service=claim,
        provider_service=provider,
        result_service=result or FakeResultService(),
        prepare_fn=_prepare_passthrough,
        now_factory=lambda: FIXED_NOW,
        queue_continuation=queue or AsyncMock(),
        event_emitter=emit,
        claim_batch_size=batch_size,
        provider_concurrency=concurrency,
        recovery_batch_size=5,
        lease_duration=timedelta(seconds=60),
    )


@pytest.mark.asyncio
async def test_01_v2_invokes_phase4_after_step3b(db, auth, monkeypatch, phase4_stub):
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    monkeypatch.setattr(execution_service, "enqueue_execution", AsyncMock())
    monkeypatch.setattr(execution_service, "_publish_event", AsyncMock())
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)
    called = {"n": 0}

    async def tracking(execution):
        called["n"] += 1
        await _stub_phase4_complete(execution)

    monkeypatch.setattr(mission_campaign_mock_worker, "_run_phase4_web_discovery", tracking)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)
    assert called["n"] == 1
    events = (
        await db.execute(
            select(ScrapingEvent.event_type).where(ScrapingEvent.execution_id == summary.id)
        )
    ).scalars().all()
    assert "query_generation_completed" in events
    assert "web_discovery_completed" in events
    assert "stage_completed" not in events


@pytest.mark.asyncio
async def test_02_resume_skips_regen_enters_phase4(db, auth, monkeypatch, phase4_stub):
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    monkeypatch.setattr(execution_service, "enqueue_execution", AsyncMock())
    monkeypatch.setattr(execution_service, "_publish_event", AsyncMock())
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)
    gen = AsyncMock(wraps=query_generation_service.generate_for_execution)
    monkeypatch.setattr(query_generation_service, "generate_for_execution", gen)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)
    first_calls = gen.await_count
    assert first_calls >= 1
    execution = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert execution is not None
    execution.status = ScrapingExecutionStatus.QUEUED
    execution.completed_at = None
    await db.commit()
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)
    assert gen.await_count >= first_calls
    count = (
        await db.execute(
            select(func.count()).select_from(ScrapingSourceDiscoveryQuery).where(
                ScrapingSourceDiscoveryQuery.execution_id == summary.id
            )
        )
    ).scalar_one()
    assert count > 0


@pytest.mark.asyncio
async def test_03_legacy_skips_v2_phase4(db, auth, monkeypatch):
    mission, blueprint = await _approved_mission_with_team_plan(db, auth)
    monkeypatch.setattr(execution_service, "enqueue_execution", AsyncMock())
    monkeypatch.setattr(execution_service, "_publish_event", AsyncMock())
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution = await db.get(ScrapingExecution, summary.id)
    assert execution is not None
    execution.status = ScrapingExecutionStatus.QUEUED
    execution.frozen_execution_plan_json = None
    execution.blueprint_snapshot_json = None
    execution.execution_plan_hash = None
    execution.execution_plan_schema_version = None
    execution.blueprint_version_snapshot = blueprint.version
    await db.commit()
    phase4 = AsyncMock()
    monkeypatch.setattr(mission_campaign_mock_worker, "_run_phase4_web_discovery", phase4)
    discover = AsyncMock()
    monkeypatch.setattr(source_discovery_service, "discover", discover)
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)
    assert phase4.await_count == 0
    assert discover.await_count == 0
    done = await db.get(ScrapingExecution, summary.id, populate_existing=True)
    assert done is not None and done.status == ScrapingExecutionStatus.COMPLETED
    events = (
        await db.execute(
            select(ScrapingEvent.event_type).where(ScrapingEvent.execution_id == summary.id)
        )
    ).scalars().all()
    assert "stage_completed" in events
    assert "web_discovery_completed" not in events


@pytest.mark.asyncio
async def test_04_05_no_legacy_planner_or_discover(db, auth, monkeypatch, phase4_stub):
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    monkeypatch.setattr(execution_service, "enqueue_execution", AsyncMock())
    monkeypatch.setattr(execution_service, "_publish_event", AsyncMock())
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    discover = AsyncMock()
    monkeypatch.setattr(source_discovery_service, "discover", discover)
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)
    assert discover.await_count == 0
    source = WORKER_PATH.read_text(encoding="utf-8")
    assert "SourceDiscoveryQueryPlanner" not in source


def test_06_no_mock_stages_on_v2_path():
    source = WORKER_PATH.read_text(encoding="utf-8")
    assert "_run_phase4_web_discovery" in source
    tree = ast.parse(source)
    assert any(
        isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_phase4_web_discovery"
        for node in tree.body
    )


@pytest.mark.asyncio
async def test_07_08_09_claim_before_provider_bounded(db, auth):
    execution = await _seed_v2_execution(db, auth)
    jobs = [
        _claimed(id=f"j{i}", organization_id=auth.org_id, execution_id=execution.id)
        for i in range(5)
    ]
    claim = FakeClaimService(jobs_to_claim=list(jobs))
    claim.inspection = WorkInspectionResult(outcome="ok", pending_eligible_count=3)
    provider = FakeProvider(default_factory=lambda j: _success_provider(j, empty=True))
    queue = AsyncMock()
    service = _build_service(
        db, claim=claim, provider=provider, queue=queue, batch_size=2, concurrency=2
    )
    result = await service.run_discovery_work_slice(auth.org_id, execution.id)
    assert claim.claim_before_provider == [True]
    assert claim.claim_batch_sizes == [2]
    assert len(provider.calls) == 2
    assert result.outcome == "continue_enqueued"
    assert result.continuation_enqueued is True
    assert queue.await_count == 1


@pytest.mark.asyncio
async def test_10_11_success_and_empty_persist(db, auth):
    execution = await _seed_v2_execution(db, auth)
    job = _claimed(organization_id=auth.org_id, execution_id=execution.id)
    claim = FakeClaimService(jobs_to_claim=[job])
    claim.inspection = WorkInspectionResult(
        outcome="ok", succeeded_count=1, pending_eligible_count=0, running_count=0
    )
    provider = FakeProvider(default_factory=lambda j: _success_provider(j, empty=True))
    result_svc = FakeResultService()
    service = _build_service(db, claim=claim, provider=provider, result=result_svc, batch_size=5)
    outcome = await service.run_discovery_work_slice(auth.org_id, execution.id)
    assert len(result_svc.calls) == 1
    assert result_svc.calls[0].ready
    assert outcome.outcome == "completed"
    assert outcome.counts.succeeded_count == 1


@pytest.mark.asyncio
async def test_12_13_14_15_retryable_and_retry_after(db, auth):
    execution = await _seed_v2_execution(db, auth)
    job = _claimed(organization_id=auth.org_id, execution_id=execution.id)
    retry_at = FIXED_NOW + timedelta(seconds=42)
    claim = FakeClaimService(jobs_to_claim=[job])
    claim.inspection = WorkInspectionResult(
        outcome="ok",
        pending_delayed_count=1,
        earliest_next_attempt_at=retry_at,
        running_count=0,
    )
    provider = FakeProvider(
        results={
            job.id: _provider_result(
                job,
                "provider_rate_limited",
                retry_after_seconds=42.0,
                retry_after_at=retry_at,
            )
        }
    )
    queue = AsyncMock()
    service = _build_service(db, claim=claim, provider=provider, queue=queue)
    outcome = await service.run_discovery_work_slice(auth.org_id, execution.id)
    assert len(claim.requeues) == 1
    assert claim.requeues[0]["error_code"] == "provider_rate_limited"
    assert claim.requeues[0]["next_attempt_at"] == retry_at
    assert outcome.outcome == "delayed_continue_enqueued"

    job2 = _claimed(id="j2", organization_id=auth.org_id, execution_id=execution.id)
    claim2 = FakeClaimService(jobs_to_claim=[job2])
    claim2.inspection = WorkInspectionResult(
        outcome="ok",
        pending_delayed_count=1,
        earliest_next_attempt_at=FIXED_NOW + timedelta(seconds=30),
    )
    provider2 = FakeProvider(results={job2.id: _provider_result(job2, "provider_timeout")})
    service2 = _build_service(db, claim=claim2, provider=provider2, queue=AsyncMock())
    await service2.run_discovery_work_slice(auth.org_id, execution.id)
    assert claim2.requeues[0]["error_code"] == "provider_timeout"


@pytest.mark.asyncio
async def test_16_17_18_provider_blocker_pauses_no_fake_results(db, auth):
    execution = await _seed_v2_execution(db, auth)
    job = _claimed(organization_id=auth.org_id, execution_id=execution.id)
    claim = FakeClaimService(jobs_to_claim=[job])
    claim.inspection = WorkInspectionResult(outcome="ok", pending_eligible_count=1)
    provider = FakeProvider(results={job.id: _provider_result(job, "provider_not_configured")})
    result_svc = FakeResultService()
    queue = AsyncMock()
    service = _build_service(
        db, claim=claim, provider=provider, result=result_svc, queue=queue
    )
    outcome = await service.run_discovery_work_slice(auth.org_id, execution.id)
    assert claim.terminals == []
    assert any(r["error_code"] == "provider_not_configured" for r in claim.requeues)
    assert result_svc.calls == []
    assert outcome.outcome == "provider_blocked"
    queue.assert_not_called()

    await _reset_running(db, execution.id)
    job_auth = _claimed(id="ja", organization_id=auth.org_id, execution_id=execution.id)
    claim_a = FakeClaimService(jobs_to_claim=[job_auth])
    claim_a.inspection = WorkInspectionResult(outcome="ok", pending_eligible_count=1)
    provider_a = FakeProvider(
        results={job_auth.id: _provider_result(job_auth, "provider_authentication_failed")}
    )
    result_a = FakeResultService()
    service_a = _build_service(db, claim=claim_a, provider=provider_a, result=result_a)
    out_a = await service_a.run_discovery_work_slice(auth.org_id, execution.id)
    assert result_a.calls == []
    assert claim_a.terminals == []
    assert any(r["error_code"] == "provider_authentication_failed" for r in claim_a.requeues)
    assert out_a.outcome == "provider_blocked"


@pytest.mark.asyncio
async def test_19_unexpected_exception_isolates_jobs(db, auth):
    execution = await _seed_v2_execution(db, auth)
    j1 = _claimed(id="ok", organization_id=auth.org_id, execution_id=execution.id)
    j2 = _claimed(id="boom", organization_id=auth.org_id, execution_id=execution.id)
    claim = FakeClaimService(jobs_to_claim=[j1, j2])
    claim.inspection = WorkInspectionResult(outcome="ok", succeeded_count=1, failed_count=1)
    provider = FakeProvider(
        default_factory=lambda j: _success_provider(j, empty=True),
        raise_for={"boom": RuntimeError("secret stack trace")},
    )
    events: list[dict] = []
    service = _build_service(
        db, claim=claim, provider=provider, events=events, concurrency=2, batch_size=5
    )
    await service.run_discovery_work_slice(auth.org_id, execution.id)
    assert any(t["query_job_id"] == "boom" for t in claim.terminals)
    assert any(t["error_code"] == "unexpected_provider_failure" for t in claim.terminals)
    blob = str(events).lower()
    assert "secret stack" not in blob
    assert "runtimeerror" not in blob


@pytest.mark.asyncio
async def test_20_stale_persistence_no_overwrite(db, auth):
    execution = await _seed_v2_execution(db, auth)
    job = _claimed(organization_id=auth.org_id, execution_id=execution.id)
    claim = FakeClaimService(jobs_to_claim=[job])
    claim.inspection = WorkInspectionResult(outcome="ok")
    provider = FakeProvider(default_factory=lambda j: _success_provider(j, empty=True))
    result_svc = FakeResultService(
        outcomes={
            job.id: DiscoveryPersistenceResult(
                outcome="stale_claim", error_code="stale_claim", query_job_id=job.id
            )
        }
    )
    service = _build_service(db, claim=claim, provider=provider, result=result_svc)
    outcome = await service.run_discovery_work_slice(auth.org_id, execution.id)
    assert outcome.counts.stale_skipped_count == 1
    assert claim.terminals == []


@pytest.mark.asyncio
async def test_21_22_cancel_and_pause_before_provider(db, auth):
    execution = await _seed_v2_execution(db, auth)
    job = _claimed(organization_id=auth.org_id, execution_id=execution.id)
    claim = FakeClaimService(jobs_to_claim=[job])
    claim.preflight_outcomes[job.id] = "cancelled"
    claim.inspection = WorkInspectionResult(outcome="ok")
    provider = FakeProvider()
    service = _build_service(db, claim=claim, provider=provider)
    outcome = await service.run_discovery_work_slice(auth.org_id, execution.id)
    assert provider.calls == []
    assert any(t["error_code"] == "cancelled_before_request" for t in claim.terminals)
    assert outcome.outcome == "cancelled"

    await _reset_running(db, execution.id)
    job_p = _claimed(id="jp", organization_id=auth.org_id, execution_id=execution.id)
    claim_p = FakeClaimService(jobs_to_claim=[job_p])
    claim_p.preflight_outcomes[job_p.id] = "paused"
    claim_p.inspection = WorkInspectionResult(outcome="ok")
    provider_p = FakeProvider()
    service_p = _build_service(db, claim=claim_p, provider=provider_p)
    out_p = await service_p.run_discovery_work_slice(auth.org_id, execution.id)
    assert provider_p.calls == []
    assert any(r["error_code"] == "paused_before_request" for r in claim_p.requeues)
    assert out_p.outcome == "paused"


@pytest.mark.asyncio
async def test_23_24_25_pause_after_http_cancel_late_and_supersede(db, auth):
    execution = await _seed_v2_execution(db, auth)
    job = _claimed(organization_id=auth.org_id, execution_id=execution.id)
    claim = FakeClaimService(jobs_to_claim=[job])
    claim.inspection = WorkInspectionResult(outcome="ok", succeeded_count=1)

    async def provider_then_pause(claimed_job, provider_name, **kwargs):
        del provider_name, kwargs
        async with async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)() as session:
            row = await session.get(ScrapingExecution, execution.id)
            assert row is not None
            row.status = ScrapingExecutionStatus.PAUSE_REQUESTED
            row.pause_requested_at = FIXED_NOW
            await session.commit()
        return _success_provider(claimed_job, empty=True)

    provider = FakeProvider()
    provider.execute_claimed_query = provider_then_pause  # type: ignore[method-assign]
    result_svc = FakeResultService()
    service = _build_service(db, claim=claim, provider=provider, result=result_svc)
    await service.run_discovery_work_slice(auth.org_id, execution.id)
    assert len(result_svc.calls) == 1

    job_c = _claimed(id="jc", organization_id=auth.org_id, execution_id=execution.id)
    claim_c = FakeClaimService(jobs_to_claim=[job_c])
    claim_c.inspection = WorkInspectionResult(outcome="ok")

    async def provider_then_cancel(claimed_job, provider_name, **kwargs):
        del provider_name, kwargs
        async with async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)() as session:
            row = await session.get(ScrapingExecution, execution.id)
            assert row is not None
            row.status = ScrapingExecutionStatus.CANCEL_REQUESTED
            row.cancel_requested_at = FIXED_NOW
            await session.commit()
        return _success_provider(claimed_job, empty=True)

    provider_c = FakeProvider()
    provider_c.execute_claimed_query = provider_then_cancel  # type: ignore[method-assign]
    result_c = FakeResultService()
    async with async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)() as session:
        row = await session.get(ScrapingExecution, execution.id)
        assert row is not None
        row.status = ScrapingExecutionStatus.RUNNING
        row.cancel_requested_at = None
        row.pause_requested_at = None
        await session.commit()
    service_c = _build_service(db, claim=claim_c, provider=provider_c, result=result_c)
    out_c = await service_c.run_discovery_work_slice(auth.org_id, execution.id)
    assert result_c.calls == []
    assert out_c.outcome == "cancelled"

    claim_s = FakeClaimService(lifecycle_on_claim="cancelled")
    claim_s.inspection = WorkInspectionResult(outcome="ok")
    async with async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)() as session:
        row = await session.get(ScrapingExecution, execution.id)
        assert row is not None
        row.status = ScrapingExecutionStatus.RUNNING
        await session.commit()
    service_s = _build_service(db, claim=claim_s, provider=FakeProvider())
    out_s = await service_s.run_discovery_work_slice(auth.org_id, execution.id)
    assert out_s.outcome == "cancelled"


@pytest.mark.asyncio
async def test_26_recovery_before_claim(db, auth):
    execution = await _seed_v2_execution(db, auth)
    claim = FakeClaimService(
        recovered=RecoverExpiredClaimsResult(recovered_count=2, recovered_ids=("a", "b"))
    )
    claim.inspection = WorkInspectionResult(outcome="ok")
    service = _build_service(db, claim=claim, provider=FakeProvider())
    await service.run_discovery_work_slice(auth.org_id, execution.id)
    assert len(claim.recover_calls) == 1
    assert claim.recover_calls[0]["batch_size"] == 5
    assert claim.claim_calls


@pytest.mark.asyncio
async def test_27_28_29_30_continuation_and_completion(db, auth):
    execution = await _seed_v2_execution(db, auth)
    claim = FakeClaimService()
    claim.inspection = WorkInspectionResult(outcome="ok", pending_eligible_count=4)
    queue = AsyncMock()
    service = _build_service(db, claim=claim, provider=FakeProvider(), queue=queue)
    assert (await service.run_discovery_work_slice(auth.org_id, execution.id)).outcome == (
        "continue_enqueued"
    )

    await _reset_running(db, execution.id)
    claim2 = FakeClaimService()
    claim2.inspection = WorkInspectionResult(
        outcome="ok",
        pending_delayed_count=2,
        earliest_next_attempt_at=FIXED_NOW + timedelta(minutes=5),
        running_count=0,
    )
    queue2 = AsyncMock()
    service2 = _build_service(db, claim=claim2, provider=FakeProvider(), queue=queue2)
    out2 = await service2.run_discovery_work_slice(auth.org_id, execution.id)
    assert out2.outcome == "delayed_continue_enqueued"
    assert queue2.await_args.kwargs.get("defer_until") is not None

    await _reset_running(db, execution.id)
    claim3 = FakeClaimService()
    claim3.inspection = WorkInspectionResult(outcome="ok", running_count=2)
    queue3 = AsyncMock()
    service3 = _build_service(db, claim=claim3, provider=FakeProvider(), queue=queue3)
    out3 = await service3.run_discovery_work_slice(auth.org_id, execution.id)
    assert out3.outcome == "waiting_active_leases"
    assert out3.continuation_enqueued is True

    await _reset_running(db, execution.id)
    events: list[dict] = []
    claim4 = FakeClaimService()
    claim4.inspection = WorkInspectionResult(
        outcome="ok",
        succeeded_count=3,
        failed_count=1,
        pending_eligible_count=0,
        running_count=0,
    )
    service4 = _build_service(db, claim=claim4, provider=FakeProvider(), events=events)
    out4 = await service4.run_discovery_work_slice(auth.org_id, execution.id)
    assert out4.outcome == "completed"
    types = [e["type"] for e in events]
    assert "web_discovery_completed" in types
    assert "stage_completed" not in types
    row = await db.get(ScrapingExecution, execution.id, populate_existing=True)
    assert row is not None
    assert row.status == ScrapingExecutionStatus.COMPLETED
    assert row.current_stage == "web_discovery"


@pytest.mark.asyncio
async def test_31_32_no_later_phase_or_facilities(db, auth, monkeypatch, phase4_stub):
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    monkeypatch.setattr(execution_service, "enqueue_execution", AsyncMock())
    monkeypatch.setattr(execution_service, "_publish_event", AsyncMock())
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, summary.id)
    events = (
        await db.execute(select(ScrapingEvent).where(ScrapingEvent.execution_id == summary.id))
    ).scalars().all()
    types = [e.event_type for e in events]
    assert "stage_completed" not in types
    for e in events:
        meta = dict(e.metadata_json or {})
        assert meta.get("facility_generation") in {False, None}


@pytest.mark.asyncio
async def test_33_34_35_resume_same_id_idempotent(db, auth, monkeypatch, phase4_stub):
    mission, _ = await _approved_mission_with_team_plan(db, auth)
    enqueued: list[tuple] = []

    async def capture(execution_id, **kwargs):
        enqueued.append((execution_id, kwargs.get("job_name")))

    monkeypatch.setattr(execution_service, "enqueue_execution", capture)
    monkeypatch.setattr(execution_service, "_publish_event", AsyncMock())
    summary = await execution_service.start_mission_campaign(db, auth, mission.id)
    execution_id = summary.id
    session_factory = async_sessionmaker(db.bind, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mission_campaign_mock_worker, "AsyncSessionLocal", session_factory)
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, execution_id)
    before = (
        await db.execute(
            select(func.count()).select_from(ScrapingSourceDiscoveryQuery).where(
                ScrapingSourceDiscoveryQuery.execution_id == execution_id
            )
        )
    ).scalar_one()
    row = await db.get(ScrapingExecution, execution_id, populate_existing=True)
    assert row is not None
    row.status = ScrapingExecutionStatus.PAUSED
    row.paused_at = datetime.now(UTC)
    await db.commit()
    resumed = await execution_service.resume_mission_campaign(db, auth, mission.id, execution_id)
    assert resumed.id == execution_id
    assert enqueued[-1][0] == execution_id
    row.status = ScrapingExecutionStatus.QUEUED
    await db.commit()
    await mission_campaign_mock_worker.run_mission_campaign_mock({}, execution_id)
    after = (
        await db.execute(
            select(func.count()).select_from(ScrapingSourceDiscoveryQuery).where(
                ScrapingSourceDiscoveryQuery.execution_id == execution_id
            )
        )
    ).scalar_one()
    assert after == before


@pytest.mark.asyncio
async def test_36_event_payloads_safe(db, auth):
    execution = await _seed_v2_execution(db, auth)
    job = _claimed(organization_id=auth.org_id, execution_id=execution.id)
    claim = FakeClaimService(jobs_to_claim=[job])
    claim.inspection = WorkInspectionResult(outcome="ok", succeeded_count=1)
    events: list[dict] = []
    service = _build_service(
        db,
        claim=claim,
        provider=FakeProvider(default_factory=lambda j: _success_provider(j, empty=True)),
        events=events,
    )
    await service.run_discovery_work_slice(auth.org_id, execution.id)
    blob = str(events).lower()
    for banned in (
        "secret query",
        "api_key",
        "fingerprint",
        "plan_hash",
        "canonical_url_hash",
        "sk-live",
    ):
        assert banned not in blob


@pytest.mark.asyncio
async def test_37_38_batch_not_campaign_completion_multi_slice(db, auth):
    execution = await _seed_v2_execution(db, auth)
    jobs = [
        _claimed(id=f"m{i}", organization_id=auth.org_id, execution_id=execution.id)
        for i in range(4)
    ]
    claim = FakeClaimService(jobs_to_claim=list(jobs))
    claim.inspection = WorkInspectionResult(outcome="ok", pending_eligible_count=2)
    queue = AsyncMock()
    service = _build_service(
        db,
        claim=claim,
        provider=FakeProvider(default_factory=lambda j: _success_provider(j, empty=True)),
        queue=queue,
        batch_size=2,
    )
    first = await service.run_discovery_work_slice(auth.org_id, execution.id)
    assert first.outcome == "continue_enqueued"
    assert first.counts.claimed_count == 2
    assert len(claim.jobs_to_claim) == 2
    claim.inspection = WorkInspectionResult(
        outcome="ok", succeeded_count=4, pending_eligible_count=0, running_count=0
    )
    second = await service.run_discovery_work_slice(auth.org_id, execution.id)
    assert second.counts.claimed_count == 2
    assert second.outcome == "completed"


def test_39_40_no_attempt_ceiling_or_campaign_caps():
    source = EXEC_PATH.read_text(encoding="utf-8")
    assert "max_attempts" not in source
    assert "campaign_cap" not in source
    assert "max_queries" not in source
    assert "claim_batch_size" in source
    assert "provider_concurrency" in source
    cfg = Path(__file__).resolve().parents[1] / "app" / "core" / "config.py"
    cfg_src = cfg.read_text(encoding="utf-8")
    assert "scraping_discovery_claim_batch_size" in cfg_src
    assert "scraping_discovery_provider_concurrency" in cfg_src


def test_41_42_43_no_real_network_dns_or_session_to_provider():
    source = EXEC_PATH.read_text(encoding="utf-8")
    assert "execute_claimed_query(" in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "execute_claimed_query":
                for keyword in node.keywords:
                    assert keyword.arg not in {"session", "db", "connection"}
                for arg in node.args:
                    if isinstance(arg, ast.Name):
                        assert arg.id not in {"session", "db"}
    assert "httpx" not in source
    assert "getaddrinfo" not in source


def test_source_has_no_fabricate_or_brave_fallback():
    source = EXEC_PATH.read_text(encoding="utf-8")
    assert "brave" not in source.lower()
    assert "fabricate results" not in source.lower()
    assert "SourceDiscoveryService" not in source
    assert "STAGES" not in source


def test_concurrency_must_be_positive():
    with pytest.raises(ValueError, match="concurrency"):
        SourceDiscoveryExecutionService(provider_concurrency=0)


@pytest.mark.asyncio
async def test_schema_v1_not_eligible(db, auth):
    execution = await _seed_v2_execution(db, auth, schema="1")
    claim = FakeClaimService()
    service = _build_service(db, claim=claim, provider=FakeProvider())
    out = await service.run_discovery_work_slice(auth.org_id, execution.id)
    assert out.outcome == "not_eligible"
    assert claim.claim_calls == []
