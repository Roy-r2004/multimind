"""PostgreSQL ownership and idempotency proof for guarded controlled profiles."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import (
    OrgMembership,
    OrgRole,
    Organization,
    Phase5WorkStatus,
    ScrapingCrawlNode,
    ScrapingBlueprint,
    ScrapingExecution,
    ScrapingFacilityPhaseWorkJob,
    ScrapingMission,
    ScrapingPhase5RetrievalResult,
    ScrapingPhase5WorkJob,
    ScrapingRun,
    ScrapingSourceCandidate,
    ScrapingSourceDiscoveryQuery,
    ScrapingSourceDocument,
    User,
)
from app.services.scraping.phase5_contracts import Phase5WorkKind, prepare_phase5_job
from app.services.scraping.phase5_job_service import (
    claim_guarded_controlled_http_job,
    create_job_idempotently,
    persist_retrieval_resources,
)
from app.services.scraping.phase5_retrieval_service import (
    RetrievalActionResult,
    prepare_resource,
)
from phase5a_postgres_support import create_phase5_database, drop_phase5_database
from scripts.phase5_guarded_smoke import (
    controlled_profile_identities,
    create_controlled_profile,
    preview_controlled_profile,
    validate_controlled_profile_request,
)

pytestmark = pytest.mark.asyncio


async def _create_owned_controlled_graph(sessions, *, suffix: str):
    async with sessions.begin() as session:
        organization = Organization(
            name=f"Controlled Profile {suffix}",
            slug=f"controlled-profile-{suffix}",
        )
        user = User(
            email=f"controlled-profile-{suffix}@example.test",
            hashed_password="x",
            full_name="Controlled Profile Owner",
        )
        session.add_all([organization, user])
        await session.flush()
        session.add(OrgMembership(
            org_id=organization.id, user_id=user.id, role=OrgRole.OWNER))
        await session.flush()
        organization_id, creator_id = organization.id, user.id
    request = validate_controlled_profile_request(
        organization_id,
        "LB",
        "https://cedar-rehab.org/",
        f"Cedar Rehab Package A smoke {suffix}",
    )
    async with sessions.begin() as session:
        result = await create_controlled_profile(
            session, request, creator_id=creator_id)
    return request, result


def _prepared_http_job(request, result, *, requested_at):
    return prepare_phase5_job(
        organization_id=request["organization_id"],
        execution_id=result["execution_id"],
        source_candidate_id=result["source_candidate_id"],
        crawl_node_id=result["crawl_node_id"],
        original_url=request["canonical_url"],
        source_classification="facility_profile",
        work_kind=Phase5WorkKind.HTTP_RETRIEVAL,
        selected_tool="http",
        requested_at=requested_at,
    )


@pytest.fixture
async def controlled_profile_sessions(
) -> AsyncGenerator[tuple[async_sessionmaker[AsyncSession], object], None]:
    database = await create_phase5_database()
    engine = None
    try:
        await database.alembic("upgrade", "head")
        current = await database.alembic("current")
        heads = await database.alembic("heads")
        assert "034 (head)" in current
        assert [line.strip() for line in heads.splitlines() if "(head)" in line] == [
            "034 (head)"
        ]
        schema = await database.connect()
        try:
            assert await schema.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'orgrole')"
            )
            required_tables = {
                "scraping_missions",
                "scraping_blueprints",
                "scraping_executions",
                "scraping_source_discovery_queries",
                "scraping_source_candidates",
                "scraping_crawl_nodes",
                "scraping_phase5_work_jobs",
                "scraping_phase5_retrieval_results",
                "scraping_source_documents",
                "scraping_facility_phase_work_jobs",
            }
            actual_tables = {
                row["table_name"]
                for row in await schema.fetch(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public'"
                )
            }
            assert required_tables <= actual_tables
            required_constraints = {
                "fk_source_candidates_crawl_node_org_exec",
                "uq_source_candidate_id_org_exec",
                "fk_phase5_job_candidate_org_exec",
                "uq_phase5_job_id_org_exec",
                "fk_facility_job_candidate",
                "uq_facility_phase_job_owner",
            }
            actual_constraints = {
                row["conname"]
                for row in await schema.fetch(
                    "SELECT conname FROM pg_constraint "
                    "WHERE connamespace = 'public'::regnamespace"
                )
            }
            assert required_constraints <= actual_constraints
        finally:
            await schema.close()
        engine = create_async_engine(
            database.url.replace("postgresql://", "postgresql+asyncpg://"))
        yield (
            async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False),
            database,
        )
    finally:
        if engine:
            await engine.dispose()
        await drop_phase5_database(database)


async def test_controlled_profile_graph_is_atomic_owned_and_idempotent_postgres(
    controlled_profile_sessions,
):
    sessions, _database = controlled_profile_sessions
    async with sessions.begin() as session:
        organization = Organization(
            name="Controlled Profile Org", slug="controlled-profile-org")
        user = User(
            email="controlled-profile@example.test",
            hashed_password="x",
            full_name="Controlled Profile Owner",
        )
        session.add_all([organization, user])
        await session.flush()
        session.add(OrgMembership(
            org_id=organization.id, user_id=user.id, role=OrgRole.OWNER))
        await session.flush()
        organization_id = organization.id
        creator_id = user.id

    request = validate_controlled_profile_request(
        organization_id,
        "LB",
        "https://cedar-rehab.org/",
        "Cedar Rehab Package A smoke",
    )
    ids = controlled_profile_identities(request)
    with pytest.raises(RuntimeError, match="prove_atomic_rollback"):
        async with sessions.begin() as session:
            await create_controlled_profile(
                session, request, creator_id=creator_id)
            raise RuntimeError("prove_atomic_rollback")
    async with sessions() as session:
        for model, record_id in (
            (ScrapingMission, ids["mission"]),
            (ScrapingBlueprint, ids["blueprint"]),
            (ScrapingExecution, ids["execution"]),
            (ScrapingSourceDiscoveryQuery, ids["discovery_query"]),
            (ScrapingSourceCandidate, ids["source_candidate"]),
            (ScrapingCrawlNode, ids["crawl_node"]),
        ):
            assert await session.get(model, record_id) is None

    async with sessions.begin() as session:
        first = await create_controlled_profile(
            session, request, creator_id=creator_id)
    async with sessions.begin() as session:
        second = await create_controlled_profile(
            session, request, creator_id=creator_id)

    assert first["created"] is True
    assert second["created"] is False
    assert first["execution_id"] == second["execution_id"]
    async with sessions() as session:
        execution = await session.get(
            ScrapingExecution, first["execution_id"])
        assert execution is not None
        assert execution.organization_id == organization_id
        assert execution.status.value == "paused"
        assert await session.scalar(
            select(func.count()).select_from(ScrapingMission).where(
                ScrapingMission.id == ids["mission"])) == 1
        assert await session.scalar(
            select(func.count()).select_from(ScrapingBlueprint).where(
                ScrapingBlueprint.id == ids["blueprint"])) == 1
        assert await session.scalar(
            select(func.count()).select_from(ScrapingSourceDiscoveryQuery).where(
                ScrapingSourceDiscoveryQuery.execution_id == execution.id)) == 1
        assert await session.scalar(
            select(func.count()).select_from(ScrapingRun).where(
                ScrapingRun.mission_id == ids["mission"])) == 0
        assert await session.scalar(
            select(func.count()).select_from(ScrapingCrawlNode).where(
                ScrapingCrawlNode.execution_id == execution.id)) == 1
        assert await session.scalar(
            select(func.count()).select_from(ScrapingSourceCandidate).where(
                ScrapingSourceCandidate.execution_id == execution.id)) == 1
        preview_row = (await session.execute(
            select(ScrapingCrawlNode, ScrapingSourceCandidate)
            .join(
                ScrapingSourceCandidate,
                ScrapingSourceCandidate.crawl_node_id == ScrapingCrawlNode.id,
            )
            .where(
                ScrapingCrawlNode.id == first["crawl_node_id"],
                ScrapingCrawlNode.organization_id == organization_id,
                ScrapingCrawlNode.execution_id == execution.id,
                ScrapingSourceCandidate.organization_id == organization_id,
                ScrapingSourceCandidate.execution_id == execution.id,
            )
        )).first()
        assert preview_row is not None
        for model in (
            ScrapingPhase5WorkJob,
            ScrapingPhase5RetrievalResult,
            ScrapingSourceDocument,
            ScrapingFacilityPhaseWorkJob,
        ):
            assert await session.scalar(
                select(func.count()).select_from(model).where(
                    model.execution_id == execution.id)) == 0


async def test_guarded_http_seeds_claims_outside_tx_persists_once_and_replays(
    controlled_profile_sessions,
):
    sessions, _database = controlled_profile_sessions
    request, graph = await _create_owned_controlled_graph(
        sessions, suffix="http-lifecycle")
    now = datetime.now(UTC)
    async with sessions() as session:
        preview = await preview_controlled_profile(session, request)
        assert preview["rows_that_would_be_created"] == 0
        assert await session.scalar(select(func.count()).select_from(
            ScrapingPhase5WorkJob).where(
                ScrapingPhase5WorkJob.execution_id == graph["execution_id"])) == 0

    prepared = _prepared_http_job(request, graph, requested_at=now)
    async with sessions.begin() as seed_session:
        seeded = await create_job_idempotently(seed_session, prepared)
    assert seeded.outcome == "created"
    async with sessions.begin() as claim_session:
        claimed = await claim_guarded_controlled_http_job(
            claim_session,
            job_id=seeded.record_id,
            organization_id=request["organization_id"],
            execution_id=graph["execution_id"],
            crawl_node_id=graph["crawl_node_id"],
            lease_duration=timedelta(minutes=5),
        )
    assert claimed.outcome == "job_seeded_and_claimed"
    assert claimed.claim is not None
    assert claim_session.in_transaction() is False

    class MockHttpTransport:
        calls = 0

        async def retrieve(self, *, url, requested_at, fetched_at):
            assert claim_session.in_transaction() is False
            self.calls += 1
            resource = prepare_resource(
                requested_url=url,
                final_url=url,
                content_type="text/html",
                body=b"<html>Cedar Rehab Hammana</html>",
                retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL,
                requested_at=requested_at,
                fetched_at=fetched_at,
            )
            return RetrievalActionResult(
                outcome="succeeded", resources=(resource,))

    transport = MockHttpTransport()
    retrieval = await transport.retrieve(
        url=request["canonical_url"],
        requested_at=now,
        fetched_at=datetime.now(UTC),
    )
    async with sessions.begin() as session:
        persisted = await persist_retrieval_resources(
            session,
            claimed_job=claimed.claim,
            resources=retrieval.resources,
            completed_at=datetime.now(UTC),
        )
    assert transport.calls == 1
    assert len(persisted) == 1
    assert persisted[0].outcome == "persisted"

    async with sessions.begin() as session:
        replay_seed = await create_job_idempotently(session, prepared)
    assert replay_seed.outcome == "existing"
    async with sessions.begin() as session:
        replay_claim = await claim_guarded_controlled_http_job(
            session,
            job_id=replay_seed.record_id,
            organization_id=request["organization_id"],
            execution_id=graph["execution_id"],
            crawl_node_id=graph["crawl_node_id"],
            lease_duration=timedelta(minutes=5),
        )
    assert replay_claim.outcome == "already_retrieved"
    assert transport.calls == 1

    async with sessions() as session:
        execution = await session.get(ScrapingExecution, graph["execution_id"])
        assert execution.status.value == "paused"
        assert await session.scalar(select(func.count()).select_from(
            ScrapingPhase5WorkJob).where(
                ScrapingPhase5WorkJob.execution_id == execution.id)) == 1
        assert await session.scalar(select(func.count()).select_from(
            ScrapingPhase5RetrievalResult).where(
                ScrapingPhase5RetrievalResult.execution_id == execution.id)) == 1
        assert await session.scalar(select(func.count()).select_from(
            ScrapingSourceDocument).where(
                ScrapingSourceDocument.execution_id == execution.id)) == 1
        assert await session.scalar(select(func.count()).select_from(
            ScrapingFacilityPhaseWorkJob).where(
                ScrapingFacilityPhaseWorkJob.execution_id == execution.id)) == 0


async def test_guarded_http_live_expired_stale_and_ownership_fencing(
    controlled_profile_sessions,
):
    sessions, _database = controlled_profile_sessions
    request, graph = await _create_owned_controlled_graph(
        sessions, suffix="claim-fencing")
    now = datetime.now(UTC)
    prepared = _prepared_http_job(request, graph, requested_at=now)
    async with sessions.begin() as session:
        seeded = await create_job_idempotently(session, prepared)
    async with sessions.begin() as session:
        first = await claim_guarded_controlled_http_job(
            session,
            job_id=seeded.record_id,
            organization_id=request["organization_id"],
            execution_id=graph["execution_id"],
            crawl_node_id=graph["crawl_node_id"],
            lease_duration=timedelta(minutes=5),
        )
    assert first.claim is not None
    async with sessions.begin() as session:
        live = await claim_guarded_controlled_http_job(
            session,
            job_id=seeded.record_id,
            organization_id=request["organization_id"],
            execution_id=graph["execution_id"],
            crawl_node_id=graph["crawl_node_id"],
            lease_duration=timedelta(minutes=5),
        )
    assert live.outcome == "live_claim_exists"
    assert live.diagnostic["claim_token_present"] is True
    assert "claim_token" not in live.diagnostic

    async with sessions.begin() as session:
        job = await session.get(ScrapingPhase5WorkJob, seeded.record_id)
        database_now = await session.scalar(select(func.now()))
        job.claimed_at = database_now - timedelta(minutes=10)
        job.lease_expires_at = database_now - timedelta(minutes=5)
    async with sessions() as session:
        expired_job = await session.get(ScrapingPhase5WorkJob, seeded.record_id)
        database_now = await session.scalar(select(func.now()))
        assert expired_job.claimed_at < expired_job.lease_expires_at
        assert expired_job.lease_expires_at < database_now
        assert expired_job.status == Phase5WorkStatus.RUNNING
        assert expired_job.claim_token == first.claim.claim_token
    async with sessions.begin() as session:
        reclaimed = await claim_guarded_controlled_http_job(
            session,
            job_id=seeded.record_id,
            organization_id=request["organization_id"],
            execution_id=graph["execution_id"],
            crawl_node_id=graph["crawl_node_id"],
            lease_duration=timedelta(minutes=5),
        )
    assert reclaimed.outcome == "job_seeded_and_claimed"
    assert reclaimed.claim is not None
    assert reclaimed.claim.claim_token != first.claim.claim_token
    async with sessions() as session:
        reclaimed_job = await session.get(ScrapingPhase5WorkJob, seeded.record_id)
        database_now = await session.scalar(select(func.now()))
        assert reclaimed_job.status == Phase5WorkStatus.RUNNING
        assert reclaimed_job.claim_token == reclaimed.claim.claim_token
        assert reclaimed_job.claimed_at <= database_now
        assert reclaimed_job.claimed_at < reclaimed_job.lease_expires_at
        assert database_now < reclaimed_job.lease_expires_at

    stale_resource = prepare_resource(
        requested_url=request["canonical_url"],
        final_url=request["canonical_url"],
        content_type="text/html",
        body=b"stale",
        retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL,
        requested_at=now,
        fetched_at=datetime.now(UTC),
    )
    async with sessions.begin() as session:
        stale = await persist_retrieval_resources(
            session,
            claimed_job=first.claim,
            resources=(stale_resource,),
            completed_at=datetime.now(UTC),
        )
    assert stale[0].outcome == "stale_claim"
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(
            ScrapingPhase5RetrievalResult).where(
                ScrapingPhase5RetrievalResult.execution_id
                == graph["execution_id"])) == 0
        assert await session.scalar(select(func.count()).select_from(
            ScrapingSourceDocument).where(
                ScrapingSourceDocument.execution_id == graph["execution_id"])) == 0

    current_resource = prepare_resource(
        requested_url=request["canonical_url"],
        final_url=request["canonical_url"],
        content_type="text/html",
        body=b"current",
        retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL,
        requested_at=now,
        fetched_at=datetime.now(UTC),
    )
    async with sessions.begin() as session:
        current = await persist_retrieval_resources(
            session,
            claimed_job=reclaimed.claim,
            resources=(current_resource,),
            completed_at=datetime.now(UTC),
        )
    assert current[0].outcome == "persisted"
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(
            ScrapingPhase5RetrievalResult).where(
                ScrapingPhase5RetrievalResult.execution_id
                == graph["execution_id"])) == 1
        assert await session.scalar(select(func.count()).select_from(
            ScrapingSourceDocument).where(
                ScrapingSourceDocument.execution_id == graph["execution_id"])) == 1

    async with sessions.begin() as session:
        mismatch = await claim_guarded_controlled_http_job(
            session,
            job_id=seeded.record_id,
            organization_id=request["organization_id"],
            execution_id=graph["execution_id"],
            crawl_node_id="wrong-node",
            lease_duration=timedelta(minutes=5),
        )
    assert mismatch.outcome == "ownership_mismatch"

    async with sessions.begin() as session:
        job = await session.get(ScrapingPhase5WorkJob, seeded.record_id)
        job.status = Phase5WorkStatus.RETRY_SCHEDULED
        job.claim_token = job.claimed_at = job.lease_expires_at = None
        job.next_retry_at = datetime.now(UTC) + timedelta(minutes=10)
    async with sessions.begin() as session:
        deferred = await claim_guarded_controlled_http_job(
            session,
            job_id=seeded.record_id,
            organization_id=request["organization_id"],
            execution_id=graph["execution_id"],
            crawl_node_id=graph["crawl_node_id"],
            lease_duration=timedelta(minutes=5),
        )
    assert deferred.outcome == "retry_deferred"

    async with sessions.begin() as session:
        job = await session.get(ScrapingPhase5WorkJob, seeded.record_id)
        job.status = Phase5WorkStatus.PENDING
        job.next_retry_at = None
        job.max_attempts = job.attempt_count
    async with sessions.begin() as session:
        exhausted = await claim_guarded_controlled_http_job(
            session,
            job_id=seeded.record_id,
            organization_id=request["organization_id"],
            execution_id=graph["execution_id"],
            crawl_node_id=graph["crawl_node_id"],
            lease_duration=timedelta(minutes=5),
        )
    assert exhausted.outcome == "attempts_exhausted"
