"""Focused Package A PostgreSQL claim, replay, and ownership coverage."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import ScrapingFacilityPhaseWorkJob
from app.services.scraping.facility_phase_job_service import (
    claim_batch, complete_claim, create_job, fail_claim, work_fingerprint,
)
from app.services.scraping.facility_phase_orchestration_service import seed_document_preparation
from phase5a_postgres_support import create_phase5_database, drop_phase5_database
from test_phase5a_postgres import _seed_full_context

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def package_a_sessions() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    database = await create_phase5_database()
    engine = None
    try:
        await database.alembic("upgrade", "033")
        engine = create_async_engine(database.url.replace("postgresql://", "postgresql+asyncpg://"))
        yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    finally:
        if engine:
            await engine.dispose()
        await drop_phase5_database(database)


async def _seed_jobs(maker, count: int = 1):
    context = await _seed_full_context(maker)
    kinds = ("prepare_document", "extract_chunk", "verify_candidate", "deduplicate_candidate")
    async with maker.begin() as session:
        for index in range(count):
            await create_job(
                session, organization_id=context["org"], execution_id=context["execution"],
                work_kind=kinds[index], source_document_id=(
                    context["document"] if index < 2 else None),
                metadata={"ordinal": index},
            )
    return context


async def test_seed_and_job_identity_replay_are_idempotent(package_a_sessions):
    context = await _seed_full_context(package_a_sessions)
    async with package_a_sessions() as session:
        first = await seed_document_preparation(
            session, organization_id=context["org"], execution_id=context["execution"])
        replay = await seed_document_preparation(
            session, organization_id=context["org"], execution_id=context["execution"])
        count = await session.scalar(select(func.count()).select_from(ScrapingFacilityPhaseWorkJob))
    assert first == 1
    assert replay == 0
    assert count == 1
    base = dict(organization_id=context["org"], execution_id=context["execution"],
                work_kind="extract_chunk", source_document_id=context["document"])
    assert work_fingerprint(**base) == work_fingerprint(**base)
    assert work_fingerprint(**base, version="v2") != work_fingerprint(**base)


async def test_skip_locked_claims_are_disjoint_and_batch_is_not_global_limit(package_a_sessions):
    context = await _seed_jobs(package_a_sessions, count=4)
    async def claim():
        async with package_a_sessions() as session:
            return await claim_batch(
                session, organization_id=context["org"], execution_id=context["execution"],
                batch_size=2, lease_duration=timedelta(minutes=5))
    left, right = await asyncio.gather(claim(), claim())
    assert {item.id for item in left}.isdisjoint({item.id for item in right})
    assert len(left) == len(right) == 2


async def test_live_lease_cannot_be_stolen_and_expired_lease_is_reclaimed(package_a_sessions):
    context = await _seed_jobs(package_a_sessions)
    async with package_a_sessions() as session:
        first = await claim_batch(
            session, organization_id=context["org"], execution_id=context["execution"],
            batch_size=1, lease_duration=timedelta(minutes=5))
    async with package_a_sessions() as session:
        assert await claim_batch(
            session, organization_id=context["org"], execution_id=context["execution"],
            batch_size=1, lease_duration=timedelta(minutes=5)) == []
        await session.execute(update(ScrapingFacilityPhaseWorkJob).where(
            ScrapingFacilityPhaseWorkJob.id == first[0].id
        ).values(lease_expires_at=func.now() - timedelta(seconds=1)))
        await session.commit()
        reclaimed = await claim_batch(
            session, organization_id=context["org"], execution_id=context["execution"],
            batch_size=1, lease_duration=timedelta(minutes=5))
    assert reclaimed[0].id == first[0].id
    assert reclaimed[0].claim_token != first[0].claim_token


async def test_token_and_owner_fence_completion_and_retry(package_a_sessions):
    context = await _seed_jobs(package_a_sessions)
    async with package_a_sessions() as session:
        claim = (await claim_batch(
            session, organization_id=context["org"], execution_id=context["execution"],
            batch_size=1, lease_duration=timedelta(minutes=5)))[0]
        assert not await complete_claim(
            session, organization_id=context["org"], execution_id=context["execution"],
            job_id=claim.id, claim_token="stale")
        assert not await complete_claim(
            session, organization_id="wrong-org", execution_id=context["execution"],
            job_id=claim.id, claim_token=claim.claim_token)
        assert not await fail_claim(
            session, organization_id=context["org"], execution_id="wrong-execution",
            job_id=claim.id, claim_token=claim.claim_token, classification="x",
            safe_message="safe", retryable=True, retry_delay=timedelta(seconds=1))
        assert await fail_claim(
            session, organization_id=context["org"], execution_id=context["execution"],
            job_id=claim.id, claim_token=claim.claim_token, classification="timeout",
            safe_message="Provider request failed", retryable=True,
            retry_delay=timedelta(seconds=1))
        row = await session.get(ScrapingFacilityPhaseWorkJob, claim.id)
    assert row.status == "retry_scheduled"
    assert row.claim_token is None
    assert row.safe_error_message == "Provider request failed"


async def test_database_rejects_cross_tenant_work_inputs(package_a_sessions):
    left = await _seed_full_context(package_a_sessions)
    right = await _seed_full_context(package_a_sessions)
    async with package_a_sessions() as session:
        with pytest.raises(IntegrityError):
            await create_job(
                session, organization_id=left["org"], execution_id=left["execution"],
                work_kind="prepare_document", source_document_id=right["document"])
