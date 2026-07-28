"""PostgreSQL coverage for Phase 4 Slice 7 migration 030 (write-only; run via Docker).

Ephemeral ``migration_030_*`` DB: upgrade to 029, seed surviving rows, upgrade to
030, assert cursor columns + backfill, then downgrade to 029.

  docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api-test \\
    pytest -q tests/test_phase4_discovery_pagination_postgres_migration.py
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import asyncpg
import pytest

_FORBIDDEN_TARGET_DATABASES = frozenset(
    {
        "multiai",
        "multiai_scraping_test",
        "postgres",
        "template0",
        "template1",
    }
)
_EPHEMERAL_DB_RE = re.compile(r"^migration_030_[0-9a-f]{32}$")

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_ADMIN_URL"),
    reason="POSTGRES_TEST_ADMIN_URL required (docker-compose.test.yml api-test)",
)


@dataclass
class PostgresMigrationDatabase:
    admin: asyncpg.Connection
    database: str
    url: str

    async def alembic(self, *arguments: str) -> str:
        target = urlparse(self.url).path.lstrip("/")
        if target != self.database or not _EPHEMERAL_DB_RE.fullmatch(target):
            pytest.fail(
                "Refusing alembic: target is not an isolated migration_030_* test database."
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

    async def connect(self) -> asyncpg.Connection:
        target = urlparse(self.url).path.lstrip("/")
        if target != self.database or not _EPHEMERAL_DB_RE.fullmatch(target):
            pytest.fail(
                "Refusing connect: target is not an isolated migration_030_* test database."
            )
        return await asyncpg.connect(self.url)


@pytest.fixture
async def postgres_migration_database() -> AsyncGenerator[PostgresMigrationDatabase, None]:
    admin_url = os.environ.get("POSTGRES_TEST_ADMIN_URL")
    if not admin_url:
        pytest.fail("POSTGRES_TEST_ADMIN_URL is required for PostgreSQL migration coverage.")

    database = f"migration_030_{uuid.uuid4().hex}"
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
        pytest.fail("Refusing to proceed: migration URL does not target the ephemeral test DB.")

    migration_database = PostgresMigrationDatabase(
        admin=admin,
        database=database,
        url=url,
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


async def _seed_org(connection: asyncpg.Connection, *, slug_prefix: str = "mig030") -> str:
    org_id = str(uuid.uuid4())
    await connection.execute(
        """
        INSERT INTO organizations (id, name, slug)
        VALUES ($1, 'Migration 030 org', $2)
        """,
        org_id,
        f"{slug_prefix}-{uuid.uuid4().hex[:8]}",
    )
    return org_id


async def _seed_execution(connection: asyncpg.Connection, org_id: str) -> str:
    user_id = str(uuid.uuid4())
    mission_id = str(uuid.uuid4())
    blueprint_id = str(uuid.uuid4())
    execution_id = str(uuid.uuid4())
    await connection.execute(
        """
        INSERT INTO users (id, email, hashed_password, full_name)
        VALUES ($1, $2, 'x', 'Migration 030 Tester')
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
        VALUES ($1, $2, $3, 'mig030-set', 'Migration 030', 'Plan', 'draft', 'LB', 'Lebanon')
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
        VALUES ($1, $2, 1, 'approved', 'mig030-set')
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


async def _insert_query(
    connection: asyncpg.Connection,
    *,
    org_id: str,
    execution_id: str,
    status: str,
    fingerprint: str,
    completed_at: datetime | None = None,
) -> str:
    query_id = str(uuid.uuid4())
    await connection.execute(
        """
        INSERT INTO scraping_source_discovery_queries (
            id, organization_id, execution_id, country_code, country_name, region_name,
            language_code, language_name, source_category, query_text,
            status, result_count, metadata_json,
            purpose, priority, discovery_round, generation_ordinal, scope_level,
            query_job_fingerprint, plan_hash_snapshot, important_city,
            provider, requested_at, completed_at, created_at, updated_at
        ) VALUES (
            $1, $2, $3, 'LB', 'Lebanon', NULL,
            'en', 'English', 'regulatory', $4,
            $5::varchar, 0, '{}'::jsonb,
            'seed_source_discovery', 100, 1, 0, 'countrywide',
            $6, 'planhash', NULL,
            CASE WHEN $5::varchar = 'succeeded' THEN 'serper' ELSE NULL END,
            CASE WHEN $5::varchar = 'succeeded' THEN TIMESTAMPTZ '2026-01-01 00:00:00+00' ELSE NULL END,
            $7::timestamptz,
            TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
        )
        """,
        query_id,
        org_id,
        execution_id,
        f"query-{status}",
        status,
        fingerprint,
        completed_at,
    )
    return query_id


async def _insert_candidate(
    connection: asyncpg.Connection,
    *,
    org_id: str,
    execution_id: str,
    query_id: str,
) -> str:
    candidate_id = str(uuid.uuid4())
    await connection.execute(
        """
        INSERT INTO scraping_source_candidates (
            id, organization_id, execution_id, discovery_query_id, crawl_node_id,
            provider, rank, url, canonical_url, domain, title, snippet,
            country_code, country_name, region_name, language_code, language_name,
            source_category, initial_relevance_score, initial_trust_tier, status,
            discovered_at, metadata_json, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, NULL,
            'serper', 1, 'https://example.test/a', 'https://example.test/a',
            'example.test', 'Title', 'Snippet',
            'LB', 'Lebanon', NULL, 'en', 'English',
            'regulatory', 0.5, 'medium', 'discovered',
            TIMESTAMPTZ '2026-01-01 00:00:00+00', '{}'::jsonb,
            TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
        )
        """,
        candidate_id,
        org_id,
        execution_id,
        query_id,
    )
    return candidate_id


@pytest.mark.asyncio
async def test_029_to_030_pagination_columns_and_surviving_backfill(
    postgres_migration_database: PostgresMigrationDatabase,
) -> None:
    db = postgres_migration_database
    await db.alembic("upgrade", "029")

    connection = await db.connect()
    try:
        # Pre-030: pagination columns must not exist yet.
        pre = await connection.fetchval(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'scraping_source_discovery_queries'
              AND column_name = 'next_page_number'
            """
        )
        assert pre is None

        org_id = await _seed_org(connection)
        execution_id = await _seed_execution(connection, org_id)
        pending_id = await _insert_query(
            connection,
            org_id=org_id,
            execution_id=execution_id,
            status="pending",
            fingerprint="p" * 64,
        )
        succeeded_id = await _insert_query(
            connection,
            org_id=org_id,
            execution_id=execution_id,
            status="succeeded",
            fingerprint="s" * 64,
            completed_at=datetime(2026, 1, 2, 12, 0, tzinfo=UTC),
        )
        running_id = await _insert_query(
            connection,
            org_id=org_id,
            execution_id=execution_id,
            status="running",
            fingerprint="r" * 64,
        )
        candidate_id = await _insert_candidate(
            connection,
            org_id=org_id,
            execution_id=execution_id,
            query_id=succeeded_id,
        )
    finally:
        await connection.close()

    await db.alembic("upgrade", "030")
    current = await db.alembic("current")
    assert "030" in current

    # Repository head and target-database current answer different questions.
    heads = await db.alembic("heads")
    head_lines = [line for line in heads.splitlines() if line.strip()]
    assert any(line.strip().startswith("032") and "(head)" in line for line in head_lines)
    assert not any(line.strip().startswith("030") and "(head)" in line for line in head_lines)

    connection = await db.connect()
    try:
        cols = await connection.fetch(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'scraping_source_discovery_queries'
              AND column_name IN (
                'next_page_number', 'pages_completed', 'pagination_completed',
                'last_page_result_count', 'last_page_fingerprint',
                'pagination_completed_at'
              )
            ORDER BY column_name
            """
        )
        names = {r["column_name"] for r in cols}
        assert names == {
            "next_page_number",
            "pages_completed",
            "pagination_completed",
            "last_page_result_count",
            "last_page_fingerprint",
            "pagination_completed_at",
        }
        by_name = {r["column_name"]: r for r in cols}
        assert by_name["next_page_number"]["is_nullable"] == "NO"
        assert by_name["pages_completed"]["is_nullable"] == "NO"
        assert by_name["pagination_completed"]["is_nullable"] == "NO"

        cand = await connection.fetchval(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'scraping_source_candidates'
              AND column_name = 'provider_page_number'
            """
        )
        assert cand == 1

        pending = await connection.fetchrow(
            """
            SELECT next_page_number, pages_completed, pagination_completed,
                   last_page_result_count, last_page_fingerprint, pagination_completed_at
            FROM scraping_source_discovery_queries WHERE id = $1
            """,
            pending_id,
        )
        assert pending["next_page_number"] == 1
        assert pending["pages_completed"] == 0
        assert pending["pagination_completed"] is False
        assert pending["last_page_result_count"] is None
        assert pending["last_page_fingerprint"] is None
        assert pending["pagination_completed_at"] is None

        running = await connection.fetchrow(
            """
            SELECT next_page_number, pages_completed, pagination_completed,
                   pagination_completed_at
            FROM scraping_source_discovery_queries WHERE id = $1
            """,
            running_id,
        )
        assert running["next_page_number"] == 1
        assert running["pages_completed"] == 0
        assert running["pagination_completed"] is False
        assert running["pagination_completed_at"] is None

        succeeded = await connection.fetchrow(
            """
            SELECT next_page_number, pages_completed, pagination_completed,
                   pagination_completed_at
            FROM scraping_source_discovery_queries WHERE id = $1
            """,
            succeeded_id,
        )
        assert succeeded["pagination_completed"] is True
        assert succeeded["pagination_completed_at"] is not None
        assert succeeded["next_page_number"] == 1
        assert succeeded["pages_completed"] == 0

        # Surviving candidate accepts nullable provider_page_number.
        page_num = await connection.fetchval(
            """
            SELECT provider_page_number FROM scraping_source_candidates WHERE id = $1
            """,
            candidate_id,
        )
        assert page_num is None

        # Check constraints reject invalid page cursor values.
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                UPDATE scraping_source_discovery_queries
                SET next_page_number = 0 WHERE id = $1
                """,
                pending_id,
            )
        await connection.execute("ROLLBACK")

        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                UPDATE scraping_source_discovery_queries
                SET pages_completed = -1 WHERE id = $1
                """,
                pending_id,
            )
        await connection.execute("ROLLBACK")

        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                UPDATE scraping_source_candidates
                SET provider_page_number = 0 WHERE id = $1
                """,
                candidate_id,
            )
        await connection.execute("ROLLBACK")

        # Valid pagination update accepted.
        await connection.execute(
            """
            UPDATE scraping_source_discovery_queries
            SET next_page_number = 3, pages_completed = 2,
                last_page_result_count = 10,
                last_page_fingerprint = $2
            WHERE id = $1
            """,
            pending_id,
            "a" * 64,
        )
        await connection.execute(
            """
            UPDATE scraping_source_candidates
            SET provider_page_number = 2 WHERE id = $1
            """,
            candidate_id,
        )

        # Phase 4 pagination data and columns survive the linear 030 -> current-head upgrade.
        await db.alembic("upgrade", "head")
        current = await db.alembic("current")
        assert "032" in current
        survived = await connection.fetchrow(
            """
            SELECT next_page_number, pages_completed, last_page_result_count,
                   last_page_fingerprint
            FROM scraping_source_discovery_queries WHERE id = $1
            """,
            pending_id,
        )
        assert dict(survived) == {
            "next_page_number": 3,
            "pages_completed": 2,
            "last_page_result_count": 10,
            "last_page_fingerprint": "a" * 64,
        }
        assert await connection.fetchval(
            "SELECT provider_page_number FROM scraping_source_candidates WHERE id = $1",
            candidate_id,
        ) == 2

        await db.alembic("downgrade", "029")

        missing = await connection.fetchval(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'scraping_source_discovery_queries'
              AND column_name = 'next_page_number'
            """
        )
        assert missing is None
        missing_cand = await connection.fetchval(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'scraping_source_candidates'
              AND column_name = 'provider_page_number'
            """
        )
        assert missing_cand is None

        # Surviving rows still present after downgrade.
        still = await connection.fetchval(
            "SELECT count(*) FROM scraping_source_discovery_queries"
        )
        assert still == 3
        still_cand = await connection.fetchval(
            "SELECT count(*) FROM scraping_source_candidates WHERE id = $1",
            candidate_id,
        )
        assert still_cand == 1
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_030_ephemeral_name_guards(
    postgres_migration_database: PostgresMigrationDatabase,
) -> None:
    db = postgres_migration_database
    assert _EPHEMERAL_DB_RE.fullmatch(db.database)
    assert db.database not in _FORBIDDEN_TARGET_DATABASES
    target = urlparse(db.url).path.lstrip("/")
    assert target == db.database
    assert "multiai" not in target or target.startswith("migration_030_")
