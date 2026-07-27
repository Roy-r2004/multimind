"""PostgreSQL concurrency coverage for Step 3B deterministic query generation."""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from unittest.mock import AsyncMock

import asyncpg
import pytest
from conftest import valid_blueprint
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_mission_campaign_lifecycle import lebanon_structured_blueprint

from app.core.dependencies import AuthContext
from app.db.models import (
    Organization,
    OrgRole,
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingExecution,
    ScrapingMission,
    ScrapingMissionStatus,
    ScrapingRun,
    ScrapingRunAgent,
    ScrapingRunStatus,
    ScrapingSourceDiscoveryQuery,
    User,
)
from app.schemas.scraping_clarification import ClarificationStatus
from app.schemas.scraping_execution_plan import parse_frozen_execution_plan
from app.services.scraping.execution_service import execution_service
from app.services.scraping.query_generation_service import (
    PURPOSE_SEED,
    PRIORITY_SEED,
    generate_query_job_specs,
    normalize_identity_text,
    query_generation_service,
)


@dataclass
class PostgresConcurrencyDatabase:
    """Isolated PostgreSQL database initialized via Alembic through revision 028."""

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


@pytest.fixture
async def postgres_sessions() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    admin_url = os.environ.get("POSTGRES_TEST_ADMIN_URL")
    if not admin_url:
        pytest.fail("POSTGRES_TEST_ADMIN_URL is required for PostgreSQL concurrency coverage.")
    database = f"query_gen_concurrency_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(admin_url)
    await admin.execute(f'CREATE DATABASE "{database}"')
    db = PostgresConcurrencyDatabase(
        admin=admin,
        database=database,
        url=admin_url.rsplit("/", 1)[0] + f"/{database}",
    )
    engine = None
    try:
        # Real migration chain through 028 — never Base.metadata.create_all().
        await db.alembic("upgrade", "028")
        engine = create_async_engine(
            db.url.replace("postgresql://", "postgresql+asyncpg://")
        )
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        yield maker
    finally:
        if engine is not None:
            await engine.dispose()
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1",
            database,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{database}"')
        await admin.close()


async def _make_auth(session: AsyncSession) -> AuthContext:
    org = Organization(
        name="Query concurrency",
        slug=f"qgen-{uuid.uuid4().hex[:8]}",
    )
    user = User(
        email=f"{uuid.uuid4().hex}@example.test",
        hashed_password="x",
        full_name="Owner",
    )
    session.add_all([org, user])
    await session.flush()
    return AuthContext(user=user, org_id=org.id, role=OrgRole.OWNER)


def _collision_blueprint() -> dict:
    """Seed text equals regulatory countrywide expansion for semantic collision coverage."""
    payload = lebanon_structured_blueprint()
    payload["private_paid_terminology"] = ["private"]
    payload["inpatient_residential_terminology"] = ["inpatient"]
    payload["addiction_categories"] = ["addiction"]
    payload["local_terminology"] = ["rehabilitation"]
    payload["regions"] = ["Beirut"]
    payload["important_cities"] = [{"name": "Beirut", "region_name": "Beirut"}]
    payload["languages"] = ["English"]
    payload["language_profiles"] = [{"name": "English", "code": "en", "script": "Latn"}]
    payload["query_matrix"] = [
        {
            "query": "private inpatient addiction rehabilitation Lebanon",
            "language": "English",
            "purpose": "seed collision",
        }
    ]
    return payload


@pytest.mark.asyncio
async def test_concurrent_generate_is_idempotent_and_seed_wins(
    postgres_sessions, monkeypatch
) -> None:
    async with postgres_sessions() as setup:
        auth = await _make_auth(setup)
        # model_set_id is a free-form string on missions/blueprints (no FK / no Strategy enum).
        mission = ScrapingMission(
            org_id=auth.org_id,
            created_by=auth.user.id,
            model_set_id="research-set",
            title="Concurrency mission",
            original_prompt="Find facilities",
            country_code="LB",
            country_name="Lebanon",
            country_iso3="LBN",
            continent="Asia",
            status=ScrapingMissionStatus.APPROVED,
        )
        setup.add(mission)
        await setup.flush()
        blueprint = ScrapingBlueprint(
            mission_id=mission.id,
            version=1,
            status=ScrapingBlueprintStatus.APPROVED,
            blueprint_json=valid_blueprint(),
            structured_blueprint=_collision_blueprint(),
            model_set_id="research-set",
        )
        setup.add(blueprint)
        await setup.flush()
        mission.active_blueprint_id = blueprint.id
        run = ScrapingRun(
            organization_id=auth.org_id,
            mission_id=mission.id,
            blueprint_id=blueprint.id,
            model_set_id="research-set",
            status=ScrapingRunStatus.PLANNED,
        )
        setup.add(run)
        await setup.flush()
        setup.add(
            ScrapingRunAgent(
                run_id=run.id,
                sequence=1,
                name="Planner",
                role="planner",
                purpose="Prepare campaign checkpoints",
                instructions="Run deterministic checkpoints only.",
                model_id="gpt-4.1",
            )
        )
        await setup.commit()

    monkeypatch.setattr(execution_service, "enqueue_execution", AsyncMock())

    async with postgres_sessions() as session:
        summary = await execution_service.start_mission_campaign(session, auth, mission.id)
        execution = await session.get(ScrapingExecution, summary.id)
        assert execution is not None
        execution.clarification_status = ClarificationStatus.NOT_REQUIRED.value
        execution.resolved_execution_plan_json = None
        await session.commit()
        execution_id = execution.id
        plan = parse_frozen_execution_plan(execution.frozen_execution_plan_json)
        plan_hash = execution.execution_plan_hash
        expected_specs = generate_query_job_specs(
            plan, plan_hash=plan_hash, discovery_round=1
        )
        expected_total = len(expected_specs)
        seed_collision = [
            s
            for s in expected_specs
            if normalize_identity_text(s.query_text)
            == normalize_identity_text("private inpatient addiction rehabilitation Lebanon")
            and s.source_category == "regulatory"
            and s.scope_level == "countrywide"
        ]
        assert len(seed_collision) == 1
        assert seed_collision[0].purpose == PURPOSE_SEED

    async def worker() -> dict:
        async with postgres_sessions() as session:
            row = await session.get(ScrapingExecution, execution_id)
            assert row is not None
            result = await query_generation_service.generate_for_execution(
                session, row, discovery_round=1
            )
            await session.commit()
            # Session must remain usable after success (not left in failed txn).
            await session.execute(select(func.count()).select_from(ScrapingExecution))
            return {
                "status": result.status,
                "generated_count": result.generated_count,
                "existing_count": result.existing_count,
                "total_count": result.total_count,
                "error_code": result.error_code,
            }

    results = await asyncio.gather(worker(), worker())
    assert all(r["status"] == "ok" for r in results)
    assert all(r["error_code"] is None for r in results)
    assert all(r["total_count"] == expected_total for r in results)
    assert sum(r["generated_count"] for r in results) == expected_total
    assert sum(r["existing_count"] for r in results) == expected_total

    async with postgres_sessions() as verify:
        rows = (
            await verify.execute(
                select(ScrapingSourceDiscoveryQuery).where(
                    ScrapingSourceDiscoveryQuery.execution_id == execution_id
                )
            )
        ).scalars().all()
        assert len(rows) == expected_total
        fingerprints = [r.query_job_fingerprint for r in rows]
        assert None not in fingerprints
        assert len(fingerprints) == len(set(fingerprints))

        matching = [
            r
            for r in rows
            if normalize_identity_text(r.query_text)
            == normalize_identity_text("private inpatient addiction rehabilitation Lebanon")
            and r.source_category == "regulatory"
            and r.scope_level == "countrywide"
        ]
        assert len(matching) == 1
        assert matching[0].purpose == PURPOSE_SEED
        assert matching[0].priority == PRIORITY_SEED

        # Existing rows are not rewritten on a third idempotent pass.
        sample = rows[0]
        original_purpose = sample.purpose
        sample.purpose = "mutated_should_stick"
        await verify.commit()

    async with postgres_sessions() as session:
        row = await session.get(ScrapingExecution, execution_id)
        assert row is not None
        third = await query_generation_service.generate_for_execution(
            session, row, discovery_round=1
        )
        await session.commit()
        assert third.status == "ok"
        assert third.generated_count == 0
        assert third.existing_count == expected_total

    async with postgres_sessions() as verify:
        stuck = await verify.get(ScrapingSourceDiscoveryQuery, sample.id)
        assert stuck is not None
        assert stuck.purpose == "mutated_should_stick"
        assert original_purpose != "mutated_should_stick"
