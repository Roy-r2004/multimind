"""Real Docker PostgreSQL coverage for mission-scoped blueprint allocation."""

import asyncio
import os
import subprocess
import uuid

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError
from app.db.models import OrgRole, Organization, ScrapingBlueprint, ScrapingMission, User
from app.services.scraping.blueprint_service import blueprint_service


@pytest.fixture
async def postgres_sessions():
    admin_url = os.environ.get("POSTGRES_TEST_ADMIN_URL")
    if not admin_url:
        pytest.fail("POSTGRES_TEST_ADMIN_URL is required for PostgreSQL concurrency coverage.")
    database = f"phase1b_concurrency_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(admin_url)
    await admin.execute(f'CREATE DATABASE "{database}"')
    url = admin_url.rsplit("/", 1)[0] + f"/{database}"
    try:
        subprocess.run(
            ["alembic", "upgrade", "head"],
            check=True,
            env={**os.environ, "DATABASE_URL": url.replace("postgresql://", "postgresql+asyncpg://")},
        )
        engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        yield maker
        await engine.dispose()
    finally:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1", database
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
        await admin.close()


async def _make_auth(session: AsyncSession) -> AuthContext:
    org = Organization(name="Concurrency", slug=f"concurrency-{uuid.uuid4().hex[:8]}")
    user = User(email=f"{uuid.uuid4().hex}@example.test", hashed_password="x", full_name="Owner")
    session.add_all([org, user])
    await session.flush()
    return AuthContext(user=user, org_id=org.id, role=OrgRole.OWNER)


@pytest.mark.asyncio
async def test_same_mission_concurrent_generation_is_serialized(postgres_sessions, monkeypatch):
    async with postgres_sessions() as setup:
        auth = await _make_auth(setup)
        mission = ScrapingMission(
            org_id=auth.org_id, created_by=auth.user.id, model_set_id="research-set",
            title="Austria", original_prompt="Plan", country_code="AT", country_name="Austria",
            country_iso3="AUT", continent="Europe",
        )
        setup.add(mission)
        await setup.flush()
        await setup.commit()

    async def no_enqueue(_blueprint_id: str) -> None:
        return None

    monkeypatch.setattr(blueprint_service, "enqueue_blueprint", no_enqueue)

    async def allocate():
        async with postgres_sessions() as session:
            return await blueprint_service.generate_blueprint(session, auth, mission.id)

    results = await asyncio.gather(allocate(), allocate(), return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert any(isinstance(result, ConflictError) for result in results)

    async with postgres_sessions() as verify:
        rows = (await verify.execute(
            ScrapingBlueprint.__table__.select().where(ScrapingBlueprint.mission_id == mission.id)
        )).mappings().all()
    assert [row["version"] for row in rows] == [1]


@pytest.mark.asyncio
async def test_different_missions_allocate_independently(postgres_sessions, monkeypatch):
    async with postgres_sessions() as setup:
        auth = await _make_auth(setup)
        missions = [
            ScrapingMission(
                org_id=auth.org_id, created_by=auth.user.id, model_set_id="research-set",
                title=f"Austria {index}", original_prompt="Plan", country_code="AT",
                country_name="Austria", country_iso3="AUT", continent="Europe",
            )
            for index in range(2)
        ]
        setup.add_all(missions)
        await setup.commit()

    async def no_enqueue(_blueprint_id: str) -> None:
        return None

    monkeypatch.setattr(blueprint_service, "enqueue_blueprint", no_enqueue)

    async def allocate(mission_id: str):
        async with postgres_sessions() as session:
            return await blueprint_service.generate_blueprint(session, auth, mission_id)

    results = await asyncio.gather(*(allocate(mission.id) for mission in missions))
    assert [result.version for result in results] == [1, 1]
