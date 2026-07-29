"""Unexecuted-by-Codex isolated PostgreSQL migration matrix for revision 031."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    CrawlEdgeRelationshipType, CrawlNodeSourceClassification,
    ScrapingCrawlEdge, ScrapingCrawlNode, ScrapingSourceCandidate,
    ScrapingSourceDocument, ScrapingSourceRetrievalAttempt,
    SourceCandidateStatus, SourceRetrievalAttemptStatus,
)
from test_phase4_discovery_results_postgres import (
    FIXED_NOW, _add_running_query, _seed_v2_campaign,
)

from phase5a_postgres_support import create_phase5_database, drop_phase5_database

pytestmark = pytest.mark.asyncio


async def test_031_fresh_upgrade_downgrade_reupgrade_and_one_head():
    db = await create_phase5_database()
    try:
        await db.alembic("upgrade", "031")
        connection = await db.connect()
        try:
            assert await connection.fetchval(
                "SELECT version_num FROM alembic_version") == "031"
            assert await connection.fetchval(
                "SELECT count(*) FROM pg_tables WHERE schemaname='public' "
                "AND tablename LIKE 'scraping_phase5_%'") == 2
            constraints = {row["conname"] for row in await connection.fetch(
                "SELECT conname FROM pg_constraint WHERE conname IN "
                "('uq_crawl_edge_id_org_exec','uq_source_document_id_org_exec')")}
            assert constraints == {"uq_crawl_edge_id_org_exec", "uq_source_document_id_org_exec"}
        finally:
            await connection.close()
        heads = await db.alembic("heads")
        assert heads.count("(head)") == 1
        assert "035 (head)" in heads
        await db.alembic("downgrade", "030")
        connection = await db.connect()
        try:
            assert await connection.fetchval(
                "SELECT to_regclass('scraping_phase5_work_jobs')") is None
            assert await connection.fetchval(
                "SELECT count(*) FROM pg_constraint WHERE conname IN "
                "('uq_crawl_edge_id_org_exec','uq_source_document_id_org_exec')") == 0
        finally:
            await connection.close()
        await db.alembic("upgrade", "031")
    finally:
        await drop_phase5_database(db)


async def test_031_upgrade_preserves_phase4_rows_and_downgrade_removes_phase5_in_order():
    db = await create_phase5_database()
    try:
        await db.alembic("upgrade", "030")
        engine = create_async_engine(
            db.url.replace("postgresql://", "postgresql+asyncpg://"))
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker.begin() as session:
            org, execution = await _seed_v2_campaign(session)
            query = await _add_running_query(
                session, org_id=org.id, execution_id=execution.id)
            node = ScrapingCrawlNode(
                organization_id=org.id, execution_id=execution.id,
                canonical_url="https://docs.python.org/directory",
                canonical_url_hash="a" * 64, hostname="docs.python.org",
                domain="python.org",
                source_classification=CrawlNodeSourceClassification.DIRECTORY,
                first_seen_at=FIXED_NOW)
            session.add(node)
            await session.flush()
            candidate = ScrapingSourceCandidate(
                organization_id=org.id, execution_id=execution.id,
                discovery_query_id=query.id, crawl_node_id=node.id,
                provider="serper", rank=1,
                url=node.canonical_url, canonical_url=node.canonical_url,
                domain="python.org", title="Directory", snippet="Listing",
                country_code="LB", country_name="Lebanon", region_name=None,
                language_code="en", language_name="English",
                source_category="directory", initial_relevance_score=0.8,
                initial_trust_tier="medium", status=SourceCandidateStatus.DISCOVERED,
                discovered_at=FIXED_NOW, metadata_json={})
            session.add(candidate)
            await session.flush()
            profile = ScrapingCrawlNode(
                organization_id=org.id, execution_id=execution.id,
                canonical_url="https://docs.python.org/profile",
                canonical_url_hash="b" * 64, hostname="docs.python.org",
                domain="python.org",
                source_classification=CrawlNodeSourceClassification.FACILITY_PROFILE,
                first_seen_at=FIXED_NOW)
            session.add(profile)
            await session.flush()
            edge = ScrapingCrawlEdge(
                organization_id=org.id, execution_id=execution.id,
                from_node_id=node.id, to_node_id=profile.id,
                relationship_type=CrawlEdgeRelationshipType.DIRECTORY_TO_PROFILE,
                discovery_query_id=query.id, source_candidate_id=candidate.id)
            session.add(edge)
            attempt = ScrapingSourceRetrievalAttempt(
                organization_id=org.id, execution_id=execution.id,
                source_candidate_id=candidate.id,
                status=SourceRetrievalAttemptStatus.SUCCEEDED,
                requested_url=node.canonical_url, final_url=node.canonical_url,
                redirect_count=0, http_status=200, content_type="text/html",
                declared_content_length=4, bytes_received=4,
                started_at=FIXED_NOW, completed_at=FIXED_NOW,
                idempotency_key="migration-031-attempt", metadata_json={})
            session.add(attempt)
            await session.flush()
            document = ScrapingSourceDocument(
                organization_id=org.id, execution_id=execution.id,
                source_candidate_id=candidate.id, retrieval_attempt_id=attempt.id,
                final_url=node.canonical_url, content_type="text/html",
                content_sha256="c" * 64, content_text="test", byte_size=4,
                retrieval_timestamp=FIXED_NOW, metadata_json={})
            session.add(document)
            await session.flush()
            ids = (org.id, execution.id, query.id, node.id, candidate.id,
                   edge.id, document.id)
        await engine.dispose()
        connection = await db.connect()
        try:
            assert await connection.fetchval(
                "SELECT count(*) FROM scraping_source_documents") == 1
        finally:
            await connection.close()
        await db.alembic("upgrade", "031")
        connection = await db.connect()
        try:
            assert await connection.fetchval(
                "SELECT count(*) FROM scraping_source_documents WHERE id=$1",
                ids[-1]) == 1
            await connection.execute(
                """INSERT INTO scraping_phase5_work_jobs (
                    id,organization_id,execution_id,source_candidate_id,crawl_node_id,
                    crawl_edge_id,discovery_query_id,work_kind,status,original_url,
                    canonical_url,source_classification,selected_tool,fingerprint,
                    attempt_count,requested_at,operational_metadata_json)
                   VALUES ('phase5-job',$1,$2,$3,$4,$5,$6,'http_retrieval','pending',
                    'https://docs.python.org/directory','https://docs.python.org/directory',
                    'directory','http',$7,0,now(),'{}')""",
                ids[0], ids[1], ids[4], ids[3], ids[5], ids[2], "d" * 64)
            await connection.execute(
                """INSERT INTO scraping_phase5_retrieval_results (
                    id,organization_id,execution_id,work_job_id,requested_url,
                    final_url,result_fingerprint,resource_role,result_ordinal,
                    retrieval_method,redirect_count,fetched_at,source_document_id,
                    parent_crawl_edge_id,operational_metadata_json)
                   VALUES ('phase5-result',$1,$2,'phase5-job',
                    'https://docs.python.org/directory',
                    'https://docs.python.org/directory',$3,'page',0,
                    'http_retrieval',0,now(),$4,$5,'{}')""",
                ids[0], ids[1], "e" * 64, ids[6], ids[5])
            await connection.execute(
                """INSERT INTO scraping_directory_observations (
                    id,organization_id,execution_id,work_job_id,
                    observation_fingerprint,listing_page_url,directory_source,
                    parent_directory_node_id,emitted_profile_node_id,
                    extraction_method,observed_at)
                   VALUES ('phase5-observation',$1,$2,'phase5-job',$3,
                    'https://docs.python.org/directory','Migration fixture',$4,$5,
                    'structured_payload',now())""",
                ids[0], ids[1], "f" * 64, ids[3], profile.id)
        finally:
            await connection.close()
        await db.alembic("downgrade", "030")
        await db.alembic("upgrade", "031")
    finally:
        await drop_phase5_database(db)
