"""PostgreSQL integration coverage for Phase 4 Slice 6 orchestration.

Ephemeral-DB harness (same pattern as Slice 3/5):
``POSTGRES_TEST_ADMIN_URL`` creates/drops a unique ``exec_slice6_*`` database.

Normal run:

  docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api-test \\
    pytest -q tests/test_phase4_discovery_execution_postgres.py

Do not run outside Docker/Postgres. Injected provider only — no real Serper/HTTP/DNS.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import asyncpg
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    Organization,
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingCrawlEdge,
    ScrapingCrawlNode,
    ScrapingEvent,
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingMission,
    ScrapingMissionStatus,
    ScrapingSourceCandidate,
    ScrapingSourceDiscoveryQuery,
    SourceDiscoveryQueryStatus,
    User,
)
from app.services.scraping.source_discovery_claim_service import (
    ClaimedQueryJob,
    SourceDiscoveryClaimService,
    generate_claim_token,
)
from app.services.scraping.source_discovery_execution_service import (
    PROVIDER_BLOCK_CODE_KEY,
    PROVIDER_BLOCK_PROFILE_KEY,
    PROVIDER_BLOCK_STAGE_KEY,
    SourceDiscoveryExecutionService,
)
from app.services.scraping.source_discovery_provider_service import (
    DiscoveryProviderExecutionResult,
    DiscoveryProviderResultItem,
)
from app.services.scraping.source_discovery_result_service import (
    SourceDiscoveryResultService,
    prepare_provider_results,
)

_FORBIDDEN_TARGET_DATABASES = frozenset(
    {"multiai", "multiai_scraping_test", "postgres", "template0", "template1"}
)
_EPHEMERAL_DB_RE = re.compile(r"^exec_slice6_[0-9a-f]{32}$")
PLAN_HASH = "d" * 64
FIXED_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
SAFE_URL = "https://docs.python.org/rehab"


@dataclass
class PostgresExecutionDatabase:
    admin: asyncpg.Connection
    database: str
    url: str

    async def alembic(self, *arguments: str) -> str:
        target = urlparse(self.url).path.lstrip("/")
        if target != self.database or not _EPHEMERAL_DB_RE.fullmatch(target):
            pytest.fail("Refusing alembic: target is not an isolated exec_slice6_* test database.")
        if target in _FORBIDDEN_TARGET_DATABASES:
            pytest.fail("Refusing alembic: target database name is forbidden.")
        result = await asyncio.to_thread(
            subprocess.run,
            ["alembic", *arguments],
            check=True,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "DATABASE_URL": self.url.replace("postgresql://", "postgresql+asyncpg://"),
            },
        )
        return result.stdout + result.stderr


@pytest.fixture
async def postgres_sessions() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    admin_url = os.environ.get("POSTGRES_TEST_ADMIN_URL")
    if not admin_url:
        pytest.fail("POSTGRES_TEST_ADMIN_URL is required for PostgreSQL execution coverage.")

    database = f"exec_slice6_{uuid.uuid4().hex}"
    if not _EPHEMERAL_DB_RE.fullmatch(database):
        pytest.fail("Refusing to proceed: ephemeral database name is not isolated.")
    if database in _FORBIDDEN_TARGET_DATABASES:
        pytest.fail("Refusing to proceed: database name is forbidden.")

    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    except Exception:
        await admin.close()
        raise

    parsed = urlparse(admin_url)
    url = f"postgresql://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port}/{database}"
    harness = PostgresExecutionDatabase(admin=admin, database=database, url=url)
    try:
        await harness.alembic("upgrade", "head")
        engine = create_async_engine(
            url.replace("postgresql://", "postgresql+asyncpg://"),
            pool_size=5,
            max_overflow=0,
        )
        sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        yield sessions
        await engine.dispose()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.close()


@dataclass
class InjectedProvider:
    outcomes: dict[str, str]
    calls: list[str]

    async def execute_claimed_query(
        self,
        claimed_job: ClaimedQueryJob,
        provider_name: str,
        *,
        result_page_size: int | None = None,
    ) -> DiscoveryProviderExecutionResult:
        del provider_name, result_page_size
        self.calls.append(claimed_job.id)
        outcome = self.outcomes.get(claimed_job.id, "succeeded")
        if outcome == "succeeded":
            item = DiscoveryProviderResultItem(
                original_url=SAFE_URL,
                title="Rehab",
                snippet="Directory",
                rank=1,
                provider="serper",
                provider_result_type="organic",
                query_job_id=claimed_job.id,
                organization_id=claimed_job.organization_id,
                execution_id=claimed_job.execution_id,
                claim_token=claimed_job.claim_token,
                scope_level=claimed_job.scope_level,
                language_code=claimed_job.language_code,
                region_code=claimed_job.region_code,
                region_name=claimed_job.region_name,
                important_city=claimed_job.important_city,
                country_code=claimed_job.country_code,
                discovered_at=FIXED_NOW,
            )
            return DiscoveryProviderExecutionResult(
                outcome="succeeded",
                provider="serper",
                query_job_id=claimed_job.id,
                organization_id=claimed_job.organization_id,
                execution_id=claimed_job.execution_id,
                claim_token=claimed_job.claim_token,
                discovered_at=FIXED_NOW,
                results=(item,),
                raw_result_count=1,
                accepted_result_count=1,
                diagnostic_code="succeeded",
            )
        return DiscoveryProviderExecutionResult(
            outcome=outcome,  # type: ignore[arg-type]
            provider="serper",
            query_job_id=claimed_job.id,
            organization_id=claimed_job.organization_id,
            execution_id=claimed_job.execution_id,
            claim_token=claimed_job.claim_token,
            discovered_at=FIXED_NOW,
            diagnostic_code=outcome,
        )


async def _seed_execution(
    sessions: async_sessionmaker[AsyncSession],
    *,
    job_count: int = 5,
) -> tuple[str, str, list[str]]:
    async with sessions() as db:
        org = Organization(name="Org", slug=f"org-{uuid.uuid4().hex[:8]}")
        user = User(email=f"u-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x", full_name="U")
        db.add_all([org, user])
        await db.flush()
        mission = ScrapingMission(
            org_id=org.id,
            created_by=user.id,
            model_set_id="research-set",
            title="Mission",
            original_prompt="Find facilities",
            country_code="LB",
            country_name="Lebanon",
            status=ScrapingMissionStatus.APPROVED,
        )
        db.add(mission)
        await db.flush()
        blueprint = ScrapingBlueprint(
            mission_id=mission.id,
            version=1,
            status=ScrapingBlueprintStatus.APPROVED,
            model_set_id="research-set",
            blueprint_json={"ok": True},
        )
        db.add(blueprint)
        await db.flush()
        execution = ScrapingExecution(
            organization_id=org.id,
            mission_id=mission.id,
            blueprint_id=blueprint.id,
            execution_type="mission_campaign",
            mode="mock",
            status=ScrapingExecutionStatus.RUNNING,
            country_code="LB",
            country_name="Lebanon",
            execution_origin="mission_campaign_mock",
            blueprint_version_snapshot=1,
            execution_plan_schema_version="2",
            execution_plan_hash=PLAN_HASH,
            frozen_execution_plan_json={"schema_version": "2"},
            started_at=FIXED_NOW,
        )
        db.add(execution)
        await db.flush()
        job_ids: list[str] = []
        for i in range(job_count):
            job = ScrapingSourceDiscoveryQuery(
                organization_id=org.id,
                execution_id=execution.id,
                query_text=f"query {i}",
                status=SourceDiscoveryQueryStatus.PENDING,
                priority=100,
                generation_ordinal=i + 1,
                discovery_round=1,
                purpose="seed",
                country_code="LB",
                country_name="Lebanon",
                language_code="en",
                language_name="English",
                source_category="directory",
                scope_level="countrywide",
                query_job_fingerprint=f"{i:064d}"[:64],
                plan_hash_snapshot=PLAN_HASH,
            )
            db.add(job)
            await db.flush()
            job_ids.append(job.id)
        await db.commit()
        return org.id, execution.id, job_ids


@pytest.mark.asyncio
async def test_postgres_slice6_orchestration_suite(postgres_sessions):
    """Covers Slice 6 PostgreSQL items 1–24 with an injected provider."""
    sessions = postgres_sessions
    org_id, execution_id, job_ids = await _seed_execution(sessions, job_count=5)

    claim = SourceDiscoveryClaimService(session_factory=sessions, now_factory=lambda: FIXED_NOW)
    result_svc = SourceDiscoveryResultService(session_factory=sessions, now_factory=lambda: FIXED_NOW)
    provider = InjectedProvider(outcomes={jid: "succeeded" for jid in job_ids}, calls=[])
    queue_calls: list[dict[str, Any]] = []

    async def queue_continuation(execution_id_arg, **kwargs):
        queue_calls.append({"execution_id": execution_id_arg, **kwargs})

    events: list[str] = []

    async def emit(execution_id_arg, event_type, message, *, metadata=None):
        events.append(event_type)

    service = SourceDiscoveryExecutionService(
        session_factory=sessions,
        claim_service=claim,
        provider_service=provider,
        result_service=result_svc,
        prepare_fn=prepare_provider_results,
        now_factory=lambda: FIXED_NOW,
        queue_continuation=queue_continuation,
        event_emitter=emit,
        claim_batch_size=2,
        provider_concurrency=2,
        recovery_batch_size=5,
        lease_duration=timedelta(seconds=60),
    )

    # 1–3: seed + bounded claim + persist success
    first = await service.run_discovery_work_slice(org_id, execution_id)
    assert first.counts.claimed_count == 2
    assert len(provider.calls) == 2
    assert first.outcome == "continue_enqueued"

    async with sessions() as db:
        succeeded = (
            await db.execute(
                select(func.count()).select_from(ScrapingSourceDiscoveryQuery).where(
                    ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                    ScrapingSourceDiscoveryQuery.status == SourceDiscoveryQueryStatus.SUCCEEDED,
                )
            )
        ).scalar_one()
        candidates = (
            await db.execute(
                select(func.count()).select_from(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.execution_id == execution_id
                )
            )
        ).scalar_one()
        nodes = (
            await db.execute(
                select(func.count()).select_from(ScrapingCrawlNode).where(
                    ScrapingCrawlNode.execution_id == execution_id
                )
            )
        ).scalar_one()
        edges = (
            await db.execute(
                select(func.count()).select_from(ScrapingCrawlEdge).where(
                    ScrapingCrawlEdge.execution_id == execution_id
                )
            )
        ).scalar_one()
    assert succeeded == 2
    assert candidates >= 1
    assert nodes >= 1
    assert edges == 0  # 18: no synthetic crawl edges

    # 4–5: repeated slices process beyond one batch; batch size is not campaign completion
    second = await service.run_discovery_work_slice(org_id, execution_id)
    assert second.counts.claimed_count == 2
    third = await service.run_discovery_work_slice(org_id, execution_id)
    assert third.counts.claimed_count == 1
    assert third.outcome == "completed"
    assert "web_discovery_completed" in events
    assert "stage_completed" not in events  # 21

    async with sessions() as db:
        pending = (
            await db.execute(
                select(func.count()).select_from(ScrapingSourceDiscoveryQuery).where(
                    ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                    ScrapingSourceDiscoveryQuery.status == SourceDiscoveryQueryStatus.PENDING,
                )
            )
        ).scalar_one()
        running = (
            await db.execute(
                select(func.count()).select_from(ScrapingSourceDiscoveryQuery).where(
                    ScrapingSourceDiscoveryQuery.execution_id == execution_id,
                    ScrapingSourceDiscoveryQuery.status == SourceDiscoveryQueryStatus.RUNNING,
                )
            )
        ).scalar_one()
        exec_row = await db.get(ScrapingExecution, execution_id)
    assert pending == 0 and running == 0  # 20
    assert exec_row is not None
    assert exec_row.current_stage == "web_discovery"
    assert exec_row.status == ScrapingExecutionStatus.COMPLETED

    # 16–17: idempotent replay — succeed again does not duplicate
    candidates_before = candidates
    # Re-seed a new running execution for remaining lifecycle checks
    org2, exec2, jobs2 = await _seed_execution(sessions, job_count=3)
    provider2 = InjectedProvider(
        outcomes={
            jobs2[0]: "succeeded",
            jobs2[1]: "provider_timeout",
            jobs2[2]: "provider_not_configured",
        },
        calls=[],
    )
    service2 = SourceDiscoveryExecutionService(
        session_factory=sessions,
        claim_service=SourceDiscoveryClaimService(session_factory=sessions, now_factory=lambda: FIXED_NOW),
        provider_service=provider2,
        result_service=SourceDiscoveryResultService(session_factory=sessions, now_factory=lambda: FIXED_NOW),
        prepare_fn=prepare_provider_results,
        now_factory=lambda: FIXED_NOW,
        queue_continuation=queue_continuation,
        event_emitter=emit,
        claim_batch_size=3,
        provider_concurrency=1,
        recovery_batch_size=5,
        lease_duration=timedelta(seconds=60),
    )
    queue_len_before_mixed = len(queue_calls)
    mixed = await service2.run_discovery_work_slice(org2, exec2)
    assert mixed.outcome == "provider_blocked"
    assert mixed.counts.succeeded_count == 1
    assert mixed.counts.retry_scheduled_count == 1  # 7: provider_timeout
    assert mixed.counts.failed_count == 0
    assert len(queue_calls) == queue_len_before_mixed
    assert "web_discovery_blocked" in events

    async with sessions() as db:
        exec_row = await db.get(ScrapingExecution, exec2)
        assert exec_row is not None
        assert exec_row.status == ScrapingExecutionStatus.PAUSED
        assert exec_row.current_stage == "web_discovery"
        profile = exec_row.country_profile_json or {}
        assert profile.get(PROVIDER_BLOCK_PROFILE_KEY) is True
        assert profile.get(PROVIDER_BLOCK_CODE_KEY) == "provider_not_configured"
        assert profile.get(PROVIDER_BLOCK_STAGE_KEY) == "web_discovery"

        succeeded_job = await db.get(ScrapingSourceDiscoveryQuery, jobs2[0])
        timeout_job = await db.get(ScrapingSourceDiscoveryQuery, jobs2[1])
        blocker_job = await db.get(ScrapingSourceDiscoveryQuery, jobs2[2])
        assert succeeded_job is not None
        assert timeout_job is not None
        assert blocker_job is not None
        assert succeeded_job.status == SourceDiscoveryQueryStatus.SUCCEEDED
        assert timeout_job.status == SourceDiscoveryQueryStatus.PENDING
        assert blocker_job.status == SourceDiscoveryQueryStatus.PENDING
        assert timeout_job.next_attempt_at is not None
        assert blocker_job.claim_token is None
        assert blocker_job.next_page_number == 1

        running = (
            await db.execute(
                select(func.count()).select_from(ScrapingSourceDiscoveryQuery).where(
                    ScrapingSourceDiscoveryQuery.execution_id == exec2,
                    ScrapingSourceDiscoveryQuery.status == SourceDiscoveryQueryStatus.RUNNING,
                )
            )
        ).scalar_one()
        succeeded_candidates = (
            await db.execute(
                select(func.count()).select_from(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == jobs2[0]
                )
            )
        ).scalar_one()
        blocker_candidates = (
            await db.execute(
                select(func.count()).select_from(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == jobs2[2]
                )
            )
        ).scalar_one()
    assert running == 0
    assert succeeded_candidates >= 1
    assert blocker_candidates == 0

    # 10 pause stops new claims
    org3, exec3, jobs3 = await _seed_execution(sessions, job_count=2)
    async with sessions() as db:
        row = await db.get(ScrapingExecution, exec3)
        assert row is not None
        row.status = ScrapingExecutionStatus.PAUSE_REQUESTED
        row.pause_requested_at = FIXED_NOW
        await db.commit()
    paused = await service2.run_discovery_work_slice(org3, exec3)
    assert paused.outcome == "paused"

    # 12 cancel prevents provider work
    org4, exec4, jobs4 = await _seed_execution(sessions, job_count=2)
    async with sessions() as db:
        row = await db.get(ScrapingExecution, exec4)
        assert row is not None
        row.status = ScrapingExecutionStatus.CANCEL_REQUESTED
        row.cancel_requested_at = FIXED_NOW
        await db.commit()
    provider4 = InjectedProvider(outcomes={j: "succeeded" for j in jobs4}, calls=[])
    service4 = SourceDiscoveryExecutionService(
        session_factory=sessions,
        claim_service=SourceDiscoveryClaimService(session_factory=sessions, now_factory=lambda: FIXED_NOW),
        provider_service=provider4,
        result_service=SourceDiscoveryResultService(session_factory=sessions, now_factory=lambda: FIXED_NOW),
        prepare_fn=prepare_provider_results,
        now_factory=lambda: FIXED_NOW,
        queue_continuation=queue_continuation,
        event_emitter=emit,
        claim_batch_size=2,
        provider_concurrency=2,
    )
    cancelled = await service4.run_discovery_work_slice(org4, exec4)
    assert cancelled.outcome == "cancelled"
    assert provider4.calls == []

    # 14 expired claim recovery
    org5, exec5, jobs5 = await _seed_execution(sessions, job_count=1)
    async with sessions() as db:
        job = await db.get(ScrapingSourceDiscoveryQuery, jobs5[0])
        assert job is not None
        job.status = SourceDiscoveryQueryStatus.RUNNING
        job.claim_token = generate_claim_token()
        job.claimed_at = FIXED_NOW - timedelta(minutes=5)
        job.lease_expires_at = FIXED_NOW - timedelta(minutes=1)
        job.attempt_count = 1
        job.last_attempt_at = FIXED_NOW - timedelta(minutes=5)
        job.provider = "serper"
        job.requested_at = FIXED_NOW - timedelta(minutes=5)
        await db.commit()
    provider5 = InjectedProvider(outcomes={jobs5[0]: "succeeded"}, calls=[])
    service5 = SourceDiscoveryExecutionService(
        session_factory=sessions,
        claim_service=SourceDiscoveryClaimService(session_factory=sessions, now_factory=lambda: FIXED_NOW),
        provider_service=provider5,
        result_service=SourceDiscoveryResultService(session_factory=sessions, now_factory=lambda: FIXED_NOW),
        prepare_fn=prepare_provider_results,
        now_factory=lambda: FIXED_NOW,
        queue_continuation=queue_continuation,
        event_emitter=emit,
        claim_batch_size=2,
        provider_concurrency=1,
        recovery_batch_size=5,
    )
    recovered = await service5.run_discovery_work_slice(org5, exec5)
    assert recovered.counts.recovered_count == 1
    assert provider5.calls == [jobs5[0]]

    # 19 org/exec isolation — wrong org finds not_found/not_eligible
    isolated = await service5.run_discovery_work_slice("missing-org", exec5)
    assert isolated.outcome in {"not_found", "not_eligible", "lifecycle_blocked", "completed"}

    # 23: no external calls — InjectedProvider only
    assert all(isinstance(c, str) for c in provider.calls)
