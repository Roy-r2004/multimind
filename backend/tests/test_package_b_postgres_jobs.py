"""Package B PostgreSQL job identity, claims, leases, and fencing."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import ScrapingFacilityPhaseWorkJob
from app.services.scraping.facility_phase_job_service import (
    claim_batch,
    complete_claim,
    create_job,
)
from phase5a_postgres_support import create_phase5_database, drop_phase5_database
from test_phase5a_postgres import _seed_full_context

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def package_b_sessions(
) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    database = await create_phase5_database()
    engine = None
    try:
        await database.alembic("upgrade", "035")
        engine = create_async_engine(
            database.url.replace("postgresql://", "postgresql+asyncpg://")
        )
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        if engine:
            await engine.dispose()
        await drop_phase5_database(database)


async def test_package_b_jobs_are_idempotent_kind_filtered_and_fenced(
    package_b_sessions,
):
    context = await _seed_full_context(package_b_sessions)
    async with package_b_sessions() as session:
        created = {}
        for kind in (
            "publish_candidate", "generate_execution_export", "finalize_execution"
        ):
            created[kind] = await create_job(
                session, organization_id=context["org"],
                execution_id=context["execution"], work_kind=kind,
            )
            assert await create_job(
                session, organization_id=context["org"],
                execution_id=context["execution"], work_kind=kind,
            ) is None
        count = await session.scalar(
            select(func.count()).select_from(ScrapingFacilityPhaseWorkJob).where(
                ScrapingFacilityPhaseWorkJob.execution_id == context["execution"]
            )
        )
        assert count == 3

        publish = await claim_batch(
            session, organization_id=context["org"],
            execution_id=context["execution"], batch_size=10,
            lease_duration=timedelta(minutes=5),
            work_kinds={"publish_candidate"},
        )
        assert [claim.id for claim in publish] == [created["publish_candidate"]]
        assert not await complete_claim(
            session, organization_id=context["org"],
            execution_id=context["execution"], job_id=publish[0].id,
            claim_token="stale",
        )
        assert await complete_claim(
            session, organization_id=context["org"],
            execution_id=context["execution"], job_id=publish[0].id,
            claim_token=publish[0].claim_token,
        )


async def test_package_b_live_lease_is_protected_and_expired_lease_reclaims(
    package_b_sessions,
):
    context = await _seed_full_context(package_b_sessions)
    async with package_b_sessions() as session:
        job_id = await create_job(
            session, organization_id=context["org"],
            execution_id=context["execution"],
            work_kind="generate_execution_export",
        )
        first = (await claim_batch(
            session, organization_id=context["org"],
            execution_id=context["execution"], batch_size=1,
            lease_duration=timedelta(minutes=5),
            work_kinds={"generate_execution_export"},
        ))[0]
        assert await claim_batch(
            session, organization_id=context["org"],
            execution_id=context["execution"], batch_size=1,
            lease_duration=timedelta(minutes=5),
            work_kinds={"generate_execution_export"},
        ) == []
        await session.execute(update(ScrapingFacilityPhaseWorkJob).where(
            ScrapingFacilityPhaseWorkJob.id == job_id
        ).values(
            claimed_at=func.now() - timedelta(minutes=10),
            lease_expires_at=func.now() - timedelta(minutes=5),
        ))
        await session.commit()
        reclaimed = (await claim_batch(
            session, organization_id=context["org"],
            execution_id=context["execution"], batch_size=1,
            lease_duration=timedelta(minutes=5),
            work_kinds={"generate_execution_export"},
        ))[0]
        assert reclaimed.id == first.id
        assert reclaimed.claim_token != first.claim_token
