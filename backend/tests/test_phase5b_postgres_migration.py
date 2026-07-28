"""Unexecuted isolated PostgreSQL coverage for migration 032."""

import pytest

from phase5a_postgres_support import create_phase5_database, drop_phase5_database


@pytest.mark.asyncio
async def test_032_upgrade_downgrade_and_single_head():
    db = await create_phase5_database()
    try:
        await db.alembic("upgrade", "031")
        await db.alembic("upgrade", "032")
        assert "032" in await db.alembic("current")
        heads = await db.alembic("heads")
        assert "032 (head)" in heads
        connection = await db.connect()
        try:
            definition = await connection.fetchval(
                """SELECT pg_get_constraintdef(oid) FROM pg_constraint
                   WHERE conname='ck_crawl_edge_relationship_type'""")
            for value in (
                "directory_to_official_site", "pagination", "load_more",
                "structured_api"):
                assert value in definition
            columns = {row["column_name"] for row in await connection.fetch(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_name='scraping_phase5_work_jobs'""")}
            assert {
                "input_retrieval_result_id", "input_source_document_id",
                "input_content_fingerprint", "input_retrieval_method",
                "next_entry_ordinal", "entries_completed", "expansion_completed",
                "last_processed_slice_count", "expansion_parser_version",
                "parser_state_fingerprint", "expansion_outcome",
                "requires_managed_rendering", "requires_browser_interaction",
                "continuation_markers_json",
            } <= columns
            constraints = {row["conname"]: row["definition"] for row in await connection.fetch(
                """SELECT conname, pg_get_constraintdef(oid) AS definition
                   FROM pg_constraint
                   WHERE conname IN (
                     'fk_phase5_job_input_retrieval_org_exec',
                     'fk_phase5_job_input_document_org_exec',
                     'ck_phase5_job_directory_input',
                     'ck_phase5_job_expansion_cursor')""")}
            assert set(constraints) == {
                "fk_phase5_job_input_retrieval_org_exec",
                "fk_phase5_job_input_document_org_exec",
                "ck_phase5_job_directory_input",
                "ck_phase5_job_expansion_cursor",
            }
        finally:
            await connection.close()
        await db.alembic("downgrade", "031")
        assert "031" in await db.alembic("current")
        await db.alembic("upgrade", "032")
    finally:
        await drop_phase5_database(db)
