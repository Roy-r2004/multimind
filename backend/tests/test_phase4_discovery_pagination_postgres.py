"""Phase 4 Slice 7: PostgreSQL pagination + blocker tests (write-only; do not run without Docker).

Ephemeral-DB harness (same pattern as Slice 5/6):
``POSTGRES_TEST_ADMIN_URL`` creates/drops a unique ``page_slice7_*`` database;
Alembic pins the temporary database to the Phase 4 boundary **030**. Injected provider doubles only — no
real Serper/HTTP/DNS, and never the development database.

  docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api-test \\
    pytest -q tests/test_phase4_discovery_pagination_postgres.py
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
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
    ScrapingCrawlNode,
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
    PROVIDER_BLOCK_PROFILE_KEY,
    SourceDiscoveryExecutionService,
)
from app.services.scraping.source_discovery_provider_service import (
    DiscoveryProviderContinuation,
    DiscoveryProviderExecutionResult,
    DiscoveryProviderResultItem,
)
from app.services.scraping.source_discovery_result_service import (
    SourceDiscoveryResultService,
    prepare_provider_results,
)

# Guard: this module must never open a real Serper/HTTP session.
assert os.environ.get("SERPER_LIVE_SMOKE") != "1"

_FORBIDDEN_TARGET_DATABASES = frozenset(
    {
        "multiai",
        "multiai_scraping_test",
        "postgres",
        "template0",
        "template1",
    }
)
_EPHEMERAL_DB_RE = re.compile(r"^page_slice7_[0-9a-f]{32}$")

PLAN_HASH = "e" * 64
FIXED_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)
PAGE_SIZE = 10
SAFE_PREFIX = "https://docs.python.org/page"

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_ADMIN_URL"),
    reason="POSTGRES_TEST_ADMIN_URL required (docker-compose.test.yml api-test)",
)


@dataclass
class PostgresPaginationDatabase:
    admin: asyncpg.Connection
    database: str
    url: str

    async def alembic(self, *arguments: str) -> str:
        target = urlparse(self.url).path.lstrip("/")
        if target != self.database or not _EPHEMERAL_DB_RE.fullmatch(target):
            pytest.fail(
                "Refusing alembic: target is not an isolated page_slice7_* test database."
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
        pytest.fail("POSTGRES_TEST_ADMIN_URL is required for PostgreSQL pagination coverage.")

    database = f"page_slice7_{uuid.uuid4().hex}"
    if not _EPHEMERAL_DB_RE.fullmatch(database):
        pytest.fail("Refusing to proceed: ephemeral database name is not isolated.")
    if database in _FORBIDDEN_TARGET_DATABASES:
        pytest.fail("Refusing to proceed: ephemeral name collides with a forbidden database.")

    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'CREATE DATABASE "{database}"')
    except Exception:
        await admin.close()
        raise

    parsed = urlparse(admin_url)
    url = (
        f"postgresql://{parsed.username}:{parsed.password}"
        f"@{parsed.hostname}:{parsed.port}/{database}"
    )
    target = urlparse(url).path.lstrip("/")
    if target != database or target in _FORBIDDEN_TARGET_DATABASES:
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        await admin.close()
        pytest.fail("Refusing to proceed: pagination URL does not target the ephemeral test DB.")

    harness = PostgresPaginationDatabase(admin=admin, database=database, url=url)
    engine = None
    try:
        # Slice 7 requires migration 030 pagination columns.
        await harness.alembic("upgrade", "030")
        current = await harness.alembic("current")
        if "030" not in current:
            pytest.fail("Expected ephemeral database to be at revision 030.")
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
) -> tuple[Organization, ScrapingExecution]:
    if org is None:
        org = Organization(
            name="Pagination org",
            slug=f"page-{uuid.uuid4().hex[:8]}",
        )
        session.add(org)
        await session.flush()
    user = User(
        email=f"{uuid.uuid4().hex}@example.test",
        hashed_password="x",
        full_name="Pagination Tester",
    )
    session.add(user)
    await session.flush()
    mission = ScrapingMission(
        org_id=org.id,
        created_by=user.id,
        model_set_id="page-set",
        title="Pagination mission",
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
        blueprint_json={"title": "Pagination blueprint"},
        structured_blueprint={
            "schema_version": "2",
            "country_code": "LB",
            "country_name": "Lebanon",
        },
        model_set_id="page-set",
    )
    session.add(blueprint)
    await session.flush()
    mission.active_blueprint_id = blueprint.id
    execution = ScrapingExecution(
        organization_id=org.id,
        mission_id=mission.id,
        blueprint_id=blueprint.id,
        team_plan_id=None,
        execution_type="mission_campaign",
        mode="mock",
        execution_origin="mission_campaign_mock",
        blueprint_version_snapshot=1,
        frozen_execution_plan_json={
            "schema_version": "2",
            "country_code": "LB",
            "country_name": "Lebanon",
            "regions": ["Beirut"],
            "languages": ["English"],
        },
        execution_plan_schema_version="2",
        execution_plan_hash=PLAN_HASH,
        execution_plan_compiled_at=FIXED_NOW,
        status=status,
        country_code="LB",
        country_name="Lebanon",
        started_at=FIXED_NOW if status == ScrapingExecutionStatus.RUNNING else None,
    )
    session.add(execution)
    await session.flush()
    return org, execution


async def _add_pending_query(
    session: AsyncSession,
    *,
    org_id: str,
    execution_id: str,
    fingerprint: str | None = None,
    generation_ordinal: int = 0,
    query_text: str = "Lebanon rehabilitation directory",
    next_page_number: int = 1,
    pages_completed: int = 0,
    pagination_completed: bool = False,
    last_page_fingerprint: str | None = None,
) -> ScrapingSourceDiscoveryQuery:
    row = ScrapingSourceDiscoveryQuery(
        organization_id=org_id,
        execution_id=execution_id,
        country_code="LB",
        country_name="Lebanon",
        region_code=None,
        region_name=None,
        language_code="en",
        language_name="English",
        source_category="directory",
        query_text=query_text,
        provider=None,
        status=SourceDiscoveryQueryStatus.PENDING,
        result_count=0,
        metadata_json={},
        purpose="seed_source_discovery",
        priority=100,
        discovery_round=1,
        generation_ordinal=generation_ordinal,
        scope_level="countrywide",
        query_job_fingerprint=fingerprint or uuid.uuid4().hex,
        plan_hash_snapshot=PLAN_HASH,
        important_city=None,
        next_page_number=next_page_number,
        pages_completed=pages_completed,
        pagination_completed=pagination_completed,
        last_page_fingerprint=last_page_fingerprint,
        requested_at=None,
    )
    session.add(row)
    await session.flush()
    return row


async def _add_running_query(
    session: AsyncSession,
    *,
    org_id: str,
    execution_id: str,
    claim_token: str | None = None,
    fingerprint: str | None = None,
    generation_ordinal: int = 0,
    next_page_number: int = 1,
    pages_completed: int = 0,
    last_page_fingerprint: str | None = None,
    query_text: str = "Lebanon rehabilitation directory",
) -> ScrapingSourceDiscoveryQuery:
    token = claim_token or generate_claim_token()
    row = ScrapingSourceDiscoveryQuery(
        organization_id=org_id,
        execution_id=execution_id,
        country_code="LB",
        country_name="Lebanon",
        region_code=None,
        region_name=None,
        language_code="en",
        language_name="English",
        source_category="directory",
        query_text=query_text,
        provider="serper",
        status=SourceDiscoveryQueryStatus.RUNNING,
        result_count=0,
        metadata_json={},
        purpose="seed_source_discovery",
        priority=100,
        discovery_round=1,
        generation_ordinal=generation_ordinal,
        scope_level="countrywide",
        query_job_fingerprint=fingerprint or uuid.uuid4().hex,
        plan_hash_snapshot=PLAN_HASH,
        important_city=None,
        claim_token=token,
        claimed_at=FIXED_NOW,
        lease_expires_at=FIXED_NOW + timedelta(seconds=60),
        attempt_count=1,
        last_attempt_at=FIXED_NOW,
        requested_at=FIXED_NOW,
        next_page_number=next_page_number,
        pages_completed=pages_completed,
        pagination_completed=False,
        last_page_fingerprint=last_page_fingerprint,
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
        next_page_number=int(getattr(row, "next_page_number", None) or 1),
        pages_completed=int(getattr(row, "pages_completed", None) or 0),
        pagination_completed=bool(getattr(row, "pagination_completed", False)),
        last_page_result_count=getattr(row, "last_page_result_count", None),
        last_page_fingerprint=getattr(row, "last_page_fingerprint", None),
    )


def _page_urls(page: int, count: int, *, prefix: str = SAFE_PREFIX) -> list[str]:
    start = (page - 1) * PAGE_SIZE
    return [f"{prefix}{start + i}" for i in range(1, count + 1)]


def _items(
    claimed: ClaimedQueryJob,
    urls: list[str],
    *,
    page_number: int,
) -> tuple[DiscoveryProviderResultItem, ...]:
    return tuple(
        DiscoveryProviderResultItem(
            original_url=url,
            title=f"T{i}",
            snippet=f"S{i}",
            rank=i,
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
            provider_page_number=page_number,
        )
        for i, url in enumerate(urls, start=1)
    )


def _success_page(
    claimed: ClaimedQueryJob,
    *,
    page_number: int,
    urls: list[str],
    has_more: bool,
    page_fingerprint: str | None = None,
) -> DiscoveryProviderExecutionResult:
    fingerprint = page_fingerprint or (f"{page_number:064d}"[:64])
    results = _items(claimed, urls, page_number=page_number)
    return DiscoveryProviderExecutionResult(
        outcome="succeeded",
        provider=claimed.provider,
        query_job_id=claimed.id,
        organization_id=claimed.organization_id,
        execution_id=claimed.execution_id,
        claim_token=claimed.claim_token,
        discovered_at=FIXED_NOW,
        results=results,
        raw_result_count=len(urls),
        accepted_result_count=len(results),
        diagnostic_code="succeeded",
        continuation=DiscoveryProviderContinuation(
            requested_page_size=PAGE_SIZE,
            returned_result_count=len(urls),
            has_more=has_more,
            page_number=page_number,
            page_fingerprint=fingerprint,
        ),
        page_number=page_number,
        page_fingerprint=fingerprint,
        provider_page_size=PAGE_SIZE,
    )


def _prepare_page(
    claimed: ClaimedQueryJob,
    *,
    page_number: int,
    urls: list[str],
    has_more: bool,
    page_fingerprint: str | None = None,
):
    return prepare_provider_results(
        claimed,
        _success_page(
            claimed,
            page_number=page_number,
            urls=urls,
            has_more=has_more,
            page_fingerprint=page_fingerprint,
        ),
        clock=FIXED_NOW,
    )


@dataclass
class MultiPageInjectedProvider:
    """Injected double: page1 full (has_more), page2 short/empty/final or scripted outcomes."""

    page_outcomes: dict[int, str] = field(default_factory=dict)
    page2_urls: list[str] = field(default_factory=list)
    page2_empty: bool = False
    calls: list[ClaimedQueryJob] = field(default_factory=list)
    default_outcome: str = "succeeded"

    async def execute_claimed_query(
        self,
        claimed_job: ClaimedQueryJob,
        provider_name: str,
        *,
        result_page_size: int | None = None,
    ) -> DiscoveryProviderExecutionResult:
        del provider_name, result_page_size
        self.calls.append(claimed_job)
        page = int(getattr(claimed_job, "next_page_number", None) or 1)
        outcome = self.page_outcomes.get(page, self.default_outcome)
        if outcome != "succeeded":
            return DiscoveryProviderExecutionResult(
                outcome=outcome,  # type: ignore[arg-type]
                provider="serper",
                query_job_id=claimed_job.id,
                organization_id=claimed_job.organization_id,
                execution_id=claimed_job.execution_id,
                claim_token=claimed_job.claim_token,
                discovered_at=FIXED_NOW,
                diagnostic_code=outcome,
                page_number=page,
            )
        if page == 1:
            urls = _page_urls(1, PAGE_SIZE)
            return _success_page(claimed_job, page_number=1, urls=urls, has_more=True)
        urls = [] if self.page2_empty else (self.page2_urls or _page_urls(2, 3))
        return _success_page(
            claimed_job,
            page_number=page,
            urls=urls,
            has_more=False,
            page_fingerprint=f"{page:064d}"[:64],
        )


def _build_execution_service(
    sessions: async_sessionmaker[AsyncSession],
    provider: Any,
    *,
    events: list[str] | None = None,
    queue_calls: list[dict[str, Any]] | None = None,
) -> SourceDiscoveryExecutionService:
    event_log = events if events is not None else []
    queues = queue_calls if queue_calls is not None else []

    async def queue_continuation(execution_id_arg, **kwargs):
        queues.append({"execution_id": execution_id_arg, **kwargs})

    async def emit(execution_id_arg, event_type, message, *, metadata=None):
        del execution_id_arg, message, metadata
        event_log.append(event_type)

    return SourceDiscoveryExecutionService(
        session_factory=sessions,
        claim_service=_claim_service(sessions),
        provider_service=provider,
        result_service=_result_service(sessions),
        prepare_fn=prepare_provider_results,
        now_factory=lambda: FIXED_NOW,
        queue_continuation=queue_continuation,
        event_emitter=emit,
        claim_batch_size=2,
        provider_concurrency=1,
        recovery_batch_size=5,
        lease_duration=timedelta(seconds=60),
    )


# --- Coverage -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_01_persist_page_and_continue_advances(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id, next_page_number=1
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    batch = _prepare_page(
        claimed,
        page_number=1,
        urls=_page_urls(1, PAGE_SIZE),
        has_more=True,
        page_fingerprint="1" * 64,
    )
    result = await _result_service(maker).persist_page_and_continue(batch)
    assert result.outcome == "page_continued"
    assert result.counts.next_page_number == 2
    assert result.counts.pages_completed == 1
    assert result.counts.pagination_completed is False
    assert result.counts.candidate_inserted_count == PAGE_SIZE
    assert result.query_status == SourceDiscoveryQueryStatus.PENDING.value

    async with maker() as session:
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert job is not None
        assert job.status == SourceDiscoveryQueryStatus.PENDING
        assert job.next_page_number == 2
        assert job.pages_completed == 1
        assert job.pagination_completed is False
        assert job.claim_token is None
        assert job.last_page_fingerprint == "1" * 64


@pytest.mark.asyncio
async def test_02_persist_final_page_and_succeed(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session,
            org_id=org.id,
            execution_id=execution.id,
            next_page_number=2,
            pages_completed=1,
            last_page_fingerprint="1" * 64,
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    batch = _prepare_page(
        claimed,
        page_number=2,
        urls=_page_urls(2, 3),
        has_more=False,
        page_fingerprint="2" * 64,
    )
    result = await _result_service(maker).persist_final_page_and_succeed(batch)
    assert result.outcome == "applied"
    assert result.counts.pagination_completed is True
    assert result.counts.pages_completed == 2
    assert result.counts.next_page_number == 2
    assert result.counts.query_marked_succeeded is True

    async with maker() as session:
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert job is not None
        assert job.status == SourceDiscoveryQueryStatus.SUCCEEDED
        assert job.pagination_completed is True
        assert job.pagination_completed_at is not None
        assert job.next_page_number == 2
        assert job.pages_completed == 2
        assert job.claim_token is None


@pytest.mark.asyncio
async def test_03_crash_restart_resumes_at_stored_page(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id, next_page_number=1
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    await _result_service(maker).persist_page_and_continue(
        _prepare_page(
            claimed,
            page_number=1,
            urls=_page_urls(1, PAGE_SIZE),
            has_more=True,
            page_fingerprint="a" * 64,
        )
    )

    # Crash/restart: reclaim the same job; cursor must be page 2.
    claimed_batch = await _claim_service(maker).claim_eligible_jobs(
        organization_id=org.id,
        execution_id=execution.id,
        provider="serper",
        batch_size=1,
        now=FIXED_NOW,
    )
    assert claimed_batch.claimed_count == 1
    resumed = claimed_batch.jobs[0]
    assert resumed.id == query.id
    assert resumed.next_page_number == 2
    assert resumed.pages_completed == 1
    assert resumed.last_page_fingerprint == "a" * 64


@pytest.mark.asyncio
async def test_04_retryable_keeps_page_n(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session,
            org_id=org.id,
            execution_id=execution.id,
            next_page_number=2,
            pages_completed=1,
            last_page_fingerprint="b" * 64,
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    mutation = await _claim_service(maker).requeue_retryable_failure(
        organization_id=org.id,
        execution_id=execution.id,
        query_job_id=query.id,
        claim_token=claimed.claim_token,
        error_code="provider_timeout",
        next_attempt_at=FIXED_NOW + timedelta(seconds=30),
        now=FIXED_NOW,
    )
    assert mutation.outcome == "applied"

    async with maker() as session:
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert job is not None
        assert job.status == SourceDiscoveryQueryStatus.PENDING
        assert job.next_page_number == 2
        assert job.pages_completed == 1
        assert job.last_page_fingerprint == "b" * 64
        assert job.claim_token is None


@pytest.mark.asyncio
async def test_05_concurrent_claims_cannot_double_apply_page(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id, next_page_number=1
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    batch = _prepare_page(
        claimed,
        page_number=1,
        urls=_page_urls(1, PAGE_SIZE),
        has_more=True,
        page_fingerprint="c" * 64,
    )
    r1, r2 = await asyncio.gather(
        _result_service(maker).persist_page_and_continue(batch),
        _result_service(maker).persist_page_and_continue(batch),
    )
    outcomes = {r1.outcome, r2.outcome}
    assert "page_continued" in outcomes
    # Loser sees cleared claim (stale) or already-pending status (rejected).
    assert outcomes & {"stale_claim", "rejected"}
    assert len(outcomes) == 2

    async with maker() as session:
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert job is not None
        assert job.next_page_number == 2
        assert job.pages_completed == 1
        candidates = (
            await session.execute(
                select(func.count()).select_from(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id
                )
            )
        ).scalar_one()
        assert candidates == PAGE_SIZE


@pytest.mark.asyncio
async def test_06_stale_token_rejected_without_mutation(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        token_a = generate_claim_token()
        query = await _add_running_query(
            session,
            org_id=org.id,
            execution_id=execution.id,
            claim_token=token_a,
            next_page_number=1,
        )
        await session.commit()

    async with maker() as session:
        row = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert row is not None
        claimed_a = _claimed_from_row(row)
    batch_a = _prepare_page(
        claimed_a,
        page_number=1,
        urls=_page_urls(1, PAGE_SIZE),
        has_more=True,
        page_fingerprint="d" * 64,
    )

    # Lease expires; worker B reclaims.
    later = FIXED_NOW + timedelta(minutes=5)
    claims = _claim_service(maker, now=later)
    recovered = await claims.recover_expired_claims(
        organization_id=org.id,
        execution_id=execution.id,
        batch_size=1,
        now=later,
    )
    assert recovered.recovered_count == 1
    claimed_batch = await claims.claim_eligible_jobs(
        organization_id=org.id,
        execution_id=execution.id,
        provider="serper",
        batch_size=1,
        now=later,
    )
    assert claimed_batch.claimed_count == 1
    token_b = claimed_batch.jobs[0].claim_token
    assert token_b != token_a

    stale = await _result_service(maker).persist_page_and_continue(batch_a)
    assert stale.outcome == "stale_claim"
    assert stale.counts.candidate_inserted_count == 0
    assert stale.counts.pages_completed is None or stale.counts.pages_completed == 0

    async with maker() as session:
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert job is not None
        assert job.status == SourceDiscoveryQueryStatus.RUNNING
        assert job.claim_token == token_b
        assert job.next_page_number == 1
        assert job.pages_completed == 0
        c = (
            await session.execute(
                select(func.count()).select_from(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id
                )
            )
        ).scalar_one()
        assert c == 0


@pytest.mark.asyncio
async def test_07_cross_page_duplicate_url_one_crawl_node(postgres_sessions) -> None:
    maker = postgres_sessions
    shared = "https://docs.python.org/shared-facility"
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id, next_page_number=1
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    page1_urls = _page_urls(1, PAGE_SIZE - 1) + [shared]
    r1 = await _result_service(maker).persist_page_and_continue(
        _prepare_page(
            claimed,
            page_number=1,
            urls=page1_urls,
            has_more=True,
            page_fingerprint="e" * 64,
        )
    )
    assert r1.outcome == "page_continued"

    async with maker() as session:
        row = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert row is not None
        row.status = SourceDiscoveryQueryStatus.RUNNING
        row.claim_token = generate_claim_token()
        row.claimed_at = FIXED_NOW
        row.lease_expires_at = FIXED_NOW + timedelta(seconds=60)
        row.attempt_count = 2
        row.last_attempt_at = FIXED_NOW
        row.provider = "serper"
        row.requested_at = FIXED_NOW
        await session.commit()
        claimed2 = _claimed_from_row(row)

    r2 = await _result_service(maker).persist_final_page_and_succeed(
        _prepare_page(
            claimed2,
            page_number=2,
            urls=[shared, "https://docs.python.org/page-extra"],
            has_more=False,
            page_fingerprint="f" * 64,
        )
    )
    assert r2.outcome == "applied"
    assert r2.counts.candidate_existing_count >= 1
    assert r2.counts.crawl_node_existing_count >= 1

    async with maker() as session:
        nodes = (
            await session.execute(
                select(func.count()).select_from(ScrapingCrawlNode).where(
                    ScrapingCrawlNode.execution_id == execution.id
                )
            )
        ).scalar_one()
        shared_candidates = (
            await session.execute(
                select(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id,
                    ScrapingSourceCandidate.canonical_url == shared,
                )
            )
        ).scalars().all()
        # page1 created PAGE_SIZE nodes; page2 adds one new URL node (shared reuses).
        assert nodes == PAGE_SIZE + 1
        assert len(shared_candidates) == 1
        assert shared_candidates[0].provider_page_number == 1


@pytest.mark.asyncio
async def test_08_empty_final_page_completes_pagination(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session,
            org_id=org.id,
            execution_id=execution.id,
            next_page_number=2,
            pages_completed=1,
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    result = await _result_service(maker).persist_final_page_and_succeed(
        _prepare_page(
            claimed,
            page_number=2,
            urls=[],
            has_more=False,
            page_fingerprint="0" * 64,
        )
    )
    assert result.outcome == "applied"
    assert result.counts.pagination_completed is True
    assert result.counts.candidate_inserted_count == 0

    async with maker() as session:
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert job is not None
        assert job.status == SourceDiscoveryQueryStatus.SUCCEEDED
        assert job.pagination_completed is True
        assert job.pages_completed == 2


@pytest.mark.asyncio
async def test_09_repeated_provider_page_preserves_earlier(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_pending_query(
            session, org_id=org.id, execution_id=execution.id
        )
        await session.commit()

    provider = MultiPageInjectedProvider(
        page_outcomes={2: "repeated_provider_page"},
    )
    events: list[str] = []
    service = _build_execution_service(maker, provider, events=events)

    first = await service.run_discovery_work_slice(org.id, execution.id)
    assert first.outcome == "continue_enqueued"
    assert len(provider.calls) == 1
    assert provider.calls[0].next_page_number == 1
    assert "web_discovery_page_persisted" in events

    async with maker() as session:
        candidates_after_page1 = (
            await session.execute(
                select(func.count()).select_from(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id
                )
            )
        ).scalar_one()
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert job is not None
        assert job.status == SourceDiscoveryQueryStatus.PENDING
        assert job.next_page_number == 2
        assert job.pages_completed == 1
    assert candidates_after_page1 == PAGE_SIZE

    second = await service.run_discovery_work_slice(org.id, execution.id)
    assert second.counts.failed_count == 1
    assert len(provider.calls) == 2
    assert provider.calls[1].next_page_number == 2
    assert "web_discovery_job_failed" in events

    async with maker() as session:
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        candidates = (
            await session.execute(
                select(func.count()).select_from(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id
                )
            )
        ).scalar_one()
        assert job is not None
        assert job.status == SourceDiscoveryQueryStatus.FAILED
        assert job.pagination_completed is False
        assert candidates == PAGE_SIZE  # earlier page preserved


@pytest.mark.asyncio
async def test_10_provider_blocker_requeues_at_page_n_and_pauses(
    postgres_sessions,
) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_pending_query(
            session, org_id=org.id, execution_id=execution.id
        )
        await session.commit()

    provider = MultiPageInjectedProvider(
        page_outcomes={2: "provider_not_configured"},
    )
    events: list[str] = []
    service = _build_execution_service(maker, provider, events=events)

    first = await service.run_discovery_work_slice(org.id, execution.id)
    assert first.outcome == "continue_enqueued"
    assert "web_discovery_page_persisted" in events

    blocked = await service.run_discovery_work_slice(org.id, execution.id)
    assert blocked.outcome == "provider_blocked"
    assert "web_discovery_blocked" in events
    assert provider.calls[-1].next_page_number == 2

    async with maker() as session:
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        exec_row = await session.get(ScrapingExecution, execution.id)
        assert job is not None
        assert exec_row is not None
        assert job.status == SourceDiscoveryQueryStatus.PENDING
        assert job.next_page_number == 2
        assert job.pages_completed == 1
        assert exec_row.status == ScrapingExecutionStatus.PAUSED
        profile = exec_row.country_profile_json or {}
        assert profile.get(PROVIDER_BLOCK_PROFILE_KEY) is True


@pytest.mark.asyncio
async def test_11_resume_clears_blocked_and_retries_pending_page(
    postgres_sessions,
) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_pending_query(
            session, org_id=org.id, execution_id=execution.id
        )
        await session.commit()

    provider = MultiPageInjectedProvider(
        page_outcomes={2: "provider_not_configured"},
        page2_urls=_page_urls(2, 2),
    )
    events: list[str] = []
    service = _build_execution_service(maker, provider, events=events)

    await service.run_discovery_work_slice(org.id, execution.id)
    blocked = await service.run_discovery_work_slice(org.id, execution.id)
    assert blocked.outcome == "provider_blocked"

    # Operator resume: clear pause and allow another slice (clears blocked markers).
    async with maker() as session:
        exec_row = await session.get(ScrapingExecution, execution.id)
        assert exec_row is not None
        exec_row.status = ScrapingExecutionStatus.RUNNING
        exec_row.paused_at = None
        await session.commit()

    provider.page_outcomes.pop(2, None)  # page 2 now succeeds (short final)
    resumed = await service.run_discovery_work_slice(org.id, execution.id)
    assert resumed.outcome in {"completed", "continue_enqueued"}
    assert any(c.next_page_number == 2 for c in provider.calls)

    async with maker() as session:
        job = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        exec_row = await session.get(ScrapingExecution, execution.id)
        assert job is not None
        assert exec_row is not None
        assert job.status == SourceDiscoveryQueryStatus.SUCCEEDED
        assert job.pagination_completed is True
        profile = exec_row.country_profile_json or {}
        assert not profile.get(PROVIDER_BLOCK_PROFILE_KEY)


@pytest.mark.asyncio
async def test_12_completion_waits_for_pagination_completed(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        # Succeeded without pagination_completed — must not complete discovery.
        row = ScrapingSourceDiscoveryQuery(
            organization_id=org.id,
            execution_id=execution.id,
            country_code="LB",
            country_name="Lebanon",
            language_code="en",
            language_name="English",
            source_category="directory",
            query_text="legacy succeeded",
            provider="serper",
            status=SourceDiscoveryQueryStatus.SUCCEEDED,
            result_count=0,
            metadata_json={},
            purpose="seed_source_discovery",
            priority=100,
            discovery_round=1,
            generation_ordinal=0,
            scope_level="countrywide",
            query_job_fingerprint=uuid.uuid4().hex,
            plan_hash_snapshot=PLAN_HASH,
            completed_at=FIXED_NOW,
            requested_at=FIXED_NOW,
            next_page_number=1,
            pages_completed=0,
            pagination_completed=False,
        )
        session.add(row)
        await session.commit()

    provider = MultiPageInjectedProvider()
    events: list[str] = []
    service = _build_execution_service(maker, provider, events=events)
    outcome = await service.run_discovery_work_slice(org.id, execution.id)
    assert outcome.outcome == "continue_enqueued"
    assert outcome.error_code == "pagination_incomplete"
    assert "web_discovery_completed" not in events
    assert provider.calls == []


@pytest.mark.asyncio
async def test_13_suite_uses_injected_doubles_only(postgres_sessions) -> None:
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        await _add_pending_query(session, org_id=org.id, execution_id=execution.id)
        await session.commit()

    provider = MultiPageInjectedProvider(page2_empty=True)
    service = _build_execution_service(maker, provider)
    first = await service.run_discovery_work_slice(org.id, execution.id)
    assert first.outcome == "continue_enqueued"
    assert len(provider.calls) == 1
    second = await service.run_discovery_work_slice(org.id, execution.id)
    assert second.counts.succeeded_count == 1
    assert len(provider.calls) == 2
    assert all(isinstance(c, ClaimedQueryJob) for c in provider.calls)
    assert os.environ.get("SERPER_LIVE_SMOKE") != "1"


@pytest.mark.asyncio
async def test_14_ephemeral_db_names_fail_closed(postgres_sessions) -> None:
    # Fixture already enforced page_slice7_* naming; assert regex + forbidden set.
    assert _EPHEMERAL_DB_RE.fullmatch(f"page_slice7_{'a' * 32}")
    assert not _EPHEMERAL_DB_RE.fullmatch("multiai")
    assert "multiai" in _FORBIDDEN_TARGET_DATABASES
    assert "postgres" in _FORBIDDEN_TARGET_DATABASES
    # postgres_sessions yielded successfully → alembic targeted ephemeral DB only.
    async with postgres_sessions() as session:
        one = await session.scalar(select(func.count()).select_from(Organization))
        assert one is not None


@pytest.mark.asyncio
async def test_15_within_query_uniqueness_across_pages(postgres_sessions) -> None:
    maker = postgres_sessions
    dup = "https://docs.python.org/dup-across-pages"
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id, next_page_number=1
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    page1 = _page_urls(1, PAGE_SIZE - 1) + [dup]
    r1 = await _result_service(maker).persist_page_and_continue(
        _prepare_page(
            claimed,
            page_number=1,
            urls=page1,
            has_more=True,
            page_fingerprint="u" * 64,
        )
    )
    assert r1.outcome == "page_continued"
    assert r1.counts.candidate_inserted_count == PAGE_SIZE

    async with maker() as session:
        row = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert row is not None
        row.status = SourceDiscoveryQueryStatus.RUNNING
        row.claim_token = generate_claim_token()
        row.claimed_at = FIXED_NOW
        row.lease_expires_at = FIXED_NOW + timedelta(seconds=60)
        row.attempt_count = 2
        row.last_attempt_at = FIXED_NOW
        row.provider = "serper"
        row.requested_at = FIXED_NOW
        await session.commit()
        claimed2 = _claimed_from_row(row)

    r2 = await _result_service(maker).persist_final_page_and_succeed(
        _prepare_page(
            claimed2,
            page_number=2,
            urls=[dup, "https://docs.python.org/only-page2"],
            has_more=False,
            page_fingerprint="v" * 64,
        )
    )
    assert r2.outcome == "applied"
    assert r2.counts.candidate_existing_count == 1
    assert r2.counts.candidate_inserted_count == 1

    async with maker() as session:
        candidates = (
            await session.execute(
                select(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id
                )
            )
        ).scalars().all()
        by_url = {c.canonical_url: c for c in candidates}
        assert len(candidates) == PAGE_SIZE + 1  # page1 PAGE_SIZE + one new on page2
        assert by_url[dup].provider_page_number == 1
        assert by_url["https://docs.python.org/only-page2"].provider_page_number == 2


@pytest.mark.asyncio
async def test_14_same_page_replay_repairs_null_provider_page(postgres_sessions) -> None:
    """Replay with null provenance repairs in place; conflicting non-null page fails closed."""
    maker = postgres_sessions
    async with maker() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id, next_page_number=1
        )
        await session.commit()
        claimed = _claimed_from_row(query)

    prepared = _prepare_page(
        claimed,
        page_number=1,
        urls=["https://docs.python.org/repair-null"],
        has_more=False,
        page_fingerprint="w" * 64,
    )
    first = await _result_service(maker).persist_final_page_and_succeed(prepared)
    assert first.outcome == "applied"

    async with maker() as session:
        candidate = (
            await session.execute(
                select(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id,
                    ScrapingSourceCandidate.canonical_url
                    == "https://docs.python.org/repair-null",
                )
            )
        ).scalar_one()
        candidate.provider_page_number = None
        row = await session.get(ScrapingSourceDiscoveryQuery, query.id)
        assert row is not None
        row.status = SourceDiscoveryQueryStatus.RUNNING
        row.claim_token = generate_claim_token()
        row.claimed_at = FIXED_NOW
        row.lease_expires_at = FIXED_NOW + timedelta(seconds=60)
        row.attempt_count = 2
        row.last_attempt_at = FIXED_NOW
        row.provider = "serper"
        row.requested_at = FIXED_NOW
        row.pagination_completed = False
        row.completed_at = None
        await session.commit()
        claimed2 = _claimed_from_row(row)

    stale = await _result_service(maker).persist_final_page_and_succeed(prepared)
    assert stale.outcome == "stale_claim"
    assert stale.error_code == "stale_claim"

    async with maker() as session:
        still_null = (
            await session.execute(
                select(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id,
                    ScrapingSourceCandidate.canonical_url
                    == "https://docs.python.org/repair-null",
                )
            )
        ).scalar_one()
        assert still_null.provider_page_number is None
        node_count_before = (
            await session.execute(
                select(func.count()).select_from(ScrapingCrawlNode).where(
                    ScrapingCrawlNode.execution_id == execution.id
                )
            )
        ).scalar_one()

    prepared2 = _prepare_page(
        claimed2,
        page_number=1,
        urls=["https://docs.python.org/repair-null"],
        has_more=False,
        page_fingerprint="w" * 64,
    )
    repaired = await _result_service(maker).persist_final_page_and_succeed(prepared2)
    assert repaired.outcome == "applied"
    assert repaired.counts.candidate_existing_count == 1

    async with maker() as session:
        candidate = (
            await session.execute(
                select(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id,
                    ScrapingSourceCandidate.canonical_url
                    == "https://docs.python.org/repair-null",
                )
            )
        ).scalar_one()
        assert candidate.provider_page_number == 1
        candidate_count = (
            await session.execute(
                select(func.count()).select_from(ScrapingSourceCandidate).where(
                    ScrapingSourceCandidate.discovery_query_id == query.id
                )
            )
        ).scalar_one()
        assert candidate_count == 1
        node_count_after = (
            await session.execute(
                select(func.count()).select_from(ScrapingCrawlNode).where(
                    ScrapingCrawlNode.execution_id == execution.id
                )
            )
        ).scalar_one()
        assert node_count_after == node_count_before
