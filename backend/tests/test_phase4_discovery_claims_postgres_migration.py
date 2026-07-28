"""PostgreSQL coverage for Phase 4 Slice 1 migration 029.

Same ephemeral-DB harness as ``test_deterministic_query_postgres_migration`` /
``test_widen_blueprint_status_migration`` / ``test_repair_blueprint_schema_drift_migration``:
``POSTGRES_TEST_ADMIN_URL`` is used only to CREATE/DROP a unique ``migration_029_*``
database; Alembic never targets the admin/development database.

Normal run (injects the admin URL via ``docker-compose.test.yml``):

  docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm api-test \\
    pytest -q tests/test_phase4_discovery_claims_postgres_migration.py
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import subprocess
import uuid
from pathlib import Path
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from urllib.parse import urlparse

import asyncpg
import pytest

# Fail closed: never treat these as migration targets.
_FORBIDDEN_TARGET_DATABASES = frozenset(
    {
        "multiai",
        "multiai_scraping_test",
        "postgres",
        "template0",
        "template1",
    }
)
_EPHEMERAL_DB_RE = re.compile(r"^migration_029_[0-9a-f]{32}$")


@dataclass
class PostgresMigrationDatabase:
    admin: asyncpg.Connection
    database: str
    url: str

    async def alembic(self, *arguments: str) -> str:
        target = urlparse(self.url).path.lstrip("/")
        if target != self.database or not _EPHEMERAL_DB_RE.fullmatch(target):
            pytest.fail(
                "Refusing alembic: target is not an isolated migration_029_* test database."
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
                "Refusing connect: target is not an isolated migration_029_* test database."
            )
        return await asyncpg.connect(self.url)


@pytest.fixture
async def postgres_migration_database() -> AsyncGenerator[PostgresMigrationDatabase, None]:
    """Create a unique disposable Postgres DB; drop it after the test (028-style)."""
    admin_url = os.environ.get("POSTGRES_TEST_ADMIN_URL")
    if not admin_url:
        pytest.fail("POSTGRES_TEST_ADMIN_URL is required for PostgreSQL migration coverage.")

    database = f"migration_029_{uuid.uuid4().hex}"
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


async def _seed_org(connection: asyncpg.Connection, *, slug_prefix: str = "mig029") -> str:
    org_id = str(uuid.uuid4())
    await connection.execute(
        """
        INSERT INTO organizations (id, name, slug)
        VALUES ($1, 'Migration 029 org', $2)
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
        VALUES ($1, $2, 'x', 'Migration 029 Tester')
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
        VALUES ($1, $2, $3, 'mig029-set', 'Migration 029', 'Plan', 'draft', 'LB', 'Lebanon')
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
        VALUES ($1, $2, 1, 'approved', 'mig029-set')
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


async def _insert_pending_query(
    connection: asyncpg.Connection,
    *,
    org_id: str,
    execution_id: str,
    row_id: str | None = None,
    fingerprint: str | None = None,
) -> str:
    query_id = row_id or str(uuid.uuid4())
    await connection.execute(
        """
        INSERT INTO scraping_source_discovery_queries (
            id, organization_id, execution_id, country_code, country_name, region_name,
            language_code, language_name, source_category, query_text,
            status, result_count, metadata_json,
            purpose, priority, discovery_round, generation_ordinal, scope_level,
            query_job_fingerprint, plan_hash_snapshot, important_city,
            provider, requested_at, created_at, updated_at
        ) VALUES (
            $1, $2, $3, 'LB', 'Lebanon', NULL,
            'en', 'English', 'regulatory', 'pending job',
            'pending', 0, '{}'::jsonb,
            'seed_source_discovery', 100, 1, 0, 'countrywide',
            $4, 'planhash', NULL,
            NULL, NULL,
            TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
        )
        """,
        query_id,
        org_id,
        execution_id,
        fingerprint or ("f" * 64),
    )
    return query_id


async def _insert_candidate(
    connection: asyncpg.Connection,
    *,
    org_id: str,
    execution_id: str,
    query_id: str,
    row_id: str | None = None,
    region_name: str | None = "Beirut",
    crawl_node_id: str | None = None,
    rank: int = 1,
) -> str:
    candidate_id = row_id or str(uuid.uuid4())
    await connection.execute(
        """
        INSERT INTO scraping_source_candidates (
            id, organization_id, execution_id, discovery_query_id, crawl_node_id,
            provider, rank, url, canonical_url, domain, title, snippet,
            country_code, country_name, region_name, language_code, language_name,
            source_category, initial_relevance_score, initial_trust_tier, status,
            discovered_at, metadata_json, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5,
            'serper', $6, 'https://example.test/a', 'https://example.test/a',
            'example.test', 'Title', 'Snippet',
            'LB', 'Lebanon', $7, 'en', 'English',
            'regulatory', 0.5, 'medium', 'discovered',
            TIMESTAMPTZ '2026-01-01 00:00:00+00', '{}'::jsonb,
            TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
        )
        """,
        candidate_id,
        org_id,
        execution_id,
        query_id,
        crawl_node_id,
        rank,
        region_name,
    )
    return candidate_id


async def _insert_node(
    connection: asyncpg.Connection,
    *,
    org_id: str,
    execution_id: str,
    url_hash: str,
    row_id: str | None = None,
    canonical_url: str = "https://example.test/a",
) -> str:
    node_id = row_id or str(uuid.uuid4())
    await connection.execute(
        """
        INSERT INTO scraping_crawl_nodes (
            id, organization_id, execution_id, canonical_url, canonical_url_hash,
            hostname, domain, source_classification, first_seen_at, created_at, updated_at
        ) VALUES (
            $1, $2, $3, $4, $5,
            'example.test', 'example.test', 'unclassified',
            TIMESTAMPTZ '2026-01-01 00:00:00+00',
            TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
        )
        """,
        node_id,
        org_id,
        execution_id,
        canonical_url,
        url_hash,
    )
    return node_id


@pytest.mark.asyncio
async def test_028_to_029_lifecycle_crawl_graph_constraints(
    postgres_migration_database: PostgresMigrationDatabase,
) -> None:
    db = postgres_migration_database
    await db.alembic("upgrade", "028")
    connection = await db.connect()
    try:
        org_id = await _seed_org(connection)
        execution_id = await _seed_execution(connection, org_id)
        query_id = await _insert_pending_query(
            connection, org_id=org_id, execution_id=execution_id
        )
        # Pre-029 candidates table has NOT NULL region_name and no crawl_node_id.
        await connection.execute(
            """
            INSERT INTO scraping_source_candidates (
                id, organization_id, execution_id, discovery_query_id,
                provider, rank, url, canonical_url, domain, title, snippet,
                country_code, country_name, region_name, language_code, language_name,
                source_category, initial_relevance_score, initial_trust_tier, status,
                discovered_at, metadata_json, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4,
                'serper', 1, 'https://example.test/a', 'https://example.test/a',
                'example.test', 'Title', 'Snippet',
                'LB', 'Lebanon', 'Beirut', 'en', 'English',
                'regulatory', 0.5, 'medium', 'discovered',
                TIMESTAMPTZ '2026-01-01 00:00:00+00', '{}'::jsonb,
                TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
            )
            """,
            str(uuid.uuid4()),
            org_id,
            execution_id,
            query_id,
        )
    finally:
        await connection.close()

    await db.alembic("upgrade", "029")
    connection = await db.connect()
    try:
        row = await connection.fetchrow(
            """
            SELECT attempt_count, status, provider, requested_at, query_job_fingerprint,
                   claim_token, last_error_code
            FROM scraping_source_discovery_queries WHERE id = $1
            """,
            query_id,
        )
        assert row["attempt_count"] == 0
        assert row["status"] == "pending"
        assert row["provider"] is None
        assert row["requested_at"] is None
        assert row["query_job_fingerprint"] == "f" * 64
        assert row["claim_token"] is None
        assert row["last_error_code"] is None

        # claim_token remains String(36) (project UUID convention), not native uuid.
        claim_type = await connection.fetchrow(
            """
            SELECT data_type, character_maximum_length
            FROM information_schema.columns
            WHERE table_name = 'scraping_source_discovery_queries'
              AND column_name = 'claim_token'
            """
        )
        assert claim_type["data_type"] == "character varying"
        assert claim_type["character_maximum_length"] == 36

        region_nullable = await connection.fetchval(
            """
            SELECT is_nullable FROM information_schema.columns
            WHERE table_name = 'scraping_source_candidates' AND column_name = 'region_name'
            """
        )
        assert region_nullable == "YES"

        indexes = {
            r["indexname"]
            for r in await connection.fetch(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'scraping_source_discovery_queries'"
            )
        }
        assert "ix_source_discovery_queries_pending_claim" in indexes
        assert "ix_source_discovery_queries_running_lease" in indexes

        pending_def = await connection.fetchval(
            """
            SELECT indexdef FROM pg_indexes
            WHERE indexname = 'ix_source_discovery_queries_pending_claim'
            """
        )
        assert pending_def is not None
        assert "organization_id" in pending_def
        assert "execution_id" in pending_def
        assert "priority" in pending_def
        assert "generation_ordinal" in pending_def
        assert "next_attempt_at" in pending_def
        assert "pending" in pending_def

        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                "UPDATE scraping_source_discovery_queries SET attempt_count = -1 WHERE id = $1",
                query_id,
            )
        await connection.execute("ROLLBACK")

        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                UPDATE scraping_source_discovery_queries
                SET claimed_at = TIMESTAMPTZ '2026-01-02 00:00:00+00',
                    lease_expires_at = TIMESTAMPTZ '2026-01-01 00:00:00+00'
                WHERE id = $1
                """,
                query_id,
            )
        await connection.execute("ROLLBACK")

        # Valid lease ordering accepted.
        await connection.execute(
            """
            UPDATE scraping_source_discovery_queries
            SET claimed_at = TIMESTAMPTZ '2026-01-01 00:00:00+00',
                lease_expires_at = TIMESTAMPTZ '2026-01-01 00:05:00+00',
                claim_token = $2,
                status = 'running'
            WHERE id = $1
            """,
            query_id,
            str(uuid.uuid4()),
        )

        await connection.execute(
            "UPDATE scraping_source_candidates SET region_name = NULL"
        )

        url_hash = "a" * 64
        node_id = await _insert_node(
            connection, org_id=org_id, execution_id=execution_id, url_hash=url_hash
        )
        with pytest.raises(asyncpg.UniqueViolationError):
            await _insert_node(
                connection, org_id=org_id, execution_id=execution_id, url_hash=url_hash
            )
        await connection.execute("ROLLBACK")

        org_b = await _seed_org(connection, slug_prefix="mig029b")
        exec_same_org_other = await _seed_execution(connection, org_id)
        exec_org_b = await _seed_execution(connection, org_b)
        # Same hash across org/execution is allowed.
        await _insert_node(
            connection,
            org_id=org_id,
            execution_id=exec_same_org_other,
            url_hash=url_hash,
        )
        await _insert_node(
            connection, org_id=org_b, execution_id=exec_org_b, url_hash=url_hash
        )

        # Multiple candidates → one node (distinct discovery queries).
        existing_cand = await connection.fetchval(
            "SELECT id FROM scraping_source_candidates WHERE discovery_query_id = $1 LIMIT 1",
            query_id,
        )
        await connection.execute(
            "UPDATE scraping_source_candidates SET crawl_node_id = $1, region_name = NULL "
            "WHERE id = $2",
            node_id,
            existing_cand,
        )
        query_id_2 = await _insert_pending_query(
            connection,
            org_id=org_id,
            execution_id=execution_id,
            fingerprint="e" * 64,
        )
        cand_b = await _insert_candidate(
            connection,
            org_id=org_id,
            execution_id=execution_id,
            query_id=query_id_2,
            region_name=None,
            crawl_node_id=node_id,
            rank=1,
        )
        linked = await connection.fetchval(
            "SELECT COUNT(*) FROM scraping_source_candidates WHERE crawl_node_id = $1",
            node_id,
        )
        assert linked == 2
        assert existing_cand != cand_b
        cand_a = existing_cand

        # Cross-org candidate→node link rejected.
        foreign_node = await connection.fetchval(
            "SELECT id FROM scraping_crawl_nodes WHERE organization_id = $1 LIMIT 1",
            org_b,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                "UPDATE scraping_source_candidates SET crawl_node_id = $1 WHERE id = $2",
                foreign_node,
                existing_cand,
            )
        await connection.execute("ROLLBACK")

        # Cross-execution candidate→node link rejected.
        other_exec_node = await connection.fetchval(
            "SELECT id FROM scraping_crawl_nodes "
            "WHERE organization_id = $1 AND execution_id = $2 LIMIT 1",
            org_id,
            exec_same_org_other,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                "UPDATE scraping_source_candidates SET crawl_node_id = $1 WHERE id = $2",
                other_exec_node,
                existing_cand,
            )
        await connection.execute("ROLLBACK")

        # crawl_node_id with execution_id NULL rejected by CHECK.
        hist_cand = str(uuid.uuid4())
        await connection.execute(
            """
            INSERT INTO scraping_source_candidates (
                id, organization_id, execution_id, discovery_query_id,
                provider, rank, url, canonical_url, domain, title, snippet,
                country_code, country_name, region_name, language_code, language_name,
                source_category, initial_relevance_score, initial_trust_tier, status,
                discovered_at, metadata_json, created_at, updated_at
            ) VALUES (
                $1, $2, NULL, $3,
                'serper', 9, 'https://example.test/hist', 'https://example.test/hist',
                'example.test', 'Hist', 'Snippet',
                'LB', 'Lebanon', NULL, 'en', 'English',
                'regulatory', 0.1, 'medium', 'discovered',
                TIMESTAMPTZ '2026-01-01 00:00:00+00', '{}'::jsonb,
                TIMESTAMPTZ '2026-01-01 00:00:00+00', TIMESTAMPTZ '2026-01-01 00:00:00+00'
            )
            """,
            hist_cand,
            org_id,
            query_id,
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                "UPDATE scraping_source_candidates SET crawl_node_id = $1 WHERE id = $2",
                node_id,
                hist_cand,
            )
        await connection.execute("ROLLBACK")
        # Historical NULL crawl_node_id + NULL execution_id remains valid.
        assert (
            await connection.fetchval(
                "SELECT crawl_node_id FROM scraping_source_candidates WHERE id = $1",
                hist_cand,
            )
            is None
        )

        node_b = await _insert_node(
            connection,
            org_id=org_id,
            execution_id=execution_id,
            url_hash="b" * 64,
            canonical_url="https://example.test/b",
        )
        edge_id = str(uuid.uuid4())
        await connection.execute(
            """
            INSERT INTO scraping_crawl_edges (
                id, organization_id, execution_id, from_node_id, to_node_id,
                relationship_type, discovery_query_id, source_candidate_id, created_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                'directory_to_profile', $6, $7,
                TIMESTAMPTZ '2026-01-01 00:00:00+00'
            )
            """,
            edge_id,
            org_id,
            execution_id,
            node_b,
            node_id,
            query_id,
            cand_a,
        )

        # Cross-org discovery_query provenance rejected.
        query_org_b = await _insert_pending_query(
            connection,
            org_id=org_b,
            execution_id=exec_org_b,
            fingerprint="c" * 64,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO scraping_crawl_edges (
                    id, organization_id, execution_id, from_node_id, to_node_id,
                    relationship_type, discovery_query_id, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, 'discovered_link', $6,
                    TIMESTAMPTZ '2026-01-01 00:00:00+00'
                )
                """,
                str(uuid.uuid4()),
                org_id,
                execution_id,
                node_b,
                node_id,
                query_org_b,
            )
        await connection.execute("ROLLBACK")

        # Cross-exec discovery_query provenance rejected.
        query_other_exec = await _insert_pending_query(
            connection,
            org_id=org_id,
            execution_id=exec_same_org_other,
            fingerprint="d" * 64,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO scraping_crawl_edges (
                    id, organization_id, execution_id, from_node_id, to_node_id,
                    relationship_type, discovery_query_id, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, 'related_source', $6,
                    TIMESTAMPTZ '2026-01-01 00:00:00+00'
                )
                """,
                str(uuid.uuid4()),
                org_id,
                execution_id,
                node_b,
                node_id,
                query_other_exec,
            )
        await connection.execute("ROLLBACK")

        # Cross-org candidate provenance rejected.
        cand_org_b_query = await _insert_pending_query(
            connection,
            org_id=org_b,
            execution_id=exec_org_b,
            fingerprint="1" * 64,
        )
        cand_org_b = await _insert_candidate(
            connection,
            org_id=org_b,
            execution_id=exec_org_b,
            query_id=cand_org_b_query,
            region_name=None,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO scraping_crawl_edges (
                    id, organization_id, execution_id, from_node_id, to_node_id,
                    relationship_type, source_candidate_id, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, 'discovered_link', $6,
                    TIMESTAMPTZ '2026-01-01 00:00:00+00'
                )
                """,
                str(uuid.uuid4()),
                org_id,
                execution_id,
                node_b,
                node_id,
                cand_org_b,
            )
        await connection.execute("ROLLBACK")

        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                """
                INSERT INTO scraping_crawl_edges (
                    id, organization_id, execution_id, from_node_id, to_node_id,
                    relationship_type, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, 'directory_to_profile',
                    TIMESTAMPTZ '2026-01-01 00:00:00+00'
                )
                """,
                str(uuid.uuid4()),
                org_id,
                execution_id,
                node_b,
                node_id,
            )
        await connection.execute("ROLLBACK")

        with pytest.raises(asyncpg.CheckViolationError):
            await connection.execute(
                """
                INSERT INTO scraping_crawl_edges (
                    id, organization_id, execution_id, from_node_id, to_node_id,
                    relationship_type, created_at
                ) VALUES (
                    $1, $2, $3, $4, $4, 'related_source',
                    TIMESTAMPTZ '2026-01-01 00:00:00+00'
                )
                """,
                str(uuid.uuid4()),
                org_id,
                execution_id,
                node_id,
            )
        await connection.execute("ROLLBACK")

        # Cross-execution / cross-org node endpoints rejected by composite FK.
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO scraping_crawl_edges (
                    id, organization_id, execution_id, from_node_id, to_node_id,
                    relationship_type, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, 'discovered_link',
                    TIMESTAMPTZ '2026-01-01 00:00:00+00'
                )
                """,
                str(uuid.uuid4()),
                org_id,
                execution_id,
                node_b,
                other_exec_node,
            )
        await connection.execute("ROLLBACK")

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                """
                INSERT INTO scraping_crawl_edges (
                    id, organization_id, execution_id, from_node_id, to_node_id,
                    relationship_type, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, 'related_source',
                    TIMESTAMPTZ '2026-01-01 00:00:00+00'
                )
                """,
                str(uuid.uuid4()),
                org_id,
                execution_id,
                node_b,
                foreign_node,
            )
        await connection.execute("ROLLBACK")

        # RESTRICT: node delete blocked while candidates still reference it.
        await connection.execute(
            "DELETE FROM scraping_crawl_edges WHERE from_node_id = $1 OR to_node_id = $1",
            node_id,
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await connection.execute(
                "DELETE FROM scraping_crawl_nodes WHERE id = $1", node_id
            )
        await connection.execute("ROLLBACK")

        # Clear only crawl_node_id (ownership columns untouched), then node delete OK.
        await connection.execute(
            "UPDATE scraping_source_candidates SET crawl_node_id = NULL "
            "WHERE crawl_node_id = $1",
            node_id,
        )
        org_before, exec_before = await connection.fetchrow(
            "SELECT organization_id, execution_id FROM scraping_source_candidates WHERE id = $1",
            cand_a,
        )
        await connection.execute("DELETE FROM scraping_crawl_nodes WHERE id = $1", node_id)
        org_after, exec_after = await connection.fetchrow(
            "SELECT organization_id, execution_id FROM scraping_source_candidates WHERE id = $1",
            cand_a,
        )
        assert org_after == org_before == org_id
        assert exec_after == exec_before == execution_id
        assert (
            await connection.fetchval(
                "SELECT crawl_node_id FROM scraping_source_candidates WHERE id = $1",
                cand_a,
            )
            is None
        )
        assert (
            await connection.fetchval(
                "SELECT COUNT(*) FROM scraping_source_candidates WHERE id = $1",
                cand_a,
            )
            == 1
        )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_029_compatible_downgrade_and_fail_closed(
    postgres_migration_database: PostgresMigrationDatabase,
) -> None:
    db = postgres_migration_database
    await db.alembic("upgrade", "029")
    connection = await db.connect()
    try:
        org_id = await _seed_org(connection)
        execution_id = await _seed_execution(connection, org_id)
        query_id = await _insert_pending_query(
            connection, org_id=org_id, execution_id=execution_id
        )
        await _insert_candidate(
            connection,
            org_id=org_id,
            execution_id=execution_id,
            query_id=query_id,
            region_name="Beirut",
        )
    finally:
        await connection.close()

    await db.alembic("downgrade", "028")
    connection = await db.connect()
    try:
        cols = {
            r["column_name"]
            for r in await connection.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'scraping_source_discovery_queries'"
            )
        }
        assert "claim_token" not in cols
        assert "attempt_count" not in cols
        tables = {
            r["tablename"]
            for r in await connection.fetch(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        assert "scraping_crawl_nodes" not in tables
        assert "scraping_crawl_edges" not in tables
        status = await connection.fetchval(
            "SELECT status FROM scraping_source_discovery_queries WHERE id = $1",
            query_id,
        )
        assert status == "pending"
    finally:
        await connection.close()

    await db.alembic("upgrade", "029")
    connection = await db.connect()
    try:
        await connection.execute(
            "UPDATE scraping_source_candidates SET region_name = NULL"
        )
        before_count = await connection.fetchval(
            "SELECT COUNT(*) FROM scraping_source_candidates"
        )
    finally:
        await connection.close()

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        await db.alembic("downgrade", "028")
    combined = (exc_info.value.stderr or "") + (exc_info.value.stdout or "")
    assert "Cannot downgrade migration 029" in combined

    connection = await db.connect()
    try:
        after_count = await connection.fetchval(
            "SELECT COUNT(*) FROM scraping_source_candidates"
        )
        assert after_count == before_count
        assert await connection.fetchval(
            "SELECT to_regclass('public.scraping_crawl_nodes')"
        )
    finally:
        await connection.close()


def _load_migration_module(filename: str):
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(f"migration_{filename}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_029_revision_chain(postgres_migration_database: PostgresMigrationDatabase) -> None:
    db = postgres_migration_database
    await db.alembic("upgrade", "028")
    current = await db.alembic("current")
    assert "028" in current
    await db.alembic("upgrade", "029")
    current = await db.alembic("current")
    assert "029" in current

    # Repository heads reflect the latest linear revision (032), not only the DB current.
    heads = await db.alembic("heads")
    head_lines = [line.strip() for line in heads.splitlines() if line.strip()]
    head_revisions = [
        line.split()[0]
        for line in head_lines
        if "(head)" in line and not line.startswith("INFO")
    ]
    assert head_revisions == ["032"]
    assert not any(line.startswith("029") and "(head)" in line for line in head_lines)

    mig_029 = _load_migration_module("029_phase4_discovery_claims_and_crawl_graph.py")
    mig_030 = _load_migration_module("030_phase4_discovery_pagination.py")
    mig_031 = _load_migration_module("031_phase5_directory_retrieval_foundation.py")
    mig_032 = _load_migration_module("032_phase5b_directory_graph_relationships.py")
    assert mig_029.revision == "029"
    assert mig_029.down_revision == "028"
    assert mig_030.revision == "030"
    assert mig_030.down_revision == "029"
    assert mig_031.revision == "031"
    assert mig_031.down_revision == "030"
    assert mig_032.revision == "032"
    assert mig_032.down_revision == "031"
