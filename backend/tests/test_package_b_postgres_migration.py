"""PostgreSQL contract for Package B revision 035."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from phase5a_postgres_support import create_phase5_database, drop_phase5_database

def test_035_is_linear_and_has_a_populated_state_downgrade_preflight():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/035_package_b_publication_export_lifecycle.py"
    )
    spec = importlib.util.spec_from_file_location("migration_035", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.revision == "035"
    assert module.down_revision == "034"
    source = path.read_text(encoding="utf-8")
    assert "publish_candidate" in source
    assert "generate_execution_export" in source
    assert "finalize_execution" in source
    assert "Refusing migration 035 downgrade" in source


@pytest.mark.asyncio
async def test_035_upgrade_empty_downgrade_reupgrade_and_one_head():
    database = await create_phase5_database()
    try:
        await database.alembic("upgrade", "035")
        connection = await database.connect()
        try:
            assert await connection.fetchval(
                "SELECT version_num FROM alembic_version"
            ) == "035"
            assert await connection.fetchval(
                "SELECT to_regclass('scraping_execution_exports')"
            ) == "scraping_execution_exports"
            constraints = {
                row["conname"]
                for row in await connection.fetch(
                    "SELECT conname FROM pg_constraint WHERE conname IN "
                    "('ck_facility_phase_job_kind',"
                    "'uq_scraping_execution_export_kind',"
                    "'uq_scraping_execution_export_owner',"
                    "'ck_scraping_execution_export_status',"
                    "'ck_scraping_execution_export_succeeded')"
                )
            }
            assert constraints == {
                "ck_facility_phase_job_kind",
                "uq_scraping_execution_export_kind",
                "uq_scraping_execution_export_owner",
                "ck_scraping_execution_export_status",
                "ck_scraping_execution_export_succeeded",
            }
            definition = await connection.fetchval(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname='ck_facility_phase_job_kind'"
            )
            for work_kind in (
                "publish_candidate",
                "generate_execution_export",
                "finalize_execution",
            ):
                assert work_kind in definition
        finally:
            await connection.close()
        heads = await database.alembic("heads")
        assert [line.strip() for line in heads.splitlines() if "(head)" in line] == [
            "035 (head)"
        ]
        await database.alembic("downgrade", "034")
        connection = await database.connect()
        try:
            assert await connection.fetchval(
                "SELECT to_regclass('scraping_execution_exports')"
            ) is None
            assert await connection.fetchval(
                "SELECT version_num FROM alembic_version"
            ) == "034"
        finally:
            await connection.close()
        await database.alembic("upgrade", "035")
    finally:
        await drop_phase5_database(database)
