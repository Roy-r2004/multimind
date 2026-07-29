"""PostgreSQL matrix for the idempotent 024 blueprint-schema repair."""

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import asyncpg
import pytest

_MISSION_COLUMNS = {"country_iso3", "continent"}
_BLUEPRINT_COLUMNS = {
    "country_name_snapshot",
    "country_iso3_snapshot",
    "continent_snapshot",
    "provider",
    "provider_model_id",
    "prompt_template_version",
    "rendered_prompt_snapshot",
    "human_readable_blueprint",
    "structured_blueprint",
    "citations",
    "revision_request",
    "generation_error",
    "queued_at",
    "started_at",
    "completed_at",
    "discarded_at",
    "failed_at",
    "provider_operation_id",
    "provider_execution_metadata",
}
_INDEXES = {
    "ix_scraping_missions_country_iso3",
    "ix_scraping_blueprints_provider",
    "ix_scraping_blueprints_prompt_template_version",
    "ix_scraping_blueprints_provider_operation",
}


@dataclass(frozen=True)
class SchemaState:
    mission_columns: frozenset[str]
    blueprint_columns: frozenset[str]
    indexes: frozenset[str]
    constraints: frozenset[tuple[str, str, str]]


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

    database = f"migration_024_{uuid.uuid4().hex}"
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
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", database
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
        await admin.close()


async def _columns(connection: asyncpg.Connection, table: str) -> set[str]:
    rows = await connection.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
        """,
        table,
    )
    return {row["column_name"] for row in rows}


async def _indexes(connection: asyncpg.Connection) -> set[str]:
    rows = await connection.fetch(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'public'
          AND tablename IN ('scraping_missions', 'scraping_blueprints')
        """
    )
    return {row["indexname"] for row in rows}


async def _constraints(connection: asyncpg.Connection) -> set[tuple[str, str, str]]:
    rows = await connection.fetch(
        """
        SELECT table_name, constraint_name, constraint_type
        FROM information_schema.table_constraints
        WHERE table_schema = 'public'
          AND table_name IN ('scraping_missions', 'scraping_blueprints')
        """
    )
    return {
        (row["table_name"], row["constraint_name"], row["constraint_type"])
        for row in rows
    }


async def _schema_state(connection: asyncpg.Connection) -> SchemaState:
    return SchemaState(
        mission_columns=frozenset(await _columns(connection, "scraping_missions")),
        blueprint_columns=frozenset(await _columns(connection, "scraping_blueprints")),
        indexes=frozenset(await _indexes(connection)),
        constraints=frozenset(await _constraints(connection)),
    )


async def _assert_repaired(connection: asyncpg.Connection) -> None:
    assert _MISSION_COLUMNS <= await _columns(connection, "scraping_missions")
    assert _BLUEPRINT_COLUMNS <= await _columns(connection, "scraping_blueprints")
    assert _INDEXES <= await _indexes(connection)


async def _assert_each_repair_object_is_unique(connection: asyncpg.Connection) -> None:
    for table, names in [
        ("scraping_missions", _MISSION_COLUMNS),
        ("scraping_blueprints", _BLUEPRINT_COLUMNS),
    ]:
        for name in names:
            assert await connection.fetchval(
                """
                SELECT count(*)
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2
                """,
                table,
                name,
            ) == 1
    for name in _INDEXES:
        assert await connection.fetchval(
            """
            SELECT count(*)
            FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = $1
            """,
            name,
        ) == 1


async def _assert_legacy_rows_preserved(
    connection: asyncpg.Connection, mission_id: str, blueprint_id: str
) -> None:
    assert await connection.fetchval(
        "SELECT title FROM scraping_missions WHERE id = $1", mission_id
    ) == "Legacy mission"
    assert await connection.fetchval(
        "SELECT original_prompt FROM scraping_missions WHERE id = $1", mission_id
    ) == "Preserve this mission"
    assert await connection.fetchval(
        "SELECT blueprint_json::text FROM scraping_blueprints WHERE id = $1", blueprint_id
    ) == '{"legacy": true}'


async def _insert_legacy_blueprint(connection: asyncpg.Connection) -> tuple[str, str]:
    suffix = uuid.uuid4().hex
    org_id, user_id, mission_id, blueprint_id = (str(uuid.uuid4()) for _ in range(4))
    await connection.execute(
        """
        INSERT INTO organizations (id, name, slug)
        VALUES ($1, 'Migration test organization', $2)
        """,
        org_id,
        f"migration-{suffix}",
    )
    await connection.execute(
        """
        INSERT INTO users (id, email, hashed_password, full_name)
        VALUES ($1, $2, 'x', 'Migration Tester')
        """,
        user_id,
        f"{suffix}@example.test",
    )
    await connection.execute(
        """
        INSERT INTO scraping_missions (
            id, org_id, created_by, model_set_id, title, original_prompt, status, country_code, country_name
        )
        VALUES ($1, $2, $3, 'legacy-set', 'Legacy mission', 'Preserve this mission', 'draft', 'AT', 'Austria')
        """,
        mission_id,
        org_id,
        user_id,
    )
    await connection.execute(
        """
        INSERT INTO scraping_blueprints (
            id, mission_id, version, status, blueprint_json, model_set_id
        )
        VALUES ($1, $2, 1, 'draft', '{"legacy": true}'::json, 'legacy-set')
        """,
        blueprint_id,
        mission_id,
    )
    return mission_id, blueprint_id


@pytest.mark.asyncio
async def test_024_upgrades_normally_from_023(postgres_migration_database: PostgresMigrationDatabase) -> None:
    await postgres_migration_database.alembic("upgrade", "023")
    connection = await postgres_migration_database.connect()
    try:
        mission_id, blueprint_id = await _insert_legacy_blueprint(connection)
        before = await _schema_state(connection)
        await postgres_migration_database.alembic("upgrade", "024")
        await _assert_repaired(connection)
        assert await _schema_state(connection) == before
        await _assert_each_repair_object_is_unique(connection)
        await _assert_legacy_rows_preserved(connection, mission_id, blueprint_id)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_024_repairs_fully_drifted_database_stamped_at_023(
    postgres_migration_database: PostgresMigrationDatabase,
) -> None:
    await postgres_migration_database.alembic("upgrade", "020")
    connection = await postgres_migration_database.connect()
    try:
        mission_id, blueprint_id = await _insert_legacy_blueprint(connection)
        before = await _schema_state(connection)
        await postgres_migration_database.alembic("stamp", "023")
        await postgres_migration_database.alembic("upgrade", "024")
        await _assert_repaired(connection)
        after = await _schema_state(connection)
        assert after.mission_columns == before.mission_columns | _MISSION_COLUMNS
        assert after.blueprint_columns == before.blueprint_columns | _BLUEPRINT_COLUMNS
        assert after.indexes == before.indexes | _INDEXES
        assert after.constraints == before.constraints
        await _assert_legacy_rows_preserved(connection, mission_id, blueprint_id)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_024_repairs_partially_drifted_database(
    postgres_migration_database: PostgresMigrationDatabase,
) -> None:
    await postgres_migration_database.alembic("upgrade", "023")
    connection = await postgres_migration_database.connect()
    try:
        mission_id, blueprint_id = await _insert_legacy_blueprint(connection)
        before = await _schema_state(connection)
        await connection.execute(
            """
            DROP INDEX ix_scraping_missions_country_iso3;
            DROP INDEX ix_scraping_blueprints_provider_operation;
            ALTER TABLE scraping_missions DROP COLUMN continent;
            ALTER TABLE scraping_blueprints
                DROP COLUMN provider_operation_id,
                DROP COLUMN provider_execution_metadata;
            """
        )
        await postgres_migration_database.alembic("upgrade", "024")
        await _assert_repaired(connection)
        after = await _schema_state(connection)
        assert after == before
        assert {"country_iso3"} <= after.mission_columns
        assert {"provider", "prompt_template_version"} <= after.blueprint_columns
        assert "ix_scraping_blueprints_provider" in after.indexes
        assert (
            "scraping_blueprints",
            "uq_scraping_blueprint_mission_version",
            "UNIQUE",
        ) in after.constraints
        await _assert_legacy_rows_preserved(connection, mission_id, blueprint_id)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_024_downgrade_and_reupgrade_are_non_destructive(
    postgres_migration_database: PostgresMigrationDatabase,
) -> None:
    await postgres_migration_database.alembic("upgrade", "020")
    connection = await postgres_migration_database.connect()
    try:
        mission_id, blueprint_id = await _insert_legacy_blueprint(connection)
        await postgres_migration_database.alembic("stamp", "023")
        await postgres_migration_database.alembic("upgrade", "024")
        await postgres_migration_database.alembic("downgrade", "023")
        await _assert_repaired(connection)
        await postgres_migration_database.alembic("upgrade", "024")
        await _assert_repaired(connection)
        await _assert_legacy_rows_preserved(connection, mission_id, blueprint_id)
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_024_remains_linear_ancestor_of_single_head(
    postgres_migration_database: PostgresMigrationDatabase,
) -> None:
    await postgres_migration_database.alembic("upgrade", "024")
    current_024 = await postgres_migration_database.alembic("current")
    assert "024" in current_024

    await postgres_migration_database.alembic("upgrade", "head")
    heads = await postgres_migration_database.alembic("heads")
    current = await postgres_migration_database.alembic("current")

    # 024 remains a linear ancestor; exactly one Alembic head must exist.
    assert heads.count("(head)") == 1
    assert "034 (head)" in heads
    assert "034" in current
