"""PostgreSQL coverage for migration 028 deterministic query-job schema."""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import asyncpg
import pytest


@dataclass
class PostgresMigrationDatabase:
    admin: asyncpg.Connection
    database: str
    url: str

    async def alembic(self, *arguments: str) -> str:
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

    async def connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(self.url)


@pytest.fixture
async def postgres_migration_database() -> AsyncGenerator[PostgresMigrationDatabase, None]:
    admin_url = os.environ.get("POSTGRES_TEST_ADMIN_URL")
    if not admin_url:
        pytest.fail("POSTGRES_TEST_ADMIN_URL is required for PostgreSQL migration coverage.")

    database = f"migration_028_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(admin_url)
    await admin.execute(f'CREATE DATABASE "{database}"')
    migration_database = PostgresMigrationDatabase(
        admin=admin,
        database=database,
        url=admin_url.rsplit("/", 1)[0] + f"/{database}",
    )
    try:
        yield migration_database
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
            database,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
        await admin.close()


async def _seed_org(connection: asyncpg.Connection) -> str:
    org_id = str(uuid.uuid4())
    await connection.execute(
        """
        INSERT INTO organizations (id, name, slug)
        VALUES ($1, 'Migration 028 org', $2)
        """,
        org_id,
        f"mig028-{uuid.uuid4().hex[:8]}",
    )
    return org_id


async def _seed_execution(connection: asyncpg.Connection, org_id: str) -> str:
    """Create a minimal valid ScrapingExecution chain for plan-backed query rows."""
    user_id = str(uuid.uuid4())
    mission_id = str(uuid.uuid4())
    blueprint_id = str(uuid.uuid4())
    execution_id = str(uuid.uuid4())
    await connection.execute(
        """
        INSERT INTO users (id, email, hashed_password, full_name)
        VALUES ($1, $2, 'x', 'Migration 028 Tester')
        """,
        user_id,
        f"{uuid.uuid4().hex}@example.test",
    )
    await connection.execute(
        """
        INSERT INTO scraping_missions (
            id, org_id, created_by, model_set_id, title, original_prompt,
            status, country_code, country_name
        )
        VALUES ($1, $2, $3, 'mig028-set', 'Migration 028', 'Plan', 'draft', 'LB', 'Lebanon')
        """,
        mission_id,
        org_id,
        user_id,
    )
    await connection.execute(
        """
        INSERT INTO scraping_blueprints (
            id, mission_id, version, status, model_set_id
        )
        VALUES ($1, $2, 1, 'approved', 'mig028-set')
        """,
        blueprint_id,
        mission_id,
    )
    await connection.execute(
        """
        INSERT INTO scraping_executions (
            id, organization_id, mission_id, blueprint_id, team_plan_id,
            execution_type, mode, status, country_code, country_name
        )
        VALUES (
            $1, $2, $3, $4, NULL,
            'mission_campaign', 'mock', 'completed', 'LB', 'Lebanon'
        )
        """,
        execution_id,
        org_id,
        mission_id,
        blueprint_id,
    )
    return execution_id


async def _insert_pre_028_query(
    connection: asyncpg.Connection,
    *,
    org_id: str,
    row_id: str,
    query_text: str,
    region_name: str,
    metadata_json: str,
    provider: str = "serper",
) -> None:
    await connection.execute(
        """
        INSERT INTO scraping_source_discovery_queries (
            id, organization_id, execution_id, country_code, country_name,
            region_name, language_code, language_name, source_category, query_text,
            provider, status, requested_at, result_count, metadata_json,
            created_at, updated_at
        ) VALUES (
            $1, $2, NULL, 'LB', 'Lebanon',
            $3, 'en', 'English', 'regulatory', $4,
            $5, 'succeeded', TIMESTAMPTZ '2026-01-01 00:00:00+00', 1, $6::jsonb,
            TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
        )
        """,
        row_id,
        org_id,
        region_name,
        query_text,
        provider,
        metadata_json,
    )


async def _column_info(connection: asyncpg.Connection, column: str) -> asyncpg.Record:
    row = await connection.fetchrow(
        """
        SELECT data_type, character_maximum_length, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'scraping_source_discovery_queries'
          AND column_name = $1
        """,
        column,
    )
    assert row is not None
    return row


@pytest.mark.asyncio
async def test_027_to_028_adds_columns_text_backfill_and_constraints(
    postgres_migration_database: PostgresMigrationDatabase,
) -> None:
    db = postgres_migration_database
    await db.alembic("upgrade", "027")
    connection = await db.connect()
    try:
        org_id = await _seed_org(connection)
        q1 = str(uuid.uuid4())
        q2 = str(uuid.uuid4())
        await _insert_pre_028_query(
            connection,
            org_id=org_id,
            row_id=q1,
            query_text="legacy query one",
            region_name="Beirut",
            metadata_json='{"purpose": "from-metadata"}',
        )
        await _insert_pre_028_query(
            connection,
            org_id=org_id,
            row_id=q2,
            query_text="legacy query two",
            region_name="Beirut",
            metadata_json="{}",
        )
        before = await _column_info(connection, "query_text")
        assert before["data_type"] == "character varying"
        assert before["character_maximum_length"] == 512
    finally:
        await connection.close()

    await db.alembic("upgrade", "028")
    connection = await db.connect()
    try:
        query_text = await _column_info(connection, "query_text")
        assert query_text["data_type"] == "text"
        assert query_text["character_maximum_length"] is None

        for col, nullable in (
            ("provider", "YES"),
            ("requested_at", "YES"),
            ("region_name", "YES"),
            ("purpose", "NO"),
            ("priority", "NO"),
            ("discovery_round", "NO"),
            ("generation_ordinal", "NO"),
            ("scope_level", "NO"),
            ("query_job_fingerprint", "YES"),
            ("plan_hash_snapshot", "YES"),
            ("important_city", "YES"),
        ):
            info = await _column_info(connection, col)
            assert info["is_nullable"] == nullable, col

        row1 = await connection.fetchrow(
            "SELECT purpose, priority, discovery_round, generation_ordinal, scope_level, "
            "plan_hash_snapshot, query_job_fingerprint, provider, requested_at "
            "FROM scraping_source_discovery_queries WHERE id = $1",
            q1,
        )
        assert row1["purpose"] == "from-metadata"
        assert row1["priority"] == 500
        assert row1["discovery_round"] == 1
        assert row1["generation_ordinal"] == 0
        assert row1["scope_level"] == "region"
        assert row1["plan_hash_snapshot"] is None
        assert row1["query_job_fingerprint"] is None
        assert row1["provider"] == "serper"
        assert row1["requested_at"] is not None

        row2 = await connection.fetchrow(
            "SELECT purpose FROM scraping_source_discovery_queries WHERE id = $1",
            q2,
        )
        assert row2["purpose"] == "legacy_source_discovery"

        # Multiple NULL fingerprints allowed.
        for _ in range(2):
            await connection.execute(
                """
                INSERT INTO scraping_source_discovery_queries (
                    id, organization_id, country_code, country_name, region_name,
                    language_code, language_name, source_category, query_text,
                    provider, status, requested_at, result_count, metadata_json,
                    purpose, priority, discovery_round, generation_ordinal, scope_level,
                    query_job_fingerprint, plan_hash_snapshot, important_city,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, 'LB', 'Lebanon', 'Beirut',
                    'en', 'English', 'regulatory', 'null-fp',
                    'serper', 'succeeded', TIMESTAMPTZ '2026-01-01 00:00:00+00', 0, '{}'::jsonb,
                    'legacy_source_discovery', 500, 1, 0, 'region',
                    NULL, NULL, NULL,
                    TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
                )
                """,
                str(uuid.uuid4()),
                org_id,
            )

        execution_id = await _seed_execution(connection, org_id)

        # Provenance: historical NULL fingerprint + NULL plan hash remains valid (above).
        # Non-null fingerprint with NULL execution_id is rejected.
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO scraping_source_discovery_queries (
                    id, organization_id, execution_id, country_code, country_name, region_name,
                    language_code, language_name, source_category, query_text,
                    status, result_count, metadata_json,
                    purpose, priority, discovery_round, generation_ordinal, scope_level,
                    query_job_fingerprint, plan_hash_snapshot, important_city,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, NULL, 'LB', 'Lebanon', NULL,
                    'en', 'English', 'regulatory', 'fp no exec',
                    'pending', 0, '{}'::jsonb,
                    'seed_source_discovery', 100, 1, 0, 'countrywide',
                    $3, 'planhash', NULL,
                    TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
                )
                """,
                str(uuid.uuid4()),
                org_id,
                "f" * 64,
            )
        await connection.execute("ROLLBACK")

        # Non-null fingerprint with NULL plan hash is rejected.
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO scraping_source_discovery_queries (
                    id, organization_id, execution_id, country_code, country_name, region_name,
                    language_code, language_name, source_category, query_text,
                    status, result_count, metadata_json,
                    purpose, priority, discovery_round, generation_ordinal, scope_level,
                    query_job_fingerprint, plan_hash_snapshot, important_city,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, $3, 'LB', 'Lebanon', NULL,
                    'en', 'English', 'regulatory', 'fp no hash',
                    'pending', 0, '{}'::jsonb,
                    'seed_source_discovery', 100, 1, 0, 'countrywide',
                    $4, NULL, NULL,
                    TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
                )
                """,
                str(uuid.uuid4()),
                org_id,
                execution_id,
                "g" * 64,
            )
        await connection.execute("ROLLBACK")

        # Valid plan-backed row succeeds; duplicate fingerprint for same org/execution rejected.
        fp = "a" * 64
        await connection.execute(
            """
            INSERT INTO scraping_source_discovery_queries (
                id, organization_id, execution_id, country_code, country_name, region_name,
                language_code, language_name, source_category, query_text,
                status, result_count, metadata_json,
                purpose, priority, discovery_round, generation_ordinal, scope_level,
                query_job_fingerprint, plan_hash_snapshot, important_city,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, 'LB', 'Lebanon', NULL,
                'en', 'English', 'regulatory', 'fp-one',
                'pending', 0, '{}'::jsonb,
                'seed_source_discovery', 100, 1, 0, 'countrywide',
                $4, 'planhash', NULL,
                TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
            )
            """,
            str(uuid.uuid4()),
            org_id,
            execution_id,
            fp,
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                """
                INSERT INTO scraping_source_discovery_queries (
                    id, organization_id, execution_id, country_code, country_name, region_name,
                    language_code, language_name, source_category, query_text,
                    status, result_count, metadata_json,
                    purpose, priority, discovery_round, generation_ordinal, scope_level,
                    query_job_fingerprint, plan_hash_snapshot, important_city,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, $3, 'LB', 'Lebanon', NULL,
                    'en', 'English', 'regulatory', 'fp-two',
                    'pending', 0, '{}'::jsonb,
                    'seed_source_discovery', 100, 1, 1, 'countrywide',
                    $4, 'planhash', NULL,
                    TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
                )
                """,
                str(uuid.uuid4()),
                org_id,
                execution_id,
                fp,
            )
        await connection.execute("ROLLBACK")

        # Scope constraint: valid shapes.
        await connection.execute(
            """
            INSERT INTO scraping_source_discovery_queries (
                id, organization_id, execution_id, country_code, country_name, region_name,
                language_code, language_name, source_category, query_text,
                status, result_count, metadata_json,
                purpose, priority, discovery_round, generation_ordinal, scope_level,
                query_job_fingerprint, plan_hash_snapshot, important_city,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, 'LB', 'Lebanon', NULL,
                'en', 'English', 'regulatory', 'countrywide ok',
                'pending', 0, '{}'::jsonb,
                'seed_source_discovery', 100, 1, 0, 'countrywide',
                $4, 'planhash', NULL,
                TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
            )
            """,
            str(uuid.uuid4()),
            org_id,
            execution_id,
            "b" * 64,
        )
        await connection.execute(
            """
            INSERT INTO scraping_source_discovery_queries (
                id, organization_id, execution_id, country_code, country_name, region_name,
                language_code, language_name, source_category, query_text,
                status, result_count, metadata_json,
                purpose, priority, discovery_round, generation_ordinal, scope_level,
                query_job_fingerprint, plan_hash_snapshot, important_city,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, 'LB', 'Lebanon', 'Beirut',
                'en', 'English', 'regulatory', 'city ok',
                'pending', 0, '{}'::jsonb,
                'regulatory_source_discovery', 220, 1, 1, 'city',
                $4, 'planhash', 'Beirut',
                TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
            )
            """,
            str(uuid.uuid4()),
            org_id,
            execution_id,
            "c" * 64,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO scraping_source_discovery_queries (
                    id, organization_id, execution_id, country_code, country_name, region_name,
                    language_code, language_name, source_category, query_text,
                    status, result_count, metadata_json,
                    purpose, priority, discovery_round, generation_ordinal, scope_level,
                    query_job_fingerprint, plan_hash_snapshot, important_city,
                    created_at, updated_at
                ) VALUES (
                    $1, $2, $3, 'LB', 'Lebanon', 'Beirut',
                    'en', 'English', 'regulatory', 'bad countrywide',
                    'pending', 0, '{}'::jsonb,
                    'seed_source_discovery', 100, 1, 2, 'countrywide',
                    $4, 'planhash', NULL,
                    TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
                )
                """,
                str(uuid.uuid4()),
                org_id,
                execution_id,
                "d" * 64,
            )
        await connection.execute("ROLLBACK")

        indexes = {
            row["indexname"]
            for row in await connection.fetch(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'scraping_source_discovery_queries'"
            )
        }
        assert "ix_source_discovery_queries_fingerprint" not in indexes
        assert "ix_source_discovery_queries_round" in indexes
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_028_compatible_downgrade_and_fail_closed(
    postgres_migration_database: PostgresMigrationDatabase,
) -> None:
    db = postgres_migration_database
    await db.alembic("upgrade", "027")
    connection = await db.connect()
    try:
        org_id = await _seed_org(connection)
        q1 = str(uuid.uuid4())
        await _insert_pre_028_query(
            connection,
            org_id=org_id,
            row_id=q1,
            query_text="legacy compatible",
            region_name="Beirut",
            metadata_json="{}",
        )
    finally:
        await connection.close()

    await db.alembic("upgrade", "028")
    # Compatible historical-only rows can downgrade safely.
    await db.alembic("downgrade", "027")
    connection = await db.connect()
    try:
        cols = {
            row["column_name"]
            for row in await connection.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'scraping_source_discovery_queries'"
            )
        }
        assert "purpose" not in cols
        assert "query_job_fingerprint" not in cols
        query_text = await _column_info(connection, "query_text")
        assert query_text["data_type"] == "character varying"
        assert query_text["character_maximum_length"] == 512
        provider = await connection.fetchval(
            "SELECT provider FROM scraping_source_discovery_queries WHERE id = $1",
            q1,
        )
        assert provider == "serper"
    finally:
        await connection.close()

    # Re-upgrade and insert Step 3B pending row → fail-closed downgrade.
    await db.alembic("upgrade", "028")
    connection = await db.connect()
    try:
        execution_id = await _seed_execution(connection, org_id)
        await connection.execute(
            """
            INSERT INTO scraping_source_discovery_queries (
                id, organization_id, execution_id, country_code, country_name, region_name,
                language_code, language_name, source_category, query_text,
                status, result_count, metadata_json,
                purpose, priority, discovery_round, generation_ordinal, scope_level,
                query_job_fingerprint, plan_hash_snapshot, important_city,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, 'LB', 'Lebanon', NULL,
                'en', 'English', 'regulatory', $4,
                'pending', 0, '{}'::jsonb,
                'seed_source_discovery', 100, 1, 0, 'countrywide',
                $5, 'planhash', NULL,
                TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
            )
            """,
            str(uuid.uuid4()),
            org_id,
            execution_id,
            "x" * 600,
            "e" * 64,
        )
        before_count = await connection.fetchval(
            "SELECT COUNT(*) FROM scraping_source_discovery_queries"
        )
    finally:
        await connection.close()

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        await db.alembic("downgrade", "027")
    assert "Cannot downgrade migration 028" in (exc_info.value.stderr or "") + (
        exc_info.value.stdout or ""
    )

    connection = await db.connect()
    try:
        after_count = await connection.fetchval(
            "SELECT COUNT(*) FROM scraping_source_discovery_queries"
        )
        assert after_count == before_count
        assert await _column_info(connection, "purpose")
        query_text = await _column_info(connection, "query_text")
        assert query_text["data_type"] == "text"
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_028_revision_chain(postgres_migration_database: PostgresMigrationDatabase) -> None:
    db = postgres_migration_database
    await db.alembic("upgrade", "027")
    current = await db.alembic("current")
    assert "027" in current
    await db.alembic("upgrade", "028")
    current = await db.alembic("current")
    assert "028" in current
    heads = await db.alembic("heads")
    assert "028" in heads or "(head)" in heads
