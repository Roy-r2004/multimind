"""Isolated PostgreSQL migration contract for revision 033."""

import subprocess
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from phase5a_postgres_support import create_phase5_database, drop_phase5_database
from test_phase4_discovery_results_postgres import _seed_v2_campaign

pytestmark = pytest.mark.asyncio

PACKAGE_A_TABLES = {
    "scraping_facility_phase_work_jobs",
    "scraping_facility_candidate_decisions",
    "scraping_facility_candidate_duplicates",
}
PACKAGE_A_EXISTING_TABLE_COLUMNS = {
    ("scraping_source_document_chunks", "retrieval_result_id"),
    ("scraping_source_document_chunks", "crawl_node_id"),
    ("scraping_source_document_chunks", "original_url"),
    ("scraping_source_document_chunks", "representation_provenance"),
    ("scraping_facility_candidates", "directory_observation_id"),
    ("scraping_facility_candidate_evidence", "retrieval_result_id"),
    ("scraping_facility_candidate_evidence", "crawl_node_id"),
    ("scraping_facility_candidate_evidence", "source_url"),
}
PACKAGE_A_EXISTING_TABLE_CONSTRAINTS = {
    "uq_source_document_chunk_owner",
    "fk_chunk_retrieval_result",
    "fk_chunk_crawl_node",
    "uq_facility_candidate_owner",
    "fk_candidate_directory_observation",
    "fk_evidence_retrieval_result",
    "fk_evidence_crawl_node",
}
PACKAGE_A_INDEXES = {
    "uq_source_document_chunk_owner",
    "uq_facility_candidate_owner",
    "ix_facility_phase_jobs_claim",
    "ix_facility_phase_jobs_lease",
    "ix_facility_candidate_decisions_status",
    "ix_facility_candidate_identity",
    "ix_facility_candidate_duplicates_execution",
}


async def test_033_upgrade_schema_one_head_and_empty_downgrade() -> None:
    db = await create_phase5_database()
    try:
        await db.alembic("upgrade", "032")
        await db.alembic("upgrade", "033")
        connection = await db.connect()
        try:
            assert await connection.fetchval("SELECT version_num FROM alembic_version") == "033"
            tables = {row["tablename"] for row in await connection.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename LIKE 'scraping_facility_%'"
            )}
            assert PACKAGE_A_TABLES <= tables
            columns = {(row["table_name"], row["column_name"]) for row in await connection.fetch(
                "SELECT table_name,column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name IN "
                "('scraping_source_document_chunks','scraping_facility_candidates',"
                "'scraping_facility_candidate_evidence')"
            )}
            assert PACKAGE_A_EXISTING_TABLE_COLUMNS <= columns
            names = {row["name"] for row in await connection.fetch(
                "SELECT conname AS name FROM pg_constraint WHERE conrelid IN "
                "('scraping_facility_phase_work_jobs'::regclass,"
                "'scraping_facility_candidate_decisions'::regclass,"
                "'scraping_facility_candidate_duplicates'::regclass) "
                "UNION SELECT indexname AS name FROM pg_indexes WHERE schemaname='public' "
                "AND (indexname LIKE '%facility_phase%' OR indexname LIKE '%facility_candidate%')"
            )}
            assert {
                "uq_facility_phase_job_fingerprint", "uq_facility_phase_job_owner",
                "ck_facility_phase_job_kind", "ck_facility_phase_job_status",
                "uq_facility_candidate_decision", "ck_facility_candidate_country_decision",
                "ck_facility_candidate_final_status",
                "uq_facility_candidate_duplicate_pair",
                "ck_facility_candidate_duplicate_order",
                "ck_facility_candidate_duplicate_relationship",
                "ix_facility_phase_jobs_claim", "ix_facility_phase_jobs_lease",
                "ix_facility_candidate_decisions_status", "ix_facility_candidate_identity",
                "ix_facility_candidate_duplicates_execution",
            } <= names
            foreign_keys = await connection.fetchval(
                "SELECT count(*) FROM pg_constraint WHERE contype='f' "
                "AND conrelid IN ('scraping_facility_phase_work_jobs'::regclass,"
                "'scraping_facility_candidate_decisions'::regclass,"
                "'scraping_facility_candidate_duplicates'::regclass)"
            )
            assert foreign_keys >= 12
        finally:
            await connection.close()
        heads = await db.alembic("heads")
        assert heads.count("(head)") == 1
        assert "035" in heads
        await db.alembic("downgrade", "032")
        connection = await db.connect()
        try:
            assert await connection.fetchval(
                "SELECT version_num FROM alembic_version"
            ) == "032"
            for table in PACKAGE_A_TABLES:
                assert await connection.fetchval(
                    "SELECT to_regclass($1)", f"public.{table}"
                ) is None
            remaining_columns = {(row["table_name"], row["column_name"])
                                 for row in await connection.fetch(
                "SELECT table_name,column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND (table_name,column_name) IN ("
                "('scraping_source_document_chunks','retrieval_result_id'),"
                "('scraping_source_document_chunks','crawl_node_id'),"
                "('scraping_source_document_chunks','original_url'),"
                "('scraping_source_document_chunks','representation_provenance'),"
                "('scraping_facility_candidates','directory_observation_id'),"
                "('scraping_facility_candidate_evidence','retrieval_result_id'),"
                "('scraping_facility_candidate_evidence','crawl_node_id'),"
                "('scraping_facility_candidate_evidence','source_url'))"
            )}
            assert remaining_columns == set()
            remaining_constraints = {row["conname"] for row in await connection.fetch(
                "SELECT conname FROM pg_constraint WHERE conname = ANY($1::text[])",
                list(PACKAGE_A_EXISTING_TABLE_CONSTRAINTS),
            )}
            assert remaining_constraints == set()
            remaining_indexes = {row["indexname"] for row in await connection.fetch(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname='public' AND indexname = ANY($1::text[])",
                list(PACKAGE_A_INDEXES),
            )}
            assert remaining_indexes == set()
            remaining_row_types = {row["typname"] for row in await connection.fetch(
                "SELECT typname FROM pg_type WHERE typname = ANY($1::text[])",
                list(PACKAGE_A_TABLES),
            )}
            assert remaining_row_types == set()
        finally:
            await connection.close()
        await db.alembic("upgrade", "033")
    finally:
        await drop_phase5_database(db)


async def test_033_downgrade_refuses_durable_package_a_rows() -> None:
    db = await create_phase5_database()
    try:
        await db.alembic("upgrade", "033")
        engine = create_async_engine(db.url.replace("postgresql://", "postgresql+asyncpg://"))
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker.begin() as session:
            org, execution = await _seed_v2_campaign(session)
        connection = await db.connect()
        try:
            await connection.execute(
                "INSERT INTO scraping_facility_phase_work_jobs "
                "(id,organization_id,execution_id,work_kind,fingerprint,status,"
                "attempt_count,max_attempts,metadata_json) "
                "VALUES ($1,$2,$3,'prepare_document',$4,'pending',0,3,'{}')",
                str(uuid.uuid4()), org.id, execution.id, "a" * 64,
            )
        finally:
            await connection.close()
            await engine.dispose()
        with pytest.raises(subprocess.CalledProcessError):
            await db.alembic("downgrade", "032")
    finally:
        await drop_phase5_database(db)
