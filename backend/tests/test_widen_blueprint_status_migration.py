"""PostgreSQL coverage for migration 025 status column widening."""

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_country_blueprint_foundation import valid_structured_blueprint

from app.db.models import ScrapingBlueprint, ScrapingBlueprintStatus
from app.schemas.api import CountryMaximumCoverageStructuredBlueprint
from app.services.scraping import blueprint_generation_orchestrator as orchestrator
from app.services.scraping.blueprint_provider import BlueprintProviderResult

_ALL_STATUSES = [status.value for status in ScrapingBlueprintStatus]
_LONGEST = max(_ALL_STATUSES, key=len)


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

    database = f"migration_025_{uuid.uuid4().hex}"
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


async def _status_column_length(connection: asyncpg.Connection) -> int:
    row = await connection.fetchrow(
        """
        SELECT character_maximum_length
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'scraping_blueprints'
          AND column_name = 'status'
        """
    )
    assert row is not None
    return int(row["character_maximum_length"])


async def _seed_mission_and_blueprint(
    connection: asyncpg.Connection, *, status: str = "queued"
) -> tuple[str, str]:
    org_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    mission_id = str(uuid.uuid4())
    blueprint_id = str(uuid.uuid4())
    await connection.execute(
        """
        INSERT INTO organizations (id, name, slug)
        VALUES ($1, 'Migration test organization', $2)
        """,
        org_id,
        f"migration-{uuid.uuid4().hex[:8]}",
    )
    await connection.execute(
        """
        INSERT INTO users (id, email, hashed_password, full_name)
        VALUES ($1, $2, 'x', 'Migration Tester')
        """,
        user_id,
        f"{uuid.uuid4().hex}@example.test",
    )
    await connection.execute(
        """
        INSERT INTO scraping_missions (
            id, org_id, created_by, model_set_id, title, original_prompt, status, country_code, country_name
        )
        VALUES ($1, $2, $3, 'scraper-fixed', 'Austria', 'Plan', 'draft', 'AT', 'Austria')
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
        VALUES ($1, $2, 1, $3, 'scraper-fixed')
        """,
        blueprint_id,
        mission_id,
        status,
    )
    return mission_id, blueprint_id


@pytest.mark.asyncio
async def test_024_to_025_widens_status_and_preserves_rows(postgres_migration_database):
    db = postgres_migration_database
    await db.alembic("upgrade", "024")
    connection = await db.connect()
    try:
        assert await _status_column_length(connection) == 10
        _, blueprint_id = await _seed_mission_and_blueprint(connection, status="queued")
        with pytest.raises(asyncpg.exceptions.StringDataRightTruncationError):
            await connection.execute(
                "UPDATE scraping_blueprints SET status = $1 WHERE id = $2",
                "ready_for_review",
                blueprint_id,
            )
        await connection.execute("ROLLBACK")
    finally:
        await connection.close()

    await db.alembic("upgrade", "025")
    connection = await db.connect()
    try:
        assert await _status_column_length(connection) == 32
        row = await connection.fetchrow(
            "SELECT status FROM scraping_blueprints WHERE id = $1",
            blueprint_id,
        )
        assert row["status"] == "queued"
        await connection.execute(
            "UPDATE scraping_blueprints SET status = $1 WHERE id = $2",
            "ready_for_review",
            blueprint_id,
        )
        stored = await connection.fetchval(
            "SELECT status FROM scraping_blueprints WHERE id = $1", blueprint_id
        )
        assert stored == "ready_for_review"
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_025_stores_all_lifecycle_statuses(postgres_migration_database):
    db = postgres_migration_database
    await db.alembic("upgrade", "025")
    assert len(_LONGEST) == 16
    assert _LONGEST == "ready_for_review"
    connection = await db.connect()
    try:
        _, blueprint_id = await _seed_mission_and_blueprint(connection, status="draft")
        for status in _ALL_STATUSES:
            await connection.execute(
                "UPDATE scraping_blueprints SET status = $1 WHERE id = $2",
                status,
                blueprint_id,
            )
            assert (
                await connection.fetchval(
                    "SELECT status FROM scraping_blueprints WHERE id = $1", blueprint_id
                )
                == status
            )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_025_downgrade_is_non_destructive_and_reupgrade_succeeds(postgres_migration_database):
    db = postgres_migration_database
    await db.alembic("upgrade", "025")
    connection = await db.connect()
    try:
        _, blueprint_id = await _seed_mission_and_blueprint(connection, status="ready_for_review")
    finally:
        await connection.close()

    await db.alembic("downgrade", "024")
    connection = await db.connect()
    try:
        # Downgrade must not shrink the column or destroy ready_for_review rows.
        assert await _status_column_length(connection) == 32
        assert (
            await connection.fetchval(
                "SELECT status FROM scraping_blueprints WHERE id = $1", blueprint_id
            )
            == "ready_for_review"
        )
    finally:
        await connection.close()

    await db.alembic("upgrade", "025")
    heads = await db.alembic("heads")
    current = await db.alembic("current")
    # After upgrading only to 025, current is 025 while the repo head may be later.
    assert "025" in current
    assert heads.count("(head)") == 1
    assert "034 (head)" in heads


@pytest.mark.asyncio
async def test_generated_blueprint_can_commit_ready_for_review(postgres_migration_database, monkeypatch):
    db = postgres_migration_database
    await db.alembic("upgrade", "head")
    connection = await db.connect()
    try:
        _, blueprint_id = await _seed_mission_and_blueprint(connection, status="queued")
        await connection.execute(
            """
            UPDATE scraping_blueprints
            SET rendered_prompt_snapshot = $1,
                country_name_snapshot = 'Austria',
                country_iso3_snapshot = 'AUT',
                continent_snapshot = 'Europe',
                provider = 'openrouter',
                provider_model_id = 'openai/gpt-5.5'
            WHERE id = $2
            """,
            "Austria coverage",
            blueprint_id,
        )
    finally:
        await connection.close()

    engine = create_async_engine(db.url.replace("postgresql://", "postgresql+asyncpg://"))
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    class SuccessProvider:
        def __init__(self, _settings) -> None:
            pass

        async def generate_blueprint(self, **_kwargs):
            structured = CountryMaximumCoverageStructuredBlueprint.model_validate(
                valid_structured_blueprint()
            )
            return BlueprintProviderResult(
                human_readable_blueprint="Human research blueprint",
                structured_blueprint=structured,
                citations=[{"url": "https://example.test/a", "title": "A"}],
                provider="openrouter",
                model_id="openai/gpt-5.5",
                execution_metadata={"structuring_correction_attempted": False},
                operation_id="op-1",
            )

    monkeypatch.setattr(orchestrator, "AsyncSessionLocal", maker)
    monkeypatch.setattr(orchestrator, "OpenRouterBlueprintProvider", SuccessProvider)

    await orchestrator.run_blueprint_generation({}, blueprint_id)

    async with maker() as session:
        refreshed = await session.get(ScrapingBlueprint, blueprint_id)
        assert refreshed is not None
        assert refreshed.status == ScrapingBlueprintStatus.READY_FOR_REVIEW
        assert refreshed.human_readable_blueprint == "Human research blueprint"
        assert refreshed.structured_blueprint is not None
    await engine.dispose()
