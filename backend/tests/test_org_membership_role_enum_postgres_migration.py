"""Migration 034 PostgreSQL contract for organization membership roles."""

from __future__ import annotations

import subprocess
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import OrgMembership, OrgRole, User
from app.services.scraping.facility_phase_job_service import create_job
from phase5a_postgres_support import create_phase5_database, drop_phase5_database
from test_phase5a_postgres import _seed_full_context

EXPECTED_ENUM_LABELS = ["OWNER", "ADMIN", "MEMBER", "VIEWER"]
HISTORICAL_ROLES = {
    "owner": "OWNER",
    "ADMIN": "ADMIN",
    " Member ": "MEMBER",
    "vIeWeR": "VIEWER",
}
PACKAGE_A_TABLES = (
    "scraping_facility_phase_work_jobs",
    "scraping_facility_candidate_decisions",
    "scraping_facility_candidate_duplicates",
)


def test_034_labels_exactly_match_the_orm_enum_contract():
    assert [role.name for role in OrgRole] == EXPECTED_ENUM_LABELS
    assert list(OrgMembership.__table__.c.role.type.enums) == EXPECTED_ENUM_LABELS


async def _seed_historical_memberships(connection):
    organization_id = str(uuid.uuid4())
    await connection.execute(
        "INSERT INTO organizations (id,name,slug) VALUES ($1,$2,$3)",
        organization_id,
        "Role migration",
        f"role-migration-{uuid.uuid4().hex[:8]}",
    )
    rows = []
    for index, historical in enumerate(HISTORICAL_ROLES):
        user_id = str(uuid.uuid4())
        membership_id = str(uuid.uuid4())
        await connection.execute(
            "INSERT INTO users (id,email,hashed_password,full_name) "
            "VALUES ($1,$2,$3,$4)",
            user_id,
            f"role-{index}-{uuid.uuid4().hex}@example.test",
            "x",
            f"Role {index}",
        )
        await connection.execute(
            "INSERT INTO org_memberships (id,org_id,user_id,role) "
            "VALUES ($1,$2,$3,$4)",
            membership_id,
            organization_id,
            user_id,
            historical,
        )
        rows.append((membership_id, HISTORICAL_ROLES[historical]))
    return organization_id, rows


async def _package_state(connection):
    return {
        table: [
            dict(row)
            for row in await connection.fetch(
                f"SELECT * FROM {table} ORDER BY id")
        ]
        for table in PACKAGE_A_TABLES
    }


@pytest.mark.asyncio
async def test_034_upgrade_downgrade_reupgrade_preserves_roles_and_package_a():
    database = await create_phase5_database()
    engine = None
    try:
        await database.alembic("upgrade", "033")
        connection = await database.connect()
        try:
            assert await connection.fetchval(
                "SELECT version_num FROM alembic_version") == "033"
            assert await connection.fetchval(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='org_memberships' "
                "AND column_name='role'"
            ) == "character varying"
            organization_id, historical_rows = await _seed_historical_memberships(
                connection)
        finally:
            await connection.close()

        engine = create_async_engine(
            database.url.replace("postgresql://", "postgresql+asyncpg://"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        context = await _seed_full_context(sessions)
        async with sessions.begin() as session:
            await create_job(
                session,
                organization_id=context["org"],
                execution_id=context["execution"],
                work_kind="prepare_document",
                source_document_id=context["document"],
            )
        await engine.dispose()
        engine = None

        connection = await database.connect()
        try:
            package_before = await _package_state(connection)
        finally:
            await connection.close()

        await database.alembic("upgrade", "034")
        current = await database.alembic("current")
        heads = await database.alembic("heads")
        assert "034" in current
        assert [line.strip() for line in heads.splitlines() if "(head)" in line] == [
            "035 (head)"
        ]
        connection = await database.connect()
        try:
            labels = [
                row["enumlabel"]
                for row in await connection.fetch(
                    "SELECT e.enumlabel FROM pg_type t "
                    "JOIN pg_enum e ON e.enumtypid=t.oid "
                    "WHERE t.typname='orgrole' ORDER BY e.enumsortorder"
                )
            ]
            assert labels == EXPECTED_ENUM_LABELS
            migrated = {
                row["id"]: row["role"]
                for row in await connection.fetch(
                    "SELECT id,role::text AS role FROM org_memberships "
                    "WHERE id = ANY($1::varchar[])",
                    [row_id for row_id, _expected in historical_rows],
                )
            }
            assert migrated == dict(historical_rows)
            assert await _package_state(connection) == package_before
        finally:
            await connection.close()

        engine = create_async_engine(
            database.url.replace("postgresql://", "postgresql+asyncpg://"))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            for role in OrgRole:
                user = User(
                    email=f"orm-{role.value}-{uuid.uuid4().hex}@example.test",
                    hashed_password="x",
                    full_name=f"ORM {role.value}",
                )
                session.add(user)
                await session.flush()
                session.add(OrgMembership(
                    org_id=organization_id, user_id=user.id, role=role))
        await engine.dispose()
        engine = None

        await database.alembic("downgrade", "033")
        connection = await database.connect()
        try:
            assert await connection.fetchval(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='org_memberships' "
                "AND column_name='role'"
            ) == "character varying"
            assert not await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname='orgrole')")
            downgraded = {
                row["id"]: row["role"]
                for row in await connection.fetch(
                    "SELECT id,role FROM org_memberships "
                    "WHERE id = ANY($1::varchar[])",
                    [row_id for row_id, _expected in historical_rows],
                )
            }
            assert downgraded == {
                row_id: expected.lower()
                for row_id, expected in historical_rows
            }
            assert await _package_state(connection) == package_before
        finally:
            await connection.close()

        await database.alembic("upgrade", "034")
        connection = await database.connect()
        try:
            assert await connection.fetchval(
                "SELECT version_num FROM alembic_version") == "034"
            assert await _package_state(connection) == package_before
        finally:
            await connection.close()
    finally:
        if engine is not None:
            await engine.dispose()
        await drop_phase5_database(database)


@pytest.mark.asyncio
async def test_034_rejects_unsupported_historical_role_without_partial_upgrade():
    database = await create_phase5_database()
    try:
        await database.alembic("upgrade", "033")
        connection = await database.connect()
        try:
            organization_id = str(uuid.uuid4())
            user_id = str(uuid.uuid4())
            await connection.execute(
                "INSERT INTO organizations (id,name,slug) VALUES ($1,$2,$3)",
                organization_id, "Unsafe role", f"unsafe-{uuid.uuid4().hex[:8]}")
            await connection.execute(
                "INSERT INTO users (id,email,hashed_password,full_name) "
                "VALUES ($1,$2,$3,$4)",
                user_id, f"unsafe-{uuid.uuid4().hex}@example.test", "x", "Unsafe")
            await connection.execute(
                "INSERT INTO org_memberships (id,org_id,user_id,role) "
                "VALUES ($1,$2,$3,$4)",
                str(uuid.uuid4()), organization_id, user_id, "superuser")
        finally:
            await connection.close()
        with pytest.raises(subprocess.CalledProcessError) as failure:
            await database.alembic("upgrade", "034")
        assert "unsupported value(s)" in (failure.value.stderr or "")
        connection = await database.connect()
        try:
            assert await connection.fetchval(
                "SELECT version_num FROM alembic_version") == "033"
            assert not await connection.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname='orgrole')")
        finally:
            await connection.close()
    finally:
        await drop_phase5_database(database)
