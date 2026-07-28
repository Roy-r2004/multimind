"""Unexecuted Phase 5B PostgreSQL atomicity/idempotency coverage."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    CrawlEdgeRelationshipType, ScrapingCrawlEdge, ScrapingCrawlNode,
    ScrapingDirectoryObservation, ScrapingPhase5WorkJob,
    Phase5WorkKind as DbWorkKind, Phase5WorkStatus,
)
from app.services.scraping.directory_expansion_service import (
    PreparedDirectoryContent, PreparedContinuation,
    identify_and_prepare_directory,
    persist_prepared_expansion,
    reload_prepared_directory_content,
)
from app.services.scraping.phase5_contracts import (
    Phase5WorkKind, prepare_phase5_job,
    directory_observation_fingerprint,
)
from app.services.scraping.phase5_job_service import (
    claim_batch, create_job_idempotently,
    record_retryable_failure,
)
from app.services.scraping.phase5_contracts import RetryableFailure
from phase5a_postgres_support import create_phase5_database, drop_phase5_database
from phase5_postgres_fixtures import (
    assert_claim_live,
    fetch_database_now,
    seed_phase5_retrieval_bundle,
)
from test_phase5a_postgres import (
    _claim_jobs, _seed_full_context, _seed_retrieval_ready_context,
)

NOW = datetime(2026, 7, 28, 12, tzinfo=UTC)
DEFAULT_PAYLOAD = {
    "items": [
        {"name": "One", "url": "/profile/one",
         "website": "https://www.python.org"},
        {"name": "Branch", "url": "/profile/one", "address": "Branch B"},
    ],
    "next": "/list?page=2",
}


@pytest.fixture
async def phase5b_sessions() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    db = await create_phase5_database()
    engine = None
    try:
        await db.alembic("upgrade", "032")
        engine = create_async_engine(db.url.replace("postgresql://", "postgresql+asyncpg://"))
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        if engine is not None:
            await engine.dispose()
        await drop_phase5_database(db)


async def _claimed_directory(maker, payload=None):
    org, execution, node, candidate = await _seed_retrieval_ready_context(maker)
    payload = payload or DEFAULT_PAYLOAD
    listing_url = "https://docs.python.org/list"
    bundle = await seed_phase5_retrieval_bundle(
        maker,
        organization_id=org,
        execution_id=execution,
        crawl_node_id=node,
        source_candidate_id=candidate,
        listing_url=listing_url,
        structured_json=payload,
    )
    prepared = prepare_phase5_job(
        organization_id=org, execution_id=execution, crawl_node_id=node,
        original_url=listing_url,
        source_classification="directory",
        work_kind=Phase5WorkKind.DIRECTORY_EXPANSION,
        selected_tool="directory_expansion",
        requested_at=bundle.claim.lease_expires_at - timedelta(minutes=4),
        input_retrieval_result_id=bundle.retrieval_result_id,
        input_source_document_id=bundle.source_document_id,
        input_content_fingerprint=bundle.content_fingerprint,
        input_retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL)
    async with maker.begin() as session:
        await create_job_idempotently(session, prepared)
    claim_now, claimed = await _claim_jobs(
        maker, org, execution, lease_minutes=5,
        selected_tool="directory_expansion")
    claimed_job = claimed[0]
    source = PreparedDirectoryContent(
        job_id=claimed_job.id, organization_id=org, execution_id=execution,
        claim_token=claimed_job.claim_token, parent_crawl_node_id=node,
        input_retrieval_result_id=bundle.retrieval_result_id,
        input_source_document_id=bundle.source_document_id,
        input_retrieval_method="http_retrieval",
        listing_page_original_url=listing_url,
        listing_page_canonical_url=listing_url,
        content_type="application/json",
        structured_json=payload,
        content_fingerprint=bundle.content_fingerprint, observed_at=claim_now)
    return claimed_job, identify_and_prepare_directory(source)


@pytest.mark.asyncio
async def test_atomic_persistence_node_reuse_edge_and_observation_uniqueness(phase5b_sessions):
    claimed, prepared = await _claimed_directory(phase5b_sessions)
    async with phase5b_sessions.begin() as session:
        result = await persist_prepared_expansion(session, prepared)
    assert result.outcome == "persisted"
    async with phase5b_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(
            ScrapingDirectoryObservation)) == 2
        observation_profile_ids = (await session.scalars(select(
            ScrapingDirectoryObservation.emitted_profile_node_id))).all()
        assert len(observation_profile_ids) == 2
        assert len(set(observation_profile_ids)) == 1
        sources = (await session.scalars(select(
            ScrapingDirectoryObservation.directory_source))).all()
        assert set(sources) == {"docs.python.org"}
        # Two observations reuse one profile node; website and pagination are separate.
        assert await session.scalar(select(func.count()).select_from(
            ScrapingCrawlNode)) == 4
        assert await session.scalar(select(func.count()).select_from(
            ScrapingCrawlEdge)) == 3
        relationship_types = set((await session.scalars(select(
            ScrapingCrawlEdge.relationship_type))).all())
        assert relationship_types == {
            CrawlEdgeRelationshipType.DIRECTORY_TO_PROFILE,
            CrawlEdgeRelationshipType.PROFILE_TO_OFFICIAL_SITE,
            CrawlEdgeRelationshipType.PAGINATION,
        }
        assert await session.scalar(select(func.count()).select_from(
            ScrapingCrawlEdge).where(
                ScrapingCrawlEdge.relationship_type
                == CrawlEdgeRelationshipType.DIRECTORY_TO_PROFILE)) == 1
        job = await session.get(ScrapingPhase5WorkJob, claimed.id)
        assert job.status.value == "succeeded"


@pytest.mark.asyncio
async def test_concurrent_replay_is_idempotent_and_transaction_recovers(phase5b_sessions):
    claimed, prepared = await _claimed_directory(phase5b_sessions)
    assert [item.canonical_profile_url for item in prepared.listings] == [
        "https://docs.python.org/profile/one",
        "https://docs.python.org/profile/one",
    ]

    async def persist():
        async with phase5b_sessions.begin() as session:
            return await persist_prepared_expansion(session, prepared)

    first, second = await asyncio.gather(persist(), persist())
    assert {first.outcome, second.outcome} <= {"persisted", "stale_claim"}
    async with phase5b_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(
            ScrapingDirectoryObservation)) == 2
        profile_destinations = (await session.scalars(select(
            ScrapingCrawlEdge.to_node_id).where(
                ScrapingCrawlEdge.relationship_type
                == CrawlEdgeRelationshipType.DIRECTORY_TO_PROFILE))).all()
        assert len(profile_destinations) == 1
        assert len(set(profile_destinations)) == 1
        duplicate_rows = (await session.execute(select(
            ScrapingCrawlNode.id,
            ScrapingCrawlNode.organization_id,
            ScrapingCrawlNode.execution_id,
            ScrapingCrawlNode.canonical_url,
            ScrapingCrawlNode.canonical_url_hash,
            ScrapingCrawlNode.source_classification,
        ).where(
            ScrapingCrawlNode.organization_id == claimed.organization_id,
            ScrapingCrawlNode.execution_id == claimed.execution_id,
            ScrapingCrawlNode.canonical_url
            == "https://docs.python.org/profile/one"))).all()
        assert len(duplicate_rows) == 1, duplicate_rows
        indexes = (await session.execute(text("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename = 'scraping_crawl_nodes'
            ORDER BY indexname
        """))).all()
        canonical_index = next(
            (row for row in indexes
             if row.indexname == "uq_crawl_node_org_exec_url_hash"), None)
        assert canonical_index is not None, indexes
        assert "CREATE UNIQUE INDEX" in canonical_index.indexdef
        assert (
            "(organization_id, execution_id, canonical_url_hash)"
            in canonical_index.indexdef
        )
        assert await session.scalar(select(func.count())) == 1


@pytest.mark.asyncio
async def test_distinct_canonical_profile_urls_create_distinct_nodes(phase5b_sessions):
    claimed, prepared = await _claimed_directory(phase5b_sessions, {
        "items": [
            {"name": "One", "url": "/profile/one"},
            {"name": "Two", "url": "/profile/two"},
        ]})
    async with phase5b_sessions.begin() as session:
        assert (await persist_prepared_expansion(session, prepared)).outcome == "persisted"
    async with phase5b_sessions() as session:
        destinations = (await session.scalars(select(
            ScrapingCrawlEdge.to_node_id).where(
                ScrapingCrawlEdge.organization_id == claimed.organization_id,
                ScrapingCrawlEdge.execution_id == claimed.execution_id,
                ScrapingCrawlEdge.relationship_type
                == CrawlEdgeRelationshipType.DIRECTORY_TO_PROFILE))).all()
        assert len(destinations) == 2
        assert len(set(destinations)) == 2


@pytest.mark.asyncio
async def test_stale_expired_claim_zero_writes_and_retry_only_changes_job(phase5b_sessions):
    claimed, prepared = await _claimed_directory(phase5b_sessions)
    stale = prepared.model_copy(update={
        "source": prepared.source.model_copy(update={"claim_token": "stale"})})
    async with phase5b_sessions.begin() as session:
        result = await persist_prepared_expansion(session, stale)
        assert result.outcome == "stale_claim"
    async with phase5b_sessions.begin() as session:
        await session.execute(
            ScrapingPhase5WorkJob.__table__.update().where(
                ScrapingPhase5WorkJob.id == claimed.id).values(
                    claimed_at=func.clock_timestamp() - timedelta(minutes=2),
                    lease_expires_at=func.clock_timestamp() - timedelta(minutes=1)))
    async with phase5b_sessions.begin() as session:
        assert (await persist_prepared_expansion(session, prepared)).outcome == "stale_claim"
    async with phase5b_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(
            ScrapingDirectoryObservation)) == 0


@pytest.mark.asyncio
async def test_directory_retry_transition_is_individual(phase5b_sessions):
    claimed, _ = await _claimed_directory(phase5b_sessions)
    async with phase5b_sessions.begin() as session:
        retry_at = await fetch_database_now(session) + timedelta(minutes=1)
        outcome = await record_retryable_failure(
            session, job_id=claimed.id, organization_id=claimed.organization_id,
            execution_id=claimed.execution_id, claim_token=claimed.claim_token,
            failure=RetryableFailure(
                category="internal_parser_failure",
                public_message="Directory parsing will be retried.",
                next_retry_at=retry_at))
        assert outcome.outcome == "persisted"


@pytest.mark.asyncio
async def test_restart_continues_beyond_10000_and_completes_final_slice(phase5b_sessions):
    payload = {"items": [
        {"name": f"Facility {index}", "url": f"/profile/{index}"}
        for index in range(10001)
    ]}
    first_claim, first = await _claimed_directory(phase5b_sessions, payload)
    async with phase5b_sessions.begin() as session:
        first_result = await persist_prepared_expansion(session, first)
        assert first_result.observation_count == 2000
    async with phase5b_sessions() as session:
        job = await session.get(ScrapingPhase5WorkJob, first_claim.id)
        assert job.status.value == "pending"
        assert job.next_entry_ordinal == job.entries_completed == 2000
        assert not job.expansion_completed
    del first
    slice_counts = [2000]
    for slice_number in range(1, 6):
        async with phase5b_sessions.begin() as session:
            claim_now = await fetch_database_now(session)
            claimed = (await claim_batch(
                session, organization_id=first_claim.organization_id,
                execution_id=first_claim.execution_id,
                now=claim_now,
                lease_duration=timedelta(minutes=5), batch_size=1,
                selected_tool="directory_expansion"))[0]
            await assert_claim_live(session, claimed)
        async with phase5b_sessions() as restarted_session:
            reloaded = await reload_prepared_directory_content(
                restarted_session, claimed_job=claimed,
                observed_at=claim_now)
        continuation = identify_and_prepare_directory(reloaded)
        async with phase5b_sessions.begin() as session:
            persisted = await persist_prepared_expansion(session, continuation)
            slice_counts.append(persisted.observation_count)
    assert slice_counts == [2000] * 5 + [1]
    async with phase5b_sessions() as session:
        job = await session.get(ScrapingPhase5WorkJob, first_claim.id)
        assert job.status.value == "succeeded"
        assert job.expansion_completed
        assert job.entries_completed == 10001
        assert await session.scalar(select(func.count()).select_from(
            ScrapingDirectoryObservation)) == 10001


@pytest.mark.asyncio
async def test_input_retrieval_and_document_composite_ownership_fail_closed(
    phase5b_sessions,
):
    claim_a, prepared_a = await _claimed_directory(phase5b_sessions)
    claim_b, _ = await _claimed_directory(phase5b_sessions)
    assert claim_a.organization_id != claim_b.organization_id
    other = await _seed_full_context(phase5b_sessions)

    async def reject(row):
        async with phase5b_sessions() as session:
            session.add(row)
            with pytest.raises(IntegrityError):
                await session.flush()
            await session.rollback()

    base = dict(
        organization_id=claim_a.organization_id,
        execution_id=claim_a.execution_id,
        crawl_node_id=claim_a.crawl_node_id,
        work_kind=DbWorkKind.DIRECTORY_EXPANSION,
        status=Phase5WorkStatus.PENDING,
        original_url="https://docs.python.org/list",
        canonical_url="https://docs.python.org/list",
        source_classification="directory", selected_tool="directory_expansion",
        input_content_fingerprint=prepared_a.source.content_fingerprint,
        input_retrieval_method="http_retrieval", attempt_count=0,
        requested_at=NOW)
    await reject(ScrapingPhase5WorkJob(
        **base, input_retrieval_result_id=claim_b.input_retrieval_result_id,
        fingerprint="1" * 64))
    await reject(ScrapingPhase5WorkJob(
        **base, input_retrieval_result_id=claim_a.input_retrieval_result_id,
        input_source_document_id=other["document"], fingerprint="2" * 64))


@pytest.mark.asyncio
async def test_bound_content_fingerprint_mismatch_writes_nothing(phase5b_sessions):
    _, prepared = await _claimed_directory(phase5b_sessions)
    tampered = prepared.model_copy(update={
        "source": prepared.source.model_copy(update={
            "content_fingerprint": "f" * 64})})
    async with phase5b_sessions.begin() as session:
        result = await persist_prepared_expansion(session, tampered)
        assert result.outcome == "input_mismatch"
    async with phase5b_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(
            ScrapingDirectoryObservation)) == 0


@pytest.mark.asyncio
async def test_browser_required_marker_is_durable(phase5b_sessions):
    claim, prepared = await _claimed_directory(phase5b_sessions)
    marker = PreparedContinuation(
        relationship=CrawlEdgeRelationshipType.LOAD_MORE,
        requires_browser_interaction=True)
    prepared = prepared.model_copy(update={
        "continuations": (*prepared.continuations, marker)})
    async with phase5b_sessions.begin() as session:
        await persist_prepared_expansion(session, prepared)
    async with phase5b_sessions() as session:
        job = await session.get(ScrapingPhase5WorkJob, claim.id)
        assert job.requires_browser_interaction
        assert any(item["requires_browser_interaction"]
                   for item in job.continuation_markers_json)


@pytest.mark.asyncio
async def test_shared_topology_retains_two_directory_observation_provenances(
    phase5b_sessions,
):
    claim, prepared = await _claimed_directory(phase5b_sessions, {
        "items": [{"name": "One", "url": "/profile/shared",
                   "website": "https://www.python.org"}]})
    async with phase5b_sessions.begin() as session:
        await persist_prepared_expansion(session, prepared)
    async with phase5b_sessions.begin() as session:
        first = await session.scalar(select(ScrapingDirectoryObservation))
        second_directory = ScrapingCrawlNode(
            organization_id=claim.organization_id,
            execution_id=claim.execution_id,
            canonical_url="https://docs.python.org/second-directory",
            canonical_url_hash="9" * 64, hostname="docs.python.org",
            domain="python.org", source_classification="directory",
            first_seen_at=NOW)
        session.add(second_directory)
        await session.flush()
        fingerprint = directory_observation_fingerprint(
            organization_id=claim.organization_id,
            execution_id=claim.execution_id,
            parent_directory_node_id=second_directory.id,
            listing_page_url=second_directory.canonical_url,
            profile_url=first.profile_url,
            official_website_url=first.official_website_url,
            displayed_facility_name=first.displayed_facility_name,
            displayed_address=first.displayed_address, listing_rank=1)
        session.add(ScrapingDirectoryObservation(
            organization_id=claim.organization_id,
            execution_id=claim.execution_id, work_job_id=claim.id,
            observation_fingerprint=fingerprint,
            displayed_facility_name=first.displayed_facility_name,
            listing_page_url=second_directory.canonical_url,
            profile_url=first.profile_url,
            official_website_url=first.official_website_url,
            directory_source="python.org", listing_rank=1,
            parent_directory_node_id=second_directory.id,
            emitted_profile_node_id=first.emitted_profile_node_id,
            emitted_website_node_id=first.emitted_website_node_id,
            extraction_method="json_collection", observed_at=NOW))
    async with phase5b_sessions() as session:
        observations = (await session.scalars(select(
            ScrapingDirectoryObservation))).all()
        assert len(observations) == 2
        assert len({row.parent_directory_node_id for row in observations}) == 2
        assert len({row.emitted_profile_node_id for row in observations}) == 1
        assert len({row.emitted_website_node_id for row in observations}) == 1
        assert await session.scalar(select(func.count()).select_from(
            ScrapingCrawlEdge).where(
                ScrapingCrawlEdge.relationship_type
                == CrawlEdgeRelationshipType.PROFILE_TO_OFFICIAL_SITE)) == 1
