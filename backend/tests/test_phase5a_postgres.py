"""Phase 5A PostgreSQL race/ownership tests. Written for later execution, not run by Codex."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    CrawlNodeSourceClassification,
    CrawlEdgeRelationshipType,
    Phase5WorkKind as DbWorkKind,
    Phase5WorkStatus,
    ScrapingCrawlNode,
    ScrapingCrawlEdge,
    ScrapingSourceCandidate,
    ScrapingSourceDocument,
    ScrapingSourceRetrievalAttempt,
    ScrapingDirectoryObservation,
    ScrapingPhase5RetrievalResult,
    ScrapingPhase5WorkJob,
    SourceCandidateStatus,
    SourceRetrievalAttemptStatus,
)
from app.services.scraping.phase5_contracts import (
    Phase5WorkKind,
    PreparedDirectoryObservation,
    PreparedRetrievalResult,
    prepare_phase5_job,
    directory_observation_fingerprint,
    retrieval_result_fingerprint,
)
from app.services.scraping.phase5_job_service import (
    claim_batch,
    create_job_idempotently,
    persist_retrieval_result,
    persist_directory_observation,
    recover_expired_claims,
)
from phase5a_postgres_support import create_phase5_database, drop_phase5_database
from test_phase4_discovery_results_postgres import _add_running_query, _seed_v2_campaign

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)


@pytest.fixture
async def phase5_sessions() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    db = await create_phase5_database()
    engine = None
    try:
        await db.alembic("upgrade", "031")
        engine = create_async_engine(db.url.replace("postgresql://", "postgresql+asyncpg://"))
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        if engine is not None:
            await engine.dispose()
        await drop_phase5_database(db)


async def _seed_context(maker):
    async with maker.begin() as session:
        org, execution = await _seed_v2_campaign(session)
        node = ScrapingCrawlNode(
            organization_id=org.id, execution_id=execution.id,
            canonical_url="https://docs.python.org/a",
            canonical_url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            hostname="docs.python.org", domain="python.org",
            source_classification=CrawlNodeSourceClassification.DIRECTORY,
            first_seen_at=NOW)
        session.add(node)
        await session.flush()
        return org.id, execution.id, node.id


async def _seed_full_context(maker):
    async with maker.begin() as session:
        org, execution = await _seed_v2_campaign(session)
        query = await _add_running_query(
            session, org_id=org.id, execution_id=execution.id)
        node = ScrapingCrawlNode(
            organization_id=org.id, execution_id=execution.id,
            canonical_url="https://docs.python.org/directory",
            canonical_url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            hostname="docs.python.org", domain="python.org",
            source_classification=CrawlNodeSourceClassification.DIRECTORY,
            first_seen_at=NOW)
        profile = ScrapingCrawlNode(
            organization_id=org.id, execution_id=execution.id,
            canonical_url="https://docs.python.org/profile",
            canonical_url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            hostname="docs.python.org", domain="python.org",
            source_classification=CrawlNodeSourceClassification.FACILITY_PROFILE,
            first_seen_at=NOW)
        session.add_all([node, profile])
        await session.flush()
        candidate = ScrapingSourceCandidate(
            organization_id=org.id, execution_id=execution.id,
            discovery_query_id=query.id, crawl_node_id=node.id, provider="serper",
            rank=1, url=node.canonical_url, canonical_url=node.canonical_url,
            domain="python.org", title="Directory", snippet="Listing",
            country_code="LB", country_name="Lebanon", language_code="en",
            language_name="English", source_category="directory",
            initial_relevance_score=0.8, initial_trust_tier="medium",
            status=SourceCandidateStatus.DISCOVERED, discovered_at=NOW,
            metadata_json={})
        session.add(candidate)
        await session.flush()
        edge = ScrapingCrawlEdge(
            organization_id=org.id, execution_id=execution.id,
            from_node_id=node.id, to_node_id=profile.id,
            relationship_type=CrawlEdgeRelationshipType.DIRECTORY_TO_PROFILE,
            discovery_query_id=query.id, source_candidate_id=candidate.id)
        attempt = ScrapingSourceRetrievalAttempt(
            organization_id=org.id, execution_id=execution.id,
            source_candidate_id=candidate.id,
            status=SourceRetrievalAttemptStatus.SUCCEEDED,
            requested_url=node.canonical_url, final_url=node.canonical_url,
            redirect_count=0, http_status=200, content_type="text/html",
            bytes_received=4, started_at=NOW, completed_at=NOW,
            idempotency_key=f"phase5-{uuid.uuid4()}", metadata_json={})
        session.add_all([edge, attempt])
        await session.flush()
        document = ScrapingSourceDocument(
            organization_id=org.id, execution_id=execution.id,
            source_candidate_id=candidate.id, retrieval_attempt_id=attempt.id,
            final_url=node.canonical_url, content_type="text/html",
            content_sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            content_text="test", byte_size=4, retrieval_timestamp=NOW,
            metadata_json={})
        session.add(document)
        await session.flush()
        return {
            "org": org.id, "execution": execution.id, "query": query.id,
            "node": node.id, "profile": profile.id, "candidate": candidate.id,
            "edge": edge.id, "document": document.id,
        }


def _job(org, execution, node, *, url="https://docs.python.org/a",
         kind=Phase5WorkKind.HTTP_RETRIEVAL, tool="http"):
    return prepare_phase5_job(
        organization_id=org, execution_id=execution, crawl_node_id=node,
        original_url=url, source_classification="directory", work_kind=kind,
        selected_tool=tool, requested_at=NOW)


@pytest.mark.asyncio
async def test_concurrent_job_creation_returns_one_logical_record_and_recovers_sessions(
    phase5_sessions,
):
    org, execution, node = await _seed_context(phase5_sessions)
    prepared = _job(org, execution, node)

    async def create():
        async with phase5_sessions.begin() as session:
            return await create_job_idempotently(session, prepared)

    results = await asyncio.gather(create(), create())
    assert {result.outcome for result in results} <= {"created", "existing"}
    assert len({result.record_id for result in results}) == 1
    async with phase5_sessions() as session:
        assert await session.scalar(select(text("1"))) == 1


@pytest.mark.asyncio
async def test_bounded_skip_locked_claims_do_not_overlap_and_isolate_tenants(
    phase5_sessions,
):
    org, execution, node = await _seed_context(phase5_sessions)
    async with phase5_sessions.begin() as session:
        for ordinal in range(6):
            await create_job_idempotently(
                session, _job(org, execution, node,
                              url=f"https://docs.python.org/{ordinal}"))

    async def claim():
        async with phase5_sessions.begin() as session:
            return await claim_batch(
                session, organization_id=org, execution_id=execution, now=NOW,
                lease_duration=timedelta(minutes=1), batch_size=2)

    left, right = await asyncio.gather(claim(), claim())
    assert len(left) == len(right) == 2
    assert {x.id for x in left}.isdisjoint({x.id for x in right})


@pytest.mark.asyncio
async def test_claim_predicate_groups_retry_due_with_tenant_and_safe_url_filters(
    phase5_sessions,
):
    org_a, execution_a, node_a = await _seed_context(phase5_sessions)
    org_b, execution_b, node_b = await _seed_context(phase5_sessions)
    async with phase5_sessions.begin() as session:
        a = await create_job_idempotently(session, _job(org_a, execution_a, node_a))
        b = await create_job_idempotently(session, _job(org_b, execution_b, node_b))
        unsafe = await create_job_idempotently(
            session, _job(org_a, execution_a, node_a, url="http://127.0.0.1/private"))
        for record_id in (a.record_id, b.record_id):
            row = await session.get(ScrapingPhase5WorkJob, record_id)
            row.status = Phase5WorkStatus.RETRY_SCHEDULED
            row.next_retry_at = NOW
        unsafe_row = await session.get(ScrapingPhase5WorkJob, unsafe.record_id)
        assert unsafe_row.status is Phase5WorkStatus.REJECTED
        assert unsafe_row.canonical_url is None
    async with phase5_sessions.begin() as session:
        claimed = await claim_batch(
            session, organization_id=org_a, execution_id=execution_a, now=NOW,
            lease_duration=timedelta(minutes=1), batch_size=10)
    assert {job.id for job in claimed} == {a.record_id}
    assert all(job.organization_id == org_a and job.execution_id == execution_a
               for job in claimed)


@pytest.mark.asyncio
async def test_retry_timing_expired_recovery_active_protection_and_terminal_exclusion(
    phase5_sessions,
):
    org, execution, node = await _seed_context(phase5_sessions)
    async with phase5_sessions.begin() as session:
        result = await create_job_idempotently(session, _job(org, execution, node))
        job = await session.get(ScrapingPhase5WorkJob, result.record_id)
        job.status = Phase5WorkStatus.RETRY_SCHEDULED
        job.next_retry_at = NOW + timedelta(minutes=5)
    async with phase5_sessions.begin() as session:
        assert not await claim_batch(
            session, organization_id=org, execution_id=execution, now=NOW,
            lease_duration=timedelta(minutes=1), batch_size=10)
    async with phase5_sessions.begin() as session:
        job = await session.get(ScrapingPhase5WorkJob, result.record_id)
        job.next_retry_at = NOW
    async with phase5_sessions.begin() as session:
        claimed = await claim_batch(
            session, organization_id=org, execution_id=execution, now=NOW,
            lease_duration=timedelta(minutes=1), batch_size=10)
        assert len(claimed) == 1
    async with phase5_sessions.begin() as session:
        assert await recover_expired_claims(
            session, organization_id=org, execution_id=execution,
            now=NOW + timedelta(seconds=30)) == 0
        assert await recover_expired_claims(
            session, organization_id=org, execution_id=execution,
            now=NOW + timedelta(minutes=2)) == 1
        job = await session.get(ScrapingPhase5WorkJob, result.record_id)
        job.status = Phase5WorkStatus.FAILED
        job.next_retry_at = None
    async with phase5_sessions.begin() as session:
        assert not await claim_batch(
            session, organization_id=org, execution_id=execution,
            now=NOW + timedelta(minutes=3),
            lease_duration=timedelta(minutes=1), batch_size=10)


@pytest.mark.asyncio
async def test_unsafe_and_blocked_jobs_are_never_claimed(phase5_sessions):
    org, execution, node = await _seed_context(phase5_sessions)
    unsafe = _job(org, execution, node, url="http://127.0.0.1/private")
    async with phase5_sessions.begin() as session:
        created = await create_job_idempotently(session, unsafe)
        row = await session.get(ScrapingPhase5WorkJob, created.record_id)
        assert row.status is Phase5WorkStatus.REJECTED
        assert row.canonical_url is row.claim_token is row.lease_expires_at is None
        blocked = await create_job_idempotently(
            session, _job(org, execution, node,
                          url="https://docs.python.org/provider-blocked"))
        blocked_row = await session.get(ScrapingPhase5WorkJob, blocked.record_id)
        blocked_row.status = Phase5WorkStatus.BLOCKED
        blocked_row.last_error_category = "provider_unavailable"
    async with phase5_sessions.begin() as session:
        assert not await claim_batch(
            session, organization_id=org, execution_id=execution, now=NOW,
            lease_duration=timedelta(minutes=1), batch_size=10)


@pytest.mark.asyncio
async def test_multi_result_replay_and_concurrent_result_collision_are_idempotent(
    phase5_sessions,
):
    org, execution, node = await _seed_context(phase5_sessions)
    async with phase5_sessions.begin() as session:
        created = await create_job_idempotently(session, _job(org, execution, node))
    async with phase5_sessions.begin() as session:
        claimed = (await claim_batch(
            session, organization_id=org, execution_id=execution, now=NOW,
            lease_duration=timedelta(minutes=5), batch_size=1))[0]

    def prepared(url, ordinal):
        fingerprint = retrieval_result_fingerprint(
            organization_id=org, execution_id=execution, work_job_id=claimed.id,
            retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL, resource_url=url,
            resource_role="page", result_ordinal=ordinal)
        return PreparedRetrievalResult(
            job_id=claimed.id, organization_id=org, execution_id=execution,
            requested_url=url, final_url=url,
            retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL, fetched_at=NOW,
            result_fingerprint=fingerprint, resource_role="page",
            result_ordinal=ordinal)

    async def persist(value):
        async with phase5_sessions.begin() as session:
            return await persist_retrieval_result(
                session, claim_token=claimed.claim_token, result=value)

    first = prepared("https://docs.python.org/a", 0)
    collision = await asyncio.gather(persist(first), persist(first))
    assert len({x.record_id for x in collision}) == 1
    await persist(prepared("https://docs.python.org/b", 1))
    async with phase5_sessions() as session:
        assert await session.scalar(select(text(
            "count(*)")).select_from(ScrapingPhase5RetrievalResult)) == 2


@pytest.mark.asyncio
async def test_stale_and_expired_tokens_cannot_persist_results(phase5_sessions):
    org, execution, node = await _seed_context(phase5_sessions)
    async with phase5_sessions.begin() as session:
        await create_job_idempotently(session, _job(org, execution, node))
    async with phase5_sessions.begin() as session:
        claimed = (await claim_batch(
            session, organization_id=org, execution_id=execution, now=NOW,
            lease_duration=timedelta(seconds=30), batch_size=1))[0]
    fingerprint = retrieval_result_fingerprint(
        organization_id=org, execution_id=execution, work_job_id=claimed.id,
        retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL,
        resource_url="https://docs.python.org/a", resource_role="page",
        result_ordinal=0)
    prepared = PreparedRetrievalResult(
        job_id=claimed.id, organization_id=org, execution_id=execution,
        requested_url="https://docs.python.org/a",
        retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL, fetched_at=NOW,
        result_fingerprint=fingerprint, resource_role="page", result_ordinal=0)
    async with phase5_sessions.begin() as session:
        assert (await persist_retrieval_result(
            session, claim_token="stale-token", result=prepared)).outcome == "stale_claim"
    async with phase5_sessions.begin() as session:
        await session.execute(text(
            """UPDATE scraping_phase5_work_jobs
               SET claimed_at = clock_timestamp() - interval '2 minutes',
                   lease_expires_at = clock_timestamp() - interval '1 minute'
               WHERE id = :id"""),
            {"id": claimed.id})
    async with phase5_sessions.begin() as session:
        assert (await persist_retrieval_result(
            session, claim_token=claimed.claim_token, result=prepared)).outcome == "stale_claim"
        assert await session.scalar(select(text(
            "count(*)")).select_from(ScrapingPhase5RetrievalResult)) == 0


@pytest.mark.asyncio
async def test_invalid_lease_chronology_is_rejected_by_database(phase5_sessions):
    org, execution, node = await _seed_context(phase5_sessions)
    async with phase5_sessions.begin() as session:
        await create_job_idempotently(session, _job(org, execution, node))
    async with phase5_sessions.begin() as session:
        claimed = (await claim_batch(
            session, organization_id=org, execution_id=execution, now=NOW,
            lease_duration=timedelta(minutes=1), batch_size=1))[0]
    async with phase5_sessions() as session:
        with pytest.raises(IntegrityError, match="ck_phase5_job_lease_after_claim"):
            await session.execute(text(
                """UPDATE scraping_phase5_work_jobs
                   SET lease_expires_at = claimed_at
                   WHERE id = :id"""), {"id": claimed.id})
        await session.rollback()


@pytest.mark.asyncio
async def test_observation_replay_and_concurrent_collision_are_idempotent(phase5_sessions):
    org, execution, node = await _seed_context(phase5_sessions)
    prepared_job = _job(
        org, execution, node, kind=Phase5WorkKind.DIRECTORY_EXPANSION,
        tool="directory_expansion")
    async with phase5_sessions.begin() as session:
        await create_job_idempotently(session, prepared_job)
    async with phase5_sessions.begin() as session:
        claimed = (await claim_batch(
            session, organization_id=org, execution_id=execution, now=NOW,
            lease_duration=timedelta(minutes=5), batch_size=1))[0]
    identity = {
        "organization_id": org, "execution_id": execution,
        "parent_directory_node_id": node,
        "listing_page_url": "https://docs.python.org/a",
        "profile_url": "https://docs.python.org/profile",
        "listing_rank": 1,
    }
    observation = PreparedDirectoryObservation(
        organization_id=org, execution_id=execution, work_job_id=claimed.id,
        displayed_facility_name="Example", listing_page_url=identity["listing_page_url"],
        profile_url=identity["profile_url"], directory_source="Example directory",
        listing_rank=1, parent_directory_node_id=node,
        extraction_method="structured_payload", observed_at=NOW,
        observation_fingerprint=directory_observation_fingerprint(**identity))

    async def persist():
        async with phase5_sessions.begin() as session:
            return await persist_directory_observation(
                session, claim_token=claimed.claim_token, observation=observation)

    results = await asyncio.gather(persist(), persist())
    assert len({x.record_id for x in results}) == 1
    async with phase5_sessions() as session:
        assert await session.scalar(select(text(
            "count(*)")).select_from(ScrapingDirectoryObservation)) == 1


@pytest.mark.asyncio
async def test_composite_ownership_and_duplicate_observation_constraints_fail_closed(
    phase5_sessions,
):
    a = await _seed_full_context(phase5_sessions)
    b = await _seed_full_context(phase5_sessions)
    assert a["org"] != b["org"]
    assert a["execution"] != b["execution"]
    assert a["candidate"] != b["candidate"]

    expected_constraints = {
        "fk_phase5_job_candidate_org_exec": (
            "FOREIGN KEY (source_candidate_id, organization_id, execution_id) "
            "REFERENCES scraping_source_candidates(id, organization_id, execution_id)"
        ),
        "fk_phase5_job_node_org_exec": "FOREIGN KEY (crawl_node_id, organization_id, execution_id)",
        "fk_phase5_job_edge_org_exec": "FOREIGN KEY (crawl_edge_id, organization_id, execution_id)",
        "fk_phase5_job_query_org_exec": "FOREIGN KEY (discovery_query_id, organization_id, execution_id)",
        "fk_phase5_retrieval_job_org_exec": "FOREIGN KEY (work_job_id, organization_id, execution_id)",
        "fk_phase5_retrieval_document_org_exec": "FOREIGN KEY (source_document_id, organization_id, execution_id)",
        "fk_phase5_retrieval_edge_org_exec": "FOREIGN KEY (parent_crawl_edge_id, organization_id, execution_id)",
        "fk_directory_observation_job_org_exec": "FOREIGN KEY (work_job_id, organization_id, execution_id)",
        "fk_directory_observation_parent_node_org_exec": "FOREIGN KEY (parent_directory_node_id, organization_id, execution_id)",
        "fk_directory_observation_profile_node_org_exec": "FOREIGN KEY (emitted_profile_node_id, organization_id, execution_id)",
        "fk_directory_observation_website_node_org_exec": "FOREIGN KEY (emitted_website_node_id, organization_id, execution_id)",
    }
    async with phase5_sessions() as session:
        rows = (await session.execute(text(
            """SELECT conname, condeferrable, condeferred,
                      pg_get_constraintdef(oid) AS definition
               FROM pg_constraint
               WHERE conname = ANY(:names)"""),
            {"names": list(expected_constraints)})).mappings().all()
        candidate_unique = await session.scalar(text(
            """SELECT pg_get_constraintdef(oid)
               FROM pg_constraint
               WHERE conname = 'uq_source_candidate_id_org_exec'"""))
    assert {row["conname"] for row in rows} == set(expected_constraints)
    for row in rows:
        assert row["condeferrable"] is False
        assert row["condeferred"] is False
        assert expected_constraints[row["conname"]] in row["definition"]
    assert candidate_unique == "UNIQUE (id, organization_id, execution_id)"

    async def rejected(row):
        async with phase5_sessions() as session:
            session.add(row)
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()
            assert await session.scalar(select(text("1"))) == 1

    await rejected(ScrapingPhase5WorkJob(
            organization_id=a["org"], execution_id=a["execution"],
            source_candidate_id=b["candidate"], crawl_node_id=a["node"],
            work_kind=DbWorkKind.HTTP_RETRIEVAL, status=Phase5WorkStatus.PENDING,
            original_url="https://docs.python.org/a",
            canonical_url="https://docs.python.org/a",
            source_classification="directory", selected_tool="http",
            fingerprint="1" * 64, requested_at=NOW))
    await rejected(ScrapingPhase5WorkJob(
            organization_id=a["org"], execution_id=a["execution"],
            crawl_node_id=b["node"], work_kind=DbWorkKind.HTTP_RETRIEVAL,
            status=Phase5WorkStatus.PENDING,
            original_url="https://docs.python.org/a",
            canonical_url="https://docs.python.org/a",
            source_classification="directory", selected_tool="http",
            fingerprint="2" * 64, requested_at=NOW))

    async with phase5_sessions.begin() as session:
        created = await create_job_idempotently(
            session, _job(a["org"], a["execution"], a["node"]))
    await rejected(ScrapingPhase5RetrievalResult(
        organization_id=a["org"], execution_id=a["execution"],
        work_job_id=created.record_id, requested_url="https://docs.python.org/a",
        final_url="https://docs.python.org/a", retrieval_method="http_retrieval",
        result_fingerprint="3" * 64, resource_role="page", result_ordinal=0,
        redirect_count=0, fetched_at=NOW, source_document_id=b["document"]))
    await rejected(ScrapingPhase5RetrievalResult(
        organization_id=a["org"], execution_id=a["execution"],
        work_job_id=created.record_id, requested_url="https://docs.python.org/a",
        final_url="https://docs.python.org/a", retrieval_method="http_retrieval",
        result_fingerprint="4" * 64, resource_role="page", result_ordinal=1,
        redirect_count=0, fetched_at=NOW, parent_crawl_edge_id=b["edge"]))
    await rejected(ScrapingDirectoryObservation(
        organization_id=a["org"], execution_id=a["execution"],
        work_job_id=created.record_id, observation_fingerprint="5" * 64,
        listing_page_url="https://docs.python.org/a",
        directory_source="Directory", parent_directory_node_id=a["node"],
        emitted_profile_node_id=b["profile"], extraction_method="structured_payload",
        observed_at=NOW))

    # Node and edge identities are also final database race guards.
    await rejected(ScrapingCrawlNode(
        organization_id=a["org"], execution_id=a["execution"],
        canonical_url="https://docs.python.org/duplicate",
        canonical_url_hash=(await _load_node_hash(phase5_sessions, a["node"])),
        hostname="docs.python.org", domain="python.org",
        source_classification=CrawlNodeSourceClassification.DIRECTORY,
        first_seen_at=NOW))
    await rejected(ScrapingCrawlEdge(
        organization_id=a["org"], execution_id=a["execution"],
        from_node_id=a["node"], to_node_id=a["profile"],
        relationship_type=CrawlEdgeRelationshipType.DIRECTORY_TO_PROFILE))


async def _load_node_hash(maker, node_id):
    async with maker() as session:
        return await session.scalar(select(
            ScrapingCrawlNode.canonical_url_hash).where(ScrapingCrawlNode.id == node_id))
