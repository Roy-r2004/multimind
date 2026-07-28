"""PostgreSQL persistence/concurrency coverage for Phase 4 Slice 5.

Ephemeral-DB harness (same pattern as Slice 3 claims / migration 029 tests):
``POSTGRES_TEST_ADMIN_URL`` is used only to CREATE/DROP a unique
``results_slice5_*`` database; Alembic never targets the admin/development database.

Normal run (injects the admin URL via ``docker-compose.test.yml``):

  docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api-test \\
    pytest -q tests/test_phase4_discovery_results_postgres.py

Do not run this file outside Docker/Postgres — it fails closed without POSTGRES_TEST_ADMIN_URL.
No real provider HTTP, external DNS, or development database access.
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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    CrawlNodeSourceClassification,
    Organization,
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingCrawlEdge,
    ScrapingCrawlNode,
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingMission,
    ScrapingMissionStatus,
    ScrapingSourceCandidate,
    ScrapingSourceDiscoveryQuery,
    SourceCandidateStatus,
    SourceDiscoveryQueryStatus,
    User,
)
from app.services.scraping.discovery_url_service import compute_canonical_url_hash
from app.services.scraping.source_discovery_claim_service import (
    ClaimedQueryJob,
    SourceDiscoveryClaimService,
    generate_claim_token,
)
from app.services.scraping.source_discovery_provider_service import (
    DiscoveryProviderExecutionResult,
    DiscoveryProviderResultItem,
)
from app.services.scraping.source_discovery_result_service import (
    PreparedDiscoveryBatch,
    PreparedDiscoveryResult,
    SourceDiscoveryResultService,
    prepare_provider_results,
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
_EPHEMERAL_DB_RE = re.compile(r"^results_slice5_[0-9a-f]{32}$")

PLAN_HASH = "c" * 64
FIXED_NOW = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
SAFE_URL = "https://docs.python.org/rehab"
SAFE_URL_B = "https://docs.python.org/other"


@dataclass
class PostgresResultsDatabase:
    admin: asyncpg.Connection
    database: str
    url: str

    async def alembic(self, *arguments: str) -> str:
        target = urlparse(self.url).path.lstrip("/")
        if target != self.database or not _EPHEMERAL_DB_RE.fullmatch(target):
            pytest.fail(
                "Refusing alembic: target is not an isolated results_slice5_* test database."
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
        pytest.fail("POSTGRES_TEST_ADMIN_URL is required for PostgreSQL result coverage.")

    database = f"results_slice5_{uuid.uuid4().hex}"
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
        pytest.fail("Refusing to proceed: results URL does not target the ephemeral test DB.")

    db = PostgresResultsDatabase(admin=admin, database=database, url=url)
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


def _result_service(
    maker: async_sessionmaker[AsyncSession],
    *,
    now: datetime = FIXED_NOW,
) -> SourceDiscoveryResultService:
    return SourceDiscoveryResultService(session_factory=maker, now_factory=lambda: now)


def _claim_service(
    maker: async_sessionmaker[AsyncSession],
    *,
    now: datetime = FIXED_NOW,
) -> SourceDiscoveryClaimService:
    return SourceDiscoveryClaimService(
        session_factory=maker,
        now_factory=lambda: now,
        default_lease_duration=timedelta(seconds=60),
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
    if org is None:
        org = Organization(
            name="Results org",
            slug=f"results-{uuid.uuid4().hex[:8]}",
        )
        session.add(org)
        await session.flush()
    user = User(
        email=f"{uuid.uuid4().hex}@example.test",
        hashed_password="x",
        full_name="Results Tester",
    )
    session.add(user)
    await session.flush()
    mission = ScrapingMission(
        org_id=org.id,
        created_by=user.id,
        model_set_id="results-set",
        title="Results mission",
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
        blueprint_json={"title": "Results blueprint"},
        structured_blueprint={
            "schema_version": "2",
            "country_code": "LB",
            "country_name": "Lebanon",
        },
        model_set_id="results-set",
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


async def _add_running_query(
    session: AsyncSession,
    *,
    org_id: str,
    execution_id: str,
    claim_token: str | None = None,
    fingerprint: str | None = None,
    query_text: str = "rehab Beirut",
    attempt_count: int = 1,
    generation_ordinal: int = 0,
    provider: str = "serper",
) -> ScrapingSourceDiscoveryQuery:
    token = claim_token or generate_claim_token()
    row = ScrapingSourceDiscoveryQuery(
        organization_id=org_id,
        execution_id=execution_id,
        country_code="LB",
        country_name="Lebanon",
        region_code="BEY",
        region_name="Beirut",
        language_code="en",
        language_name="English",
        source_category="directory",
        query_text=query_text,
        provider=provider,
        status=SourceDiscoveryQueryStatus.RUNNING,
        result_count=0,
        metadata_json={},
        purpose="seed_source_discovery",
        priority=100,
        discovery_round=1,
        generation_ordinal=generation_ordinal,
        scope_level="region",
        query_job_fingerprint=fingerprint or uuid.uuid4().hex,
        plan_hash_snapshot=PLAN_HASH,
        important_city=None,
        claim_token=token,
        claimed_at=FIXED_NOW,
        lease_expires_at=FIXED_NOW + timedelta(seconds=60),
        attempt_count=attempt_count,
        last_attempt_at=FIXED_NOW,
        requested_at=FIXED_NOW,
    )
    session.add(row)
    await session.flush()
    return row


def _claimed_from_row(row: ScrapingSourceDiscoveryQuery) -> ClaimedQueryJob:
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
    )


def _item(claimed: ClaimedQueryJob, *, url: str, title: str = "Title", snippet: str = "Snippet", rank: int = 1) -> DiscoveryProviderResultItem:
    return DiscoveryProviderResultItem(
        original_url=url,
        title=title,
        snippet=snippet,
        rank=rank,
        provider=claimed.provider,
        provider_result_type="organic",
        query_job_id=claimed.id,
        organization_id=claimed.organization_id,
        execution_id=claimed.execution_id,
        claim_token=claimed.claim_token,
        scope_level=claimed.scope_level,
        language_code=claimed.language_code,
        region_code=claimed.region_code,
        region_name=claimed.region_name,
        important_city=claimed.important_city,
        country_code=claimed.country_code,
        discovered_at=FIXED_NOW,
    )


def _provider_ok(
    claimed: ClaimedQueryJob,
    results: list[DiscoveryProviderResultItem],
) -> DiscoveryProviderExecutionResult:
    return DiscoveryProviderExecutionResult(
        outcome="succeeded",
        provider=claimed.provider,
        query_job_id=claimed.id,
        organization_id=claimed.organization_id,
        execution_id=claimed.execution_id,
        claim_token=claimed.claim_token,
        discovered_at=FIXED_NOW,
        results=tuple(results),
        raw_result_count=len(results),
        accepted_result_count=len(results),
        skipped_malformed_count=0,
        diagnostic_code="succeeded",
    )


def _prepare(
    claimed: ClaimedQueryJob,
    urls: list[str] | None = None,
    *,
    empty: bool = False,
) -> PreparedDiscoveryBatch:
    if empty:
        return prepare_provider_results(
            claimed, _provider_ok(claimed, []), clock=FIXED_NOW
        )
    items = [
        _item(claimed, url=u, title=f"T{i}", snippet=f"S{i}", rank=i + 1)
        for i, u in enumerate(urls or [SAFE_URL])
    ]
    return prepare_provider_results(claimed, _provider_ok(claimed, items), clock=FIXED_NOW)


# --- Coverage -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_01_to_08_persist_one_result_and_succeed(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id
        )
        fingerprint = query.query_job_fingerprint
        ordinal = query.generation_ordinal
        plan_hash = query.plan_hash_snapshot
        await session.commit()

    claimed = None
    async with maker() as session:
        row = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert row is not None
        claimed = _claimed_from_row(row)

    original = "https://Docs.Python.Org/rehab?utm_source=x"
    batch = prepare_provider_results(
        claimed,
        _provider_ok(claimed, [_item(claimed, url=original, title="Rehab", snippet="Dir", rank=1)]),
        clock=FIXED_NOW,
    )
    result = await _result_service(maker).persist_prepared_batch_and_succeed(batch)
    assert result.outcome == "applied"
    assert result.counts.query_marked_succeeded is True
    assert result.counts.candidate_inserted_count == 1
    assert result.counts.crawl_node_created_count == 1
    assert result.counts.crawl_edge_created_count == 0

    async with maker() as session:
        candidates = (
            await session.execute(
                select(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id
                )
            )
        ).scalars().all()
        nodes = (
            await session.execute(
                select(ScrapingCrawlNode).where(
                    ScrapingCrawlNode.execution_id == execution.id
                )
            )
        ).scalars().all()
        edges = (
            await session.execute(select(func.count()).select_from(ScrapingCrawlEdge))
        ).scalar_one()
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert job is not None
        assert len(candidates) == 1
        assert len(nodes) == 1
        assert edges == 0
        cand = candidates[0]
        node = nodes[0]
        assert cand.crawl_node_id == node.id
        assert cand.url == original
        assert cand.canonical_url == "https://docs.python.org/rehab"
        assert cand.title == "Rehab"
        assert cand.snippet == "Dir"
        assert cand.rank == 1
        assert cand.provider == "serper"
        assert cand.language_code == "en"
        assert cand.region_name == "Beirut"
        assert cand.country_code == "LB"
        assert cand.source_category == "directory"
        assert cand.status == SourceCandidateStatus.DISCOVERED
        assert job.status == SourceDiscoveryQueryStatus.SUCCEEDED
        assert job.claim_token is None
        assert job.claimed_at is None
        assert job.lease_expires_at is None
        assert job.query_job_fingerprint == fingerprint
        assert job.generation_ordinal == ordinal
        assert job.plan_hash_snapshot == plan_hash
        assert job.attempt_count == 1


@pytest.mark.asyncio
async def test_09_empty_provider_result_succeeds(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    batch = _prepare(claimed, empty=True)
    result = await _result_service(maker).persist_prepared_batch_and_succeed(batch)
    assert result.outcome == "applied"
    assert result.counts.candidate_inserted_count == 0
    assert result.counts.crawl_node_created_count == 0
    assert result.counts.query_marked_succeeded is True

    async with maker() as session:
        c = (
            await session.execute(
                select(func.count()).select_from(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id
                )
            )
        ).scalar_one()
        n = (
            await session.execute(
                select(func.count()).select_from(ScrapingCrawlNode).where(
                    ScrapingCrawlNode.execution_id == execution.id
                )
            )
        ).scalar_one()
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert c == 0 and n == 0
        assert job is not None and job.status == SourceDiscoveryQueryStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_10_11_multi_query_same_node_separate_candidates(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        q1 = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id, generation_ordinal=0
        )
        q2 = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id, generation_ordinal=1
        )
        await session.commit()
        c1 = _claimed_from_row(q1)
        c2 = _claimed_from_row(q2)

    r1 = await _result_service(maker).persist_prepared_batch_and_succeed(_prepare(c1))
    r2 = await _result_service(maker).persist_prepared_batch_and_succeed(_prepare(c2))
    assert r1.outcome == "applied" and r2.outcome == "applied"
    assert r1.counts.crawl_node_created_count == 1
    assert r2.counts.crawl_node_created_count == 0
    assert r2.counts.crawl_node_existing_count == 1

    async with maker() as session:
        nodes = (
            await session.execute(
                select(ScrapingCrawlNode).where(
                    ScrapingCrawlNode.execution_id == execution.id
                )
            )
        ).scalars().all()
        candidates = (
            await session.execute(
                select(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.execution_id == execution.id
                )
            )
        ).scalars().all()
        assert len(nodes) == 1
        assert len(candidates) == 2
        assert {c.discovery_query_id for c in candidates} == {q1.id, q2.id}
        assert all(c.crawl_node_id == nodes[0].id for c in candidates)


@pytest.mark.asyncio
async def test_12_13_separate_execution_and_org_nodes(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org_a, exec_a = await _seed_v2_campaign(session)
        org_b, exec_b = await _seed_v2_campaign(session)
        q_a1 = await _add_running_query(
            session, org_id=org_a.id, execution_id=exec_a.id
        )
        # Same org, different execution
        _, exec_a2 = await _seed_v2_campaign(session, org=org_a)
        q_a2 = await _add_running_query(
            session, org_id=org_a.id, execution_id=exec_a2.id
        )
        q_b = await _add_running_query(
            session, org_id=org_b.id, execution_id=exec_b.id
        )
        await session.commit()
        ca1 = _claimed_from_row(q_a1)
        ca2 = _claimed_from_row(q_a2)
        cb = _claimed_from_row(q_b)

    for claimed in (ca1, ca2, cb):
        result = await _result_service(maker).persist_prepared_batch_and_succeed(
            _prepare(claimed)
        )
        assert result.outcome == "applied"
        assert result.counts.crawl_node_created_count == 1

    async with maker() as session:
        total = (
            await session.execute(select(func.count()).select_from(ScrapingCrawlNode))
        ).scalar_one()
        assert total == 3


@pytest.mark.asyncio
async def test_14_15_idempotent_replay_no_dupes(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    batch = _prepare(claimed)
    first = await _result_service(maker).persist_prepared_batch_and_succeed(batch)
    second = await _result_service(maker).persist_prepared_batch_and_succeed(batch)
    assert first.outcome == "applied"
    assert second.outcome == "idempotent_replay"
    assert second.counts.candidate_inserted_count == 0
    assert second.counts.crawl_node_created_count == 0

    async with maker() as session:
        c = (
            await session.execute(
                select(func.count()).select_from(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id
                )
            )
        ).scalar_one()
        n = (
            await session.execute(
                select(func.count()).select_from(ScrapingCrawlNode).where(
                    ScrapingCrawlNode.execution_id == execution.id
                )
            )
        ).scalar_one()
        assert c == 1 and n == 1


@pytest.mark.asyncio
async def test_16_concurrent_node_upsert_converges(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        q1 = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id, generation_ordinal=0
        )
        q2 = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id, generation_ordinal=1
        )
        await session.commit()
        c1 = _claimed_from_row(q1)
        c2 = _claimed_from_row(q2)

    b1 = _prepare(c1)
    b2 = _prepare(c2)
    r1, r2 = await asyncio.gather(
        _result_service(maker).persist_prepared_batch_and_succeed(b1),
        _result_service(maker).persist_prepared_batch_and_succeed(b2),
    )
    assert {r1.outcome, r2.outcome} == {"applied"}
    async with maker() as session:
        n = (
            await session.execute(
                select(func.count()).select_from(ScrapingCrawlNode).where(
                    ScrapingCrawlNode.execution_id == execution.id
                )
            )
        ).scalar_one()
        c = (
            await session.execute(
                select(func.count()).select_from(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.execution_id == execution.id
                )
            )
        ).scalar_one()
        assert n == 1
        assert c == 2


@pytest.mark.asyncio
async def test_17_hash_collision_fails_safely(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    batch = _prepare(claimed)
    assert len(batch.results) == 1
    # Force a collision: same hash payload identity as prepared, but different URL string
    # by manually building a batch that claims the prepared hash for another URL.
    prepared = batch.results[0]
    colliding = PreparedDiscoveryResult(
        original_url="https://evil.example/other",
        canonical_url="https://evil.example/other",
        canonical_url_hash=prepared.canonical_url_hash,
        hostname="evil.example",
        domain="evil.example",
        source_classification=CrawlNodeSourceClassification.UNCLASSIFIED.value,
        classification_reason_code="insufficient_evidence",
        title="x",
        snippet="y",
        rank=1,
        provider="serper",
        provider_result_type="organic",
        discovered_at=FIXED_NOW,
        is_safe=True,
        safety_error_code=None,
        persist_candidate=True,
        persist_crawl_node=True,
    )
    # Seed the real node first.
    first = await _result_service(maker).persist_prepared_batch_and_succeed(batch)
    assert first.outcome == "applied"

    # New running query attempting colliding hash with different URL.
    async with maker() as session:
        q2 = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id, generation_ordinal=9
        )
        await session.commit()
        claimed2 = _claimed_from_row(q2)

    bad_batch = PreparedDiscoveryBatch(
        outcome="ready",
        organization_id=claimed2.organization_id,
        execution_id=claimed2.execution_id,
        query_job_id=claimed2.id,
        claim_token=claimed2.claim_token,
        provider=claimed2.provider,
        prepared_at=FIXED_NOW,
        results=(colliding,),
        country_code=claimed2.country_code,
        country_name=claimed2.country_name,
        region_code=claimed2.region_code,
        region_name=claimed2.region_name,
        language_code=claimed2.language_code,
        language_name=claimed2.language_name,
        source_category=claimed2.source_category,
        scope_level=claimed2.scope_level,
        important_city=claimed2.important_city,
        purpose=claimed2.purpose,
    )
    result = await _result_service(maker).persist_prepared_batch_and_succeed(bad_batch)
    assert result.outcome == "hash_collision"
    assert result.error_code == "canonical_url_hash_collision"
    assert prepared.canonical_url_hash not in (result.error_code or "")

    async with maker() as session:
        job = await session.get(ScrapingSourceDiscoveryQuery, q2.id)
        assert job is not None
        assert job.status == SourceDiscoveryQueryStatus.RUNNING
        assert job.claim_token == claimed2.claim_token
        c = (
            await session.execute(
                select(func.count()).select_from(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == q2.id
                )
            )
        ).scalar_one()
        assert c == 0


@pytest.mark.asyncio
async def test_18_to_21_stale_claim_blocked_reclaim_intact(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        token_a = generate_claim_token()
        query = await _add_running_query(
            session,
            org_id=org.id,
            execution_id=execution.id,
            claim_token=token_a,
        )
        await session.commit()

    # Worker A prepares with token A.
    async with maker() as session:
        row = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert row is not None
        claimed_a = _claimed_from_row(row)
    batch_a = _prepare(claimed_a)

    # Lease expires; worker B reclaims.
    claims = _claim_service(maker, now=FIXED_NOW + timedelta(minutes=5))
    recovered = await claims.recover_expired_claims(
        organization_id=org.id,
        execution_id=execution.id,
        batch_size=1,
        now=FIXED_NOW + timedelta(minutes=5),
    )
    assert recovered.recovered_count == 1
    claimed_batch = await claims.claim_eligible_jobs(
        organization_id=org.id,
        execution_id=execution.id,
        provider="serper",
        batch_size=1,
        now=FIXED_NOW + timedelta(minutes=5),
    )
    assert claimed_batch.claimed_count == 1
    token_b = claimed_batch.jobs[0].claim_token
    assert token_b != token_a

    # Worker A late persist with stale token A.
    stale = await _result_service(maker).persist_prepared_batch_and_succeed(batch_a)
    assert stale.outcome == "stale_claim"
    assert stale.counts.candidate_inserted_count == 0
    assert stale.counts.crawl_node_created_count == 0
    assert stale.counts.query_marked_succeeded is False

    async with maker() as session:
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert job is not None
        assert job.status == SourceDiscoveryQueryStatus.RUNNING
        assert job.claim_token == token_b
        c = (
            await session.execute(
                select(func.count()).select_from(ScrapingSourceCandidate)
            )
        ).scalar_one()
        n = (
            await session.execute(select(func.count()).select_from(ScrapingCrawlNode))
        ).scalar_one()
        assert c == 0 and n == 0


@pytest.mark.asyncio
async def test_22_cancelled_rejects_late_persistence(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(
            session, status=ScrapingExecutionStatus.CANCELLED
        )
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    result = await _result_service(maker).persist_prepared_batch_and_succeed(
        _prepare(claimed)
    )
    assert result.outcome == "lifecycle_blocked"
    assert result.error_code == "execution_cancelled"
    assert result.lifecycle_reason == "cancelled"


@pytest.mark.asyncio
async def test_23_completed_rejects_persistence(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(
            session,
            status=ScrapingExecutionStatus.COMPLETED,
            completed_at=FIXED_NOW,
        )
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    result = await _result_service(maker).persist_prepared_batch_and_succeed(
        _prepare(claimed)
    )
    assert result.outcome == "lifecycle_blocked"
    assert result.lifecycle_reason == "completed"


@pytest.mark.asyncio
async def test_24_failed_rejects_persistence(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(
            session, status=ScrapingExecutionStatus.FAILED
        )
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    result = await _result_service(maker).persist_prepared_batch_and_succeed(
        _prepare(claimed)
    )
    assert result.outcome == "lifecycle_blocked"
    assert result.lifecycle_reason == "failed"


@pytest.mark.asyncio
async def test_25_paused_allows_finalize_cancel_supersedes(postgres_sessions) -> None:
    maker = postgres_sessions
    # Pause allows finalize.
    async with maker() as session:
        org, execution = await _seed_v2_campaign(
            session,
            status=ScrapingExecutionStatus.PAUSED,
            pause_requested_at=FIXED_NOW,
            paused_at=FIXED_NOW,
        )
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id
        )
        await session.commit()
        claimed = _claimed_from_row(query)
    ok = await _result_service(maker).persist_prepared_batch_and_succeed(_prepare(claimed))
    assert ok.outcome == "applied"

    # Cancel supersedes pause.
    async with maker() as session:
        org2, execution2 = await _seed_v2_campaign(
            session,
            status=ScrapingExecutionStatus.PAUSE_REQUESTED,
            pause_requested_at=FIXED_NOW,
            cancel_requested_at=FIXED_NOW + timedelta(seconds=1),
        )
        query2 = await _add_running_query(
            session, org_id=org2.id, execution_id=execution2.id
        )
        await session.commit()
        claimed2 = _claimed_from_row(query2)
    blocked = await _result_service(maker).persist_prepared_batch_and_succeed(
        _prepare(claimed2)
    )
    assert blocked.outcome == "lifecycle_blocked"
    assert blocked.lifecycle_reason == "cancelled"


@pytest.mark.asyncio
async def test_26_27_injected_failure_rolls_back(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    batch = _prepare(claimed)
    service = _result_service(maker)

    async def boom(*_a, **_k):
        raise RuntimeError("injected failure")

    service._upsert_crawl_node = boom  # type: ignore[method-assign]
    result = await service.persist_prepared_batch_and_succeed(batch)
    assert result.outcome == "database_failure"

    async with maker() as session:
        c = (
            await session.execute(select(func.count()).select_from(ScrapingSourceCandidate))
        ).scalar_one()
        n = (
            await session.execute(select(func.count()).select_from(ScrapingCrawlNode))
        ).scalar_one()
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert c == 0 and n == 0
        assert job is not None
        assert job.status == SourceDiscoveryQueryStatus.RUNNING
        assert job.claim_token == claimed.claim_token


@pytest.mark.asyncio
async def test_28_29_composite_fk_blocks_cross_linkage(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org_a, exec_a = await _seed_v2_campaign(session)
        org_b, exec_b = await _seed_v2_campaign(session)
        q_a = await _add_running_query(
            session, org_id=org_a.id, execution_id=exec_a.id
        )
        q_b = await _add_running_query(
            session, org_id=org_b.id, execution_id=exec_b.id
        )
        await session.commit()
        claimed_a = _claimed_from_row(q_a)

    await _result_service(maker).persist_prepared_batch_and_succeed(_prepare(claimed_a))

    async with maker() as session:
        node = (
            await session.execute(
                select(ScrapingCrawlNode).where(
                    ScrapingCrawlNode.execution_id == exec_a.id
                )
            )
        ).scalar_one()
        # Cross-org and cross-execution candidate→node assignment must fail at DB.
        with pytest.raises(Exception):
            async with session.begin():
                session.add(
                    ScrapingSourceCandidate(
                        organization_id=org_b.id,
                        execution_id=exec_b.id,
                        discovery_query_id=q_b.id,
                        crawl_node_id=node.id,
                        provider="serper",
                        rank=1,
                        url=SAFE_URL,
                        canonical_url="https://docs.python.org/rehab",
                        domain="docs.python.org",
                        title="x",
                        snippet="y",
                        country_code="LB",
                        country_name="Lebanon",
                        language_code="en",
                        language_name="English",
                        source_category="directory",
                        initial_relevance_score=1,
                        initial_trust_tier="medium",
                        status=SourceCandidateStatus.DISCOVERED,
                        discovered_at=FIXED_NOW,
                        metadata_json={},
                    )
                )
                await session.flush()


@pytest.mark.asyncio
async def test_30_classification_does_not_downgrade(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        q1 = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id, generation_ordinal=0
        )
        q2 = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id, generation_ordinal=1
        )
        await session.commit()
        c1 = _claimed_from_row(q1)
        c2 = _claimed_from_row(q2)

    # First discovery: government.
    gov_url = "https://health.gov/facilities"
    b1 = prepare_provider_results(
        c1, _provider_ok(c1, [_item(c1, url=gov_url)]), clock=FIXED_NOW
    )
    assert b1.results[0].source_classification == "government_source"
    await _result_service(maker).persist_prepared_batch_and_succeed(b1)

    # Second: ambiguous title for same canonical host path → unclassified merge must keep gov.
    b2 = prepare_provider_results(
        c2, _provider_ok(c2, [_item(c2, url=gov_url, title="misc")]), clock=FIXED_NOW
    )
    await _result_service(maker).persist_prepared_batch_and_succeed(b2)

    async with maker() as session:
        node = (
            await session.execute(
                select(ScrapingCrawlNode).where(
                    ScrapingCrawlNode.execution_id == execution.id
                )
            )
        ).scalar_one()
        assert node.source_classification == CrawlNodeSourceClassification.GOVERNMENT_SOURCE


@pytest.mark.asyncio
async def test_31_no_synthetic_edges(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    result = await _result_service(maker).persist_prepared_batch_and_succeed(
        _prepare(claimed, [SAFE_URL, SAFE_URL_B])
    )
    assert result.counts.crawl_edge_created_count == 0
    async with maker() as session:
        edges = (
            await session.execute(select(func.count()).select_from(ScrapingCrawlEdge))
        ).scalar_one()
        nodes = (
            await session.execute(
                select(func.count()).select_from(ScrapingCrawlNode).where(
                    ScrapingCrawlNode.execution_id == execution.id
                )
            )
        ).scalar_one()
        assert edges == 0
        assert nodes == 2


@pytest.mark.asyncio
async def test_32_to_38_invariants_counts_and_no_fabrications(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id
        )
        fingerprint = query.query_job_fingerprint
        await session.commit()
        claimed = _claimed_from_row(query)

    # Include tracking duplicate + one distinct URL.
    batch = prepare_provider_results(
        claimed,
        _provider_ok(
            claimed,
            [
                _item(claimed, url=f"{SAFE_URL}?utm_source=a", rank=1),
                _item(claimed, url=SAFE_URL, rank=2),
                _item(claimed, url=SAFE_URL_B, rank=3),
            ],
        ),
        clock=FIXED_NOW,
    )
    assert batch.duplicate_within_query_count == 1
    result = await _result_service(maker).persist_prepared_batch_and_succeed(batch)
    assert result.outcome == "applied"
    assert result.counts.candidate_inserted_count == 2
    assert result.counts.crawl_node_created_count == 2
    assert result.counts.persisted_count == 2
    assert result.counts.crawl_edge_created_count == 0

    async with maker() as session:
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert job is not None
        assert job.query_job_fingerprint == fingerprint
        assert job.plan_hash_snapshot == PLAN_HASH
        c = (
            await session.execute(
                select(func.count()).select_from(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id
                )
            )
        ).scalar_one()
        n = (
            await session.execute(
                select(func.count()).select_from(ScrapingCrawlNode).where(
                    ScrapingCrawlNode.execution_id == execution.id
                )
            )
        ).scalar_one()
        assert c == result.counts.candidate_inserted_count
        assert n == result.counts.crawl_node_created_count
        # No fabricated placeholder URLs.
        urls = (
            await session.execute(
                select(ScrapingSourceCandidate.canonical_url).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id
                )
            )
        ).scalars().all()
        assert all(u.startswith("https://docs.python.org/") for u in urls)
        assert compute_canonical_url_hash(urls[0])  # hash helper still works; not exposed


@pytest.mark.asyncio
async def test_unsafe_persists_candidate_without_crawl_node(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    batch = prepare_provider_results(
        claimed,
        _provider_ok(claimed, [_item(claimed, url="http://127.0.0.1/private")]),
        clock=FIXED_NOW,
    )
    assert batch.results[0].persist_crawl_node is False
    result = await _result_service(maker).persist_prepared_batch_and_succeed(batch)
    assert result.outcome == "applied"
    assert result.counts.candidate_inserted_count == 1
    assert result.counts.crawl_node_created_count == 0

    async with maker() as session:
        cand = (
            await session.execute(
                select(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id
                )
            )
        ).scalar_one()
        n = (
            await session.execute(select(func.count()).select_from(ScrapingCrawlNode))
        ).scalar_one()
        assert cand.crawl_node_id is None
        assert cand.status == SourceCandidateStatus.REJECTED
        assert "10." not in str(cand.metadata_json)
        assert "127.0.0.1" not in str(cand.metadata_json.get("discovery_rejection", ""))
        assert cand.metadata_json.get("discovery_rejection") == "unsafe_result_url"
        assert n == 0
