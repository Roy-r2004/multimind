"""PostgreSQL concurrency coverage for Phase 4 Slice 3 claim/lease/retry lifecycle.

Ephemeral-DB harness (same pattern as migration 028/029 tests):
``POSTGRES_TEST_ADMIN_URL`` is used only to CREATE/DROP a unique
``claims_slice3_*`` database; Alembic never targets the admin/development database.

Normal run (injects the admin URL via ``docker-compose.test.yml``):

  docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api-test \\
    pytest -q tests/test_phase4_discovery_claims_postgres.py

Do not run this file outside Docker/Postgres — it fails closed without POSTGRES_TEST_ADMIN_URL.
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
from urllib.parse import urlparse

import asyncpg
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    Organization,
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingMission,
    ScrapingMissionStatus,
    ScrapingSourceDiscoveryQuery,
    SourceDiscoveryQueryStatus,
    User,
)
from app.services.scraping.source_discovery_claim_service import (
    LEASE_EXPIRED_ERROR_CODE,
    SourceDiscoveryClaimService,
    fixed_backoff_policy,
    immediate_retry_policy,
)

_FORBIDDEN_TARGET_DATABASES = frozenset(
    {
        "multiai",
        "multiai_scraping_test",
        "postgres",
        "template0",
        "template1",
    }
)
_EPHEMERAL_DB_RE = re.compile(r"^claims_slice3_[0-9a-f]{32}$")

PLAN_HASH = "c" * 64
FIXED_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)


@dataclass
class PostgresClaimsDatabase:
    admin: asyncpg.Connection
    database: str
    url: str

    async def alembic(self, *arguments: str) -> str:
        target = urlparse(self.url).path.lstrip("/")
        if target != self.database or not _EPHEMERAL_DB_RE.fullmatch(target):
            pytest.fail(
                "Refusing alembic: target is not an isolated claims_slice3_* test database."
            )
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
        pytest.fail("POSTGRES_TEST_ADMIN_URL is required for PostgreSQL claim coverage.")

    database = f"claims_slice3_{uuid.uuid4().hex}"
    if not _EPHEMERAL_DB_RE.fullmatch(database):
        pytest.fail("Refusing to proceed: ephemeral database name is not isolated.")
    if database in _FORBIDDEN_TARGET_DATABASES:
        pytest.fail("Refusing to proceed: ephemeral name collides with a forbidden database.")

    admin = await asyncpg.connect(admin_url)
    await admin.execute(f'CREATE DATABASE "{database}"')
    url = admin_url.rsplit("/", 1)[0] + f"/{database}"
    target = urlparse(url).path.lstrip("/")
    if target != database or target in _FORBIDDEN_TARGET_DATABASES:
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
        await admin.close()
        pytest.fail("Refusing to proceed: claims URL does not target the ephemeral test DB.")

    db = PostgresClaimsDatabase(admin=admin, database=database, url=url)
    engine = None
    try:
        await db.alembic("upgrade", "head")
        engine = create_async_engine(
            db.url.replace("postgresql://", "postgresql+asyncpg://")
        )
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        yield maker
    finally:
        if engine is not None:
            await engine.dispose()
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
            database,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
        await admin.close()


def _service(
    maker: async_sessionmaker[AsyncSession],
    *,
    now: datetime = FIXED_NOW,
) -> SourceDiscoveryClaimService:
    return SourceDiscoveryClaimService(
        session_factory=maker,
        now_factory=lambda: now,
        default_lease_duration=timedelta(seconds=60),
        default_retry_policy=fixed_backoff_policy(timedelta(seconds=30)),
        default_recovery_policy=immediate_retry_policy(),
    )


async def _seed_v2_campaign(
    session: AsyncSession,
    *,
    status: ScrapingExecutionStatus = ScrapingExecutionStatus.RUNNING,
    org: Organization | None = None,
    pause_requested_at: datetime | None = None,
    cancel_requested_at: datetime | None = None,
    paused_at: datetime | None = None,
    completed_at: datetime | None = None,
    schema_version: str = "2",
    execution_type: str = "mission_campaign",
) -> tuple[Organization, ScrapingExecution]:
    """Seed a valid v2 mission-campaign execution (not an invalid simplified stub)."""
    if org is None:
        org = Organization(
            name="Claims org",
            slug=f"claims-{uuid.uuid4().hex[:8]}",
        )
        session.add(org)
        await session.flush()
    user = User(
        email=f"{uuid.uuid4().hex}@example.test",
        hashed_password="x",
        full_name="Claims Tester",
    )
    session.add(user)
    await session.flush()
    mission = ScrapingMission(
        org_id=org.id,
        created_by=user.id,
        model_set_id="claims-set",
        title="Claims mission",
        original_prompt="Find rehab facilities",
        country_code="LB",
        country_name="Lebanon",
        country_iso3="LBN",
        continent="Asia",
        status=ScrapingMissionStatus.APPROVED,
    )
    session.add(mission)
    await session.flush()
    blueprint = ScrapingBlueprint(
        mission_id=mission.id,
        version=1,
        status=ScrapingBlueprintStatus.APPROVED,
        blueprint_json={"title": "Claims blueprint"},
        structured_blueprint={
            "schema_version": "2",
            "country_code": "LB",
            "country_name": "Lebanon",
        },
        model_set_id="claims-set",
    )
    session.add(blueprint)
    await session.flush()
    mission.active_blueprint_id = blueprint.id
    execution = ScrapingExecution(
        organization_id=org.id,
        mission_id=mission.id,
        blueprint_id=blueprint.id,
        team_plan_id=None,
        execution_type=execution_type,
        mode="mock",
        execution_origin="mission_campaign_mock",
        blueprint_version_snapshot=1,
        frozen_execution_plan_json={
            "schema_version": schema_version,
            "country_code": "LB",
            "country_name": "Lebanon",
            "regions": ["Beirut"],
            "languages": ["English"],
        },
        execution_plan_schema_version=schema_version,
        execution_plan_hash=PLAN_HASH,
        execution_plan_compiled_at=FIXED_NOW,
        status=status,
        country_code="LB",
        country_name="Lebanon",
        started_at=FIXED_NOW if status == ScrapingExecutionStatus.RUNNING else None,
        pause_requested_at=pause_requested_at,
        cancel_requested_at=cancel_requested_at,
        paused_at=paused_at,
        completed_at=completed_at,
    )
    session.add(execution)
    await session.flush()
    return org, execution


async def _add_pending_query(
    session: AsyncSession,
    *,
    org_id: str,
    execution_id: str,
    priority: int = 100,
    generation_ordinal: int = 0,
    fingerprint: str | None = None,
    next_attempt_at: datetime | None = None,
    query_text: str = "rehab Beirut",
    attempt_count: int = 0,
) -> ScrapingSourceDiscoveryQuery:
    row = ScrapingSourceDiscoveryQuery(
        organization_id=org_id,
        execution_id=execution_id,
        country_code="LB",
        country_name="Lebanon",
        region_name="Beirut",
        language_code="en",
        language_name="English",
        source_category="directory",
        query_text=query_text,
        status=SourceDiscoveryQueryStatus.PENDING,
        result_count=0,
        metadata_json={},
        purpose="seed_source_discovery",
        priority=priority,
        discovery_round=1,
        generation_ordinal=generation_ordinal,
        scope_level="region",
        query_job_fingerprint=fingerprint or uuid.uuid4().hex,
        plan_hash_snapshot=PLAN_HASH,
        important_city=None,
        next_attempt_at=next_attempt_at,
        attempt_count=attempt_count,
    )
    session.add(row)
    await session.flush()
    return row


async def _reload(
    session: AsyncSession, query_id: str
) -> ScrapingSourceDiscoveryQuery:
    row = await session.get(ScrapingSourceDiscoveryQuery, query_id)
    assert row is not None
    return row


# --- Coverage 1–10: ordering, batching, eligibility, claim fields ------------


@pytest.mark.asyncio
async def test_01_to_10_claim_ordering_batching_and_fields(postgres_sessions) -> None:
    async with postgres_sessions() as session:
        org, execution = await _seed_v2_campaign(session)
        # Intentionally unsorted insert order.
        q_low = await _add_pending_query(
            session, org_id=org.id, execution_id=execution.id, priority=50, generation_ordinal=2
        )
        q_mid = await _add_pending_query(
            session, org_id=org.id, execution_id=execution.id, priority=10, generation_ordinal=5
        )
        q_high = await _add_pending_query(
            session, org_id=org.id, execution_id=execution.id, priority=10, generation_ordinal=1
        )
        q_future = await _add_pending_query(
            session,
            org_id=org.id,
            execution_id=execution.id,
            priority=1,
            generation_ordinal=0,
            next_attempt_at=FIXED_NOW + timedelta(hours=1),
        )
        q_past = await _add_pending_query(
            session,
            org_id=org.id,
            execution_id=execution.id,
            priority=5,
            generation_ordinal=0,
            next_attempt_at=FIXED_NOW - timedelta(minutes=1),
        )
        q_null = await _add_pending_query(
            session,
            org_id=org.id,
            execution_id=execution.id,
            priority=5,
            generation_ordinal=1,
            next_attempt_at=None,
        )
        await session.commit()
        org_id, execution_id = org.id, execution.id
        ids = {
            "low": q_low.id,
            "mid": q_mid.id,
            "high": q_high.id,
            "future": q_future.id,
            "past": q_past.id,
            "null": q_null.id,
        }
        fingerprints = {
            q_low.id: q_low.query_job_fingerprint,
            q_mid.id: q_mid.query_job_fingerprint,
            q_high.id: q_high.query_job_fingerprint,
            q_future.id: q_future.query_job_fingerprint,
            q_past.id: q_past.query_job_fingerprint,
            q_null.id: q_null.query_job_fingerprint,
        }

    svc = _service(postgres_sessions)

    # Eligible ASC order: past(5,0), null(5,1), high(10,1), mid(10,5), low(50,2).
    # q_future(priority=1) is ineligible until next_attempt_at is due.
    # (1)(5)(6)(7) First batch: priority/ordinal ASC among eligible only.
    batch1 = await svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="serper",
        batch_size=2,
    )
    assert batch1.outcome == "claimed"
    assert batch1.claimed_count == 2
    assert [j.id for j in batch1.jobs] == [ids["past"], ids["null"]]
    # (2) batch size respected
    assert len(batch1.jobs) == 2
    # (7) future next_attempt_at excluded; NULL and past next_attempt_at eligible
    assert ids["future"] not in {j.id for j in batch1.jobs}
    assert ids["high"] not in {j.id for j in batch1.jobs}
    assert ids["mid"] not in {j.id for j in batch1.jobs}
    assert ids["low"] not in {j.id for j in batch1.jobs}
    # (8)(9) claim fields + fresh tokens
    tokens = set()
    for job in batch1.jobs:
        assert job.provider == "serper"
        assert job.requested_at == FIXED_NOW
        assert job.claimed_at == FIXED_NOW
        assert job.lease_expires_at == FIXED_NOW + timedelta(seconds=60)
        assert job.attempt_count == 1
        assert job.last_attempt_at == FIXED_NOW
        assert len(job.claim_token) == 36
        assert job.query_job_fingerprint == fingerprints[job.id]
        tokens.add(job.claim_token)
    assert len(tokens) == 2
    assert batch1.jobs[0].priority == 5
    assert batch1.jobs[0].generation_ordinal == 0
    assert batch1.jobs[1].priority == 5
    assert batch1.jobs[1].generation_ordinal == 1

    # (4) Remaining eligible jobs continue across repeated batches — no truncation.
    batch2 = await svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="serper",
        batch_size=2,
    )
    assert batch2.outcome == "claimed"
    assert batch2.claimed_count == 2
    assert [j.id for j in batch2.jobs] == [ids["high"], ids["mid"]]
    assert batch2.jobs[0].priority == 10
    assert batch2.jobs[0].generation_ordinal == 1
    assert batch2.jobs[1].priority == 10
    assert batch2.jobs[1].generation_ordinal == 5
    for job in batch2.jobs:
        assert job.provider == "serper"
        assert job.requested_at == FIXED_NOW
        assert job.attempt_count == 1
        assert job.query_job_fingerprint == fingerprints[job.id]
        assert len(job.claim_token) == 36

    batch3 = await svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="serper",
        batch_size=2,
    )
    assert batch3.outcome == "claimed"
    assert batch3.claimed_count == 1
    assert [j.id for j in batch3.jobs] == [ids["low"]]
    assert batch3.jobs[0].priority == 50
    assert batch3.jobs[0].generation_ordinal == 2
    assert batch3.jobs[0].attempt_count == 1
    assert batch3.jobs[0].provider == "serper"

    claimed_ids = (
        {j.id for j in batch1.jobs}
        | {j.id for j in batch2.jobs}
        | {j.id for j in batch3.jobs}
    )
    assert claimed_ids == {
        ids["past"],
        ids["null"],
        ids["high"],
        ids["mid"],
        ids["low"],
    }
    assert ids["future"] not in claimed_ids
    assert len(claimed_ids) == 5

    # Future job remains unclaimed while still not due.
    no_future = await svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="serper",
        batch_size=10,
    )
    assert no_future.outcome == "no_work"
    assert no_future.claimed_count == 0

    # (10) attempt_count increments after retry reclaim (first batch lead = q_past)
    first = batch1.jobs[0]
    assert first.id == ids["past"]
    requeue = await svc.requeue_retryable_failure(
        organization_id=org_id,
        execution_id=execution_id,
        query_job_id=first.id,
        claim_token=first.claim_token,
        error_code="provider_timeout",
        next_attempt_at=FIXED_NOW,
    )
    assert requeue.outcome == "applied"
    reclaim = await svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="brave",
        batch_size=1,
    )
    assert reclaim.outcome == "claimed"
    assert reclaim.claimed_count == 1
    assert reclaim.jobs[0].id == ids["past"]
    assert reclaim.jobs[0].attempt_count == 2
    assert reclaim.jobs[0].claim_token != first.claim_token
    assert reclaim.jobs[0].provider == "brave"
    assert reclaim.jobs[0].requested_at == FIXED_NOW
    assert reclaim.jobs[0].query_job_fingerprint == fingerprints[ids["past"]]

    # Succeeded path for reclaimed past job; future still excluded.
    succeeded = await svc.mark_succeeded(
        organization_id=org_id,
        execution_id=execution_id,
        query_job_id=reclaim.jobs[0].id,
        claim_token=reclaim.jobs[0].claim_token,
    )
    assert succeeded.outcome == "applied"
    assert succeeded.status == "succeeded"

    still_no_future = await svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="serper",
        batch_size=10,
    )
    assert still_no_future.outcome == "no_work"

    # (40) provenance unchanged
    async with postgres_sessions() as verify:
        for qid, fp in fingerprints.items():
            row = await _reload(verify, qid)
            assert row.query_job_fingerprint == fp
            assert row.plan_hash_snapshot == PLAN_HASH
        past_row = await _reload(verify, ids["past"])
        assert past_row.status == SourceDiscoveryQueryStatus.SUCCEEDED
        assert past_row.attempt_count == 2
        future_row = await _reload(verify, ids["future"])
        assert future_row.status == SourceDiscoveryQueryStatus.PENDING
        assert future_row.claim_token is None
        assert future_row.attempt_count == 0


@pytest.mark.asyncio
async def test_03_concurrent_claimers_are_disjoint(postgres_sessions) -> None:
    async with postgres_sessions() as session:
        org, execution = await _seed_v2_campaign(session)
        for i in range(20):
            await _add_pending_query(
                session,
                org_id=org.id,
                execution_id=execution.id,
                priority=i,
                generation_ordinal=i,
            )
        await session.commit()
        org_id, execution_id = org.id, execution.id

    svc = _service(postgres_sessions)

    async def claimer() -> set[str]:
        result = await svc.claim_eligible_jobs(
            organization_id=org_id,
            execution_id=execution_id,
            provider="serper",
            batch_size=8,
        )
        return {j.id for j in result.jobs}

    left, right = await asyncio.gather(claimer(), claimer())
    assert left.isdisjoint(right)
    assert len(left | right) == len(left) + len(right)


# --- Coverage 11–17: isolation + lifecycle gating ---------------------------


@pytest.mark.asyncio
async def test_11_to_17_isolation_and_lifecycle_gating(postgres_sessions) -> None:
    async with postgres_sessions() as session:
        org_a, exec_a = await _seed_v2_campaign(session)
        org_b, exec_b = await _seed_v2_campaign(session)
        await _add_pending_query(session, org_id=org_a.id, execution_id=exec_a.id)
        await _add_pending_query(session, org_id=org_b.id, execution_id=exec_b.id)

        paused_org, paused = await _seed_v2_campaign(
            session,
            status=ScrapingExecutionStatus.PAUSED,
            paused_at=FIXED_NOW,
            pause_requested_at=FIXED_NOW,
        )
        await _add_pending_query(session, org_id=paused_org.id, execution_id=paused.id)

        cancelled_org, cancelled = await _seed_v2_campaign(
            session,
            status=ScrapingExecutionStatus.CANCELLED,
            completed_at=FIXED_NOW,
            cancel_requested_at=FIXED_NOW,
        )
        await _add_pending_query(session, org_id=cancelled_org.id, execution_id=cancelled.id)

        completed_org, completed = await _seed_v2_campaign(
            session,
            status=ScrapingExecutionStatus.COMPLETED,
            completed_at=FIXED_NOW,
        )
        await _add_pending_query(session, org_id=completed_org.id, execution_id=completed.id)

        failed_org, failed = await _seed_v2_campaign(
            session,
            status=ScrapingExecutionStatus.FAILED,
            completed_at=FIXED_NOW,
        )
        await _add_pending_query(session, org_id=failed_org.id, execution_id=failed.id)

        # (17) cancel supersedes pause
        supersede_org, supersede = await _seed_v2_campaign(
            session,
            status=ScrapingExecutionStatus.PAUSE_REQUESTED,
            pause_requested_at=FIXED_NOW - timedelta(minutes=5),
            cancel_requested_at=FIXED_NOW,
        )
        await _add_pending_query(session, org_id=supersede_org.id, execution_id=supersede.id)

        # Second execution under same org — isolation by execution_id
        _, exec_a2 = await _seed_v2_campaign(session, org=org_a)
        q_a2 = await _add_pending_query(session, org_id=org_a.id, execution_id=exec_a2.id)
        await session.commit()
        ids = {
            "a": (org_a.id, exec_a.id),
            "b": (org_b.id, exec_b.id),
            "paused": (paused_org.id, paused.id),
            "cancelled": (cancelled_org.id, cancelled.id),
            "completed": (completed_org.id, completed.id),
            "failed": (failed_org.id, failed.id),
            "supersede": (supersede_org.id, supersede.id),
            "a2": (org_a.id, exec_a2.id, q_a2.id),
        }

    svc = _service(postgres_sessions)

    # (11) org isolation
    a_claim = await svc.claim_eligible_jobs(
        organization_id=ids["a"][0],
        execution_id=ids["a"][1],
        provider="serper",
        batch_size=10,
    )
    assert a_claim.claimed_count == 1
    wrong_org = await svc.claim_eligible_jobs(
        organization_id=ids["b"][0],
        execution_id=ids["a"][1],
        provider="serper",
        batch_size=10,
    )
    assert wrong_org.outcome == "lifecycle_blocked"
    assert wrong_org.lifecycle_reason == "not_found"

    # (12) execution isolation
    a2_claim = await svc.claim_eligible_jobs(
        organization_id=ids["a2"][0],
        execution_id=ids["a2"][1],
        provider="serper",
        batch_size=10,
    )
    assert a2_claim.claimed_count == 1
    assert a2_claim.jobs[0].id == ids["a2"][2]

    for key, reason in (
        ("paused", "paused"),
        ("cancelled", "cancelled"),
        ("completed", "completed"),
        ("failed", "failed"),
        ("supersede", "cancelled"),
    ):
        result = await svc.claim_eligible_jobs(
            organization_id=ids[key][0],
            execution_id=ids[key][1],
            provider="serper",
            batch_size=10,
        )
        assert result.outcome == "lifecycle_blocked"
        assert result.lifecycle_reason == reason
        assert result.claimed_count == 0


# --- Coverage 18–28: heartbeat / success / retry / terminal -----------------


@pytest.mark.asyncio
async def test_18_to_28_token_mutations(postgres_sessions) -> None:
    async with postgres_sessions() as session:
        org, execution = await _seed_v2_campaign(session)
        q1 = await _add_pending_query(
            session, org_id=org.id, execution_id=execution.id, priority=1, generation_ordinal=0
        )
        q2 = await _add_pending_query(
            session, org_id=org.id, execution_id=execution.id, priority=2, generation_ordinal=0
        )
        q3 = await _add_pending_query(
            session, org_id=org.id, execution_id=execution.id, priority=3, generation_ordinal=0
        )
        await session.commit()
        org_id, execution_id = org.id, execution.id
        q_ids = (q1.id, q2.id, q3.id)

    svc = _service(postgres_sessions)
    claimed = await svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="serper",
        batch_size=3,
    )
    j1, j2, j3 = claimed.jobs

    # (18) matching heartbeat
    hb = await svc.renew_claim(
        organization_id=org_id,
        execution_id=execution_id,
        query_job_id=j1.id,
        claim_token=j1.claim_token,
        lease_duration=timedelta(seconds=120),
    )
    assert hb.outcome == "applied"
    assert hb.lease_expires_at == FIXED_NOW + timedelta(seconds=120)

    # (19) stale heartbeat
    stale_hb = await svc.renew_claim(
        organization_id=org_id,
        execution_id=execution_id,
        query_job_id=j1.id,
        claim_token=str(uuid.uuid4()),
        lease_duration=timedelta(seconds=999),
    )
    assert stale_hb.outcome == "stale_claim"

    # (20) heartbeat cannot reduce lease
    long_lease_svc = SourceDiscoveryClaimService(
        session_factory=postgres_sessions,
        now_factory=lambda: FIXED_NOW + timedelta(seconds=10),
        default_lease_duration=timedelta(seconds=5),
    )
    no_reduce = await long_lease_svc.renew_claim(
        organization_id=org_id,
        execution_id=execution_id,
        query_job_id=j1.id,
        claim_token=j1.claim_token,
        lease_duration=timedelta(seconds=5),
    )
    assert no_reduce.outcome == "applied"
    assert no_reduce.lease_expires_at == FIXED_NOW + timedelta(seconds=120)

    # (21) matching success
    ok = await svc.mark_succeeded(
        organization_id=org_id,
        execution_id=execution_id,
        query_job_id=j1.id,
        claim_token=j1.claim_token,
    )
    assert ok.outcome == "applied"
    assert ok.status == "succeeded"

    # (23) repeated old-token success is non-applied / harmless
    again = await svc.mark_succeeded(
        organization_id=org_id,
        execution_id=execution_id,
        query_job_id=j1.id,
        claim_token=j1.claim_token,
    )
    assert again.outcome == "invalid_state"

    # (24)(25)(26) retryable failure
    retry = await svc.requeue_retryable_failure(
        organization_id=org_id,
        execution_id=execution_id,
        query_job_id=j2.id,
        claim_token=j2.claim_token,
        error_code="provider_rate_limited",
        next_attempt_at=FIXED_NOW + timedelta(minutes=5),
    )
    assert retry.outcome == "applied"
    assert retry.status == "pending"
    assert retry.next_attempt_at == FIXED_NOW + timedelta(minutes=5)
    assert retry.last_error_code == "provider_rate_limited"

    # Unrelated job still running / claimable path intact
    assert (
        await svc.mark_succeeded(
            organization_id=org_id,
            execution_id=execution_id,
            query_job_id=j3.id,
            claim_token=j3.claim_token,
        )
    ).outcome == "applied"

    # Future next_attempt_at blocks reclaim of j2 only
    blocked = await svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="serper",
        batch_size=5,
    )
    assert blocked.outcome == "no_work"

    # (27)(28) terminal failure
    # Reclaim j2 with a clock past next_attempt_at
    later_svc = SourceDiscoveryClaimService(
        session_factory=postgres_sessions,
        now_factory=lambda: FIXED_NOW + timedelta(minutes=6),
    )
    reclaimed = await later_svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="serper",
        batch_size=1,
    )
    assert reclaimed.jobs[0].id == j2.id
    terminal = await later_svc.mark_terminal_failure(
        organization_id=org_id,
        execution_id=execution_id,
        query_job_id=j2.id,
        claim_token=reclaimed.jobs[0].claim_token,
        error_code="malformed_provider_response",
    )
    assert terminal.outcome == "applied"
    assert terminal.status == "failed"

    still_none = await later_svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="serper",
        batch_size=5,
    )
    assert still_none.outcome == "no_work"

    async with postgres_sessions() as verify:
        row1 = await _reload(verify, q_ids[0])
        row2 = await _reload(verify, q_ids[1])
        row3 = await _reload(verify, q_ids[2])
        assert row1.status == SourceDiscoveryQueryStatus.SUCCEEDED
        assert row1.claim_token is None
        assert row2.status == SourceDiscoveryQueryStatus.FAILED
        assert row2.claim_token is None
        assert row3.status == SourceDiscoveryQueryStatus.SUCCEEDED
        # (35) no raw error text
        assert row2.last_error_code == "malformed_provider_response"
        assert row2.error_message is None or "Traceback" not in (row2.error_message or "")


# --- Coverage 22, 29–34: stale token after reclaim + lease recovery ---------


@pytest.mark.asyncio
async def test_22_and_29_to_34_lease_recovery(postgres_sessions) -> None:
    async with postgres_sessions() as session:
        org, execution = await _seed_v2_campaign(session)
        for i in range(4):
            await _add_pending_query(
                session,
                org_id=org.id,
                execution_id=execution.id,
                priority=i,
                generation_ordinal=0,
                attempt_count=0,
            )
        await session.commit()
        org_id, execution_id = org.id, execution.id

    claim_clock = FIXED_NOW
    svc = SourceDiscoveryClaimService(
        session_factory=postgres_sessions,
        now_factory=lambda: claim_clock,
        default_lease_duration=timedelta(seconds=30),
        default_recovery_policy=immediate_retry_policy(),
    )
    first = await svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="serper",
        batch_size=3,
    )
    assert first.claimed_count == 3
    old_tokens = {j.id: j.claim_token for j in first.jobs}
    valid_job = first.jobs[0]
    expired_jobs = first.jobs[1:]

    # Advance clock past lease for recovery service; keep one lease valid via heartbeat.
    recover_now = FIXED_NOW + timedelta(seconds=45)
    await svc.renew_claim(
        organization_id=org_id,
        execution_id=execution_id,
        query_job_id=valid_job.id,
        claim_token=valid_job.claim_token,
        lease_duration=timedelta(seconds=120),
        now=FIXED_NOW + timedelta(seconds=5),
    )

    recover_svc = SourceDiscoveryClaimService(
        session_factory=postgres_sessions,
        now_factory=lambda: recover_now,
        default_recovery_policy=immediate_retry_policy(),
    )

    # (31) concurrent recovery does not double-recover
    async def recover() -> tuple[str, ...]:
        result = await recover_svc.recover_expired_claims(
            organization_id=org_id,
            execution_id=execution_id,
            batch_size=10,
        )
        return result.recovered_ids

    left, right = await asyncio.gather(recover(), recover())
    recovered = set(left) | set(right)
    assert recovered == {j.id for j in expired_jobs}
    assert set(left).isdisjoint(set(right))
    assert len(left) + len(right) == len(recovered)

    # (30) valid lease not recovered
    assert valid_job.id not in recovered

    # (29)(32) recovered job claimable with new token; (10)/(34) attempt increments, no ceiling
    reclaim = await recover_svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="serper",
        batch_size=10,
    )
    reclaimed_map = {j.id: j for j in reclaim.jobs}
    for expired in expired_jobs:
        assert expired.id in reclaimed_map
        assert reclaimed_map[expired.id].attempt_count == 2
        assert reclaimed_map[expired.id].claim_token != old_tokens[expired.id]

    # (22)(33) stale pre-recovery / old token cannot succeed
    target = expired_jobs[0]
    stale = await recover_svc.mark_succeeded(
        organization_id=org_id,
        execution_id=execution_id,
        query_job_id=target.id,
        claim_token=old_tokens[target.id],
    )
    assert stale.outcome == "stale_claim"

    # High attempt_count still claimable — no artificial ceiling
    async with postgres_sessions() as session:
        row = await _reload(session, target.id)
        row.attempt_count = 10_000
        await session.commit()

    # Release current claim then reclaim
    await recover_svc.requeue_retryable_failure(
        organization_id=org_id,
        execution_id=execution_id,
        query_job_id=target.id,
        claim_token=reclaimed_map[target.id].claim_token,
        error_code="provider_unavailable",
        next_attempt_at=recover_now,
    )
    high = await recover_svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="serper",
        batch_size=1,
    )
    assert high.jobs[0].id == target.id
    assert high.jobs[0].attempt_count == 10_001

    async with postgres_sessions() as verify:
        for expired in expired_jobs:
            row = await _reload(verify, expired.id)
            if row.id == target.id:
                assert row.status == SourceDiscoveryQueryStatus.RUNNING
            # lease recovery set lease_expired at least once historically; latest may differ
            assert row.query_job_fingerprint is not None
            assert row.plan_hash_snapshot == PLAN_HASH


# --- Coverage 35–39: secrecy / TX boundary / no legacy or provider calls ----


@pytest.mark.asyncio
async def test_35_to_39_safety_and_transaction_boundary(postgres_sessions, monkeypatch) -> None:
    import app.services.scraping.source_discovery_claim_service as claim_mod

    # (37)(38) prove no planner / provider adapters are invoked via module surface
    assert not hasattr(claim_mod, "create_search_provider")
    assert not hasattr(claim_mod, "SourceDiscoveryQueryPlanner")
    assert not hasattr(claim_mod, "SourceDiscoveryService")
    monkeypatch.setattr(claim_mod, "supports_deterministic_query_generation", lambda v: v == "2")

    async with postgres_sessions() as session:
        org, execution = await _seed_v2_campaign(session)
        await _add_pending_query(session, org_id=org.id, execution_id=execution.id)
        await session.commit()
        org_id, execution_id = org.id, execution.id

    svc = _service(postgres_sessions)
    result = await svc.claim_eligible_jobs(
        organization_id=org_id,
        execution_id=execution_id,
        provider="serper",
        batch_size=1,
    )
    assert result.outcome == "claimed"
    job = result.jobs[0]
    # (36) TX finished: DTO is detached; another session can lock/update immediately
    assert type(job).__name__ == "ClaimedQueryJob"

    async with postgres_sessions() as other:
        locked = await other.execute(
            select(ScrapingSourceDiscoveryQuery)
            .where(ScrapingSourceDiscoveryQuery.id == job.id)
            .with_for_update()
        )
        row = locked.scalar_one()
        assert row.status == SourceDiscoveryQueryStatus.RUNNING
        candidate_count = await other.scalar(
            text(
                "SELECT count(*) FROM scraping_source_candidates "
                "WHERE discovery_query_id = :qid"
            ),
            {"qid": job.id},
        )
        assert int(candidate_count or 0) == 0
        await other.rollback()

    # (35) reject raw errors on retry path
    with pytest.raises(ValueError):
        await svc.requeue_retryable_failure(
            organization_id=org_id,
            execution_id=execution_id,
            query_job_id=job.id,
            claim_token=job.claim_token,
            error_code="Traceback (most recent call last): secret=abc",
        )

    applied = await svc.requeue_retryable_failure(
        organization_id=org_id,
        execution_id=execution_id,
        query_job_id=job.id,
        claim_token=job.claim_token,
        error_code="lease_expired",
    )
    assert applied.outcome == "applied"
    assert applied.last_error_code == "lease_expired"

    # (39) still no fabricated candidates / crawl nodes after lifecycle ops
    async with postgres_sessions() as verify:
        total = await verify.scalar(text("SELECT count(*) FROM scraping_source_candidates"))
        assert int(total or 0) == 0
        node_total = await verify.scalar(text("SELECT count(*) FROM scraping_crawl_nodes"))
        assert int(node_total or 0) == 0
