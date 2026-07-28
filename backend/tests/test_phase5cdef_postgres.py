"""Unexecuted PostgreSQL coverage for Phase 5C-F ownership and restart gates."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.db.models import ScrapingPhase5RetrievalResult, ScrapingPhase5WorkJob
from app.services.scraping.phase5_contracts import Phase5WorkKind, prepare_phase5_job
from app.services.scraping.phase5_job_service import create_job_idempotently
from app.services.scraping.phase5_orchestration_service import phase5_readiness
from phase5_postgres_fixtures import fetch_database_now
from test_phase5a_postgres import _seed_full_context
from test_phase5b_directory_expansion_postgres import phase5b_sessions

NOW = datetime(2026, 7, 28, tzinfo=UTC)


@pytest.mark.asyncio
async def test_http_firecrawl_playwright_jobs_are_replay_safe_and_distinct(phase5b_sessions):
    context = await _seed_full_context(phase5b_sessions)
    async with phase5b_sessions.begin() as session:
        requested_at = await fetch_database_now(session)
    common = dict(
        organization_id=context["org"], execution_id=context["execution"],
        crawl_node_id=context["node"], source_candidate_id=context["candidate"],
        original_url="https://docs.python.org/list",
        source_classification="directory", requested_at=requested_at)
    jobs = [
        prepare_phase5_job(**common, work_kind=kind, selected_tool=tool)
        for kind, tool in (
            (Phase5WorkKind.HTTP_RETRIEVAL, "http"),
            (Phase5WorkKind.FIRECRAWL_RETRIEVAL, "firecrawl"),
            (Phase5WorkKind.PLAYWRIGHT_RETRIEVAL, "playwright"),
        )
    ]
    async with phase5b_sessions.begin() as session:
        first = [await create_job_idempotently(session, job) for job in jobs]
        replay = [await create_job_idempotently(session, job) for job in jobs]
    assert len({item.record_id for item in first}) == 3
    assert [item.record_id for item in first] == [item.record_id for item in replay]


@pytest.mark.asyncio
async def test_phase5_readiness_rejects_pending_active_and_unseeded_work(phase5b_sessions):
    context = await _seed_full_context(phase5b_sessions)
    async with phase5b_sessions() as session:
        readiness = await phase5_readiness(
            session, organization_id=context["org"], execution_id=context["execution"])
    assert not readiness.ready_for_review
    assert readiness.unseeded_candidates == 1


@pytest.mark.asyncio
async def test_retrieval_result_uniqueness_is_database_guarded(phase5b_sessions):
    context = await _seed_full_context(phase5b_sessions)
    async with phase5b_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(
            ScrapingPhase5RetrievalResult)) == 0
        assert await session.scalar(select(func.count()).select_from(
            ScrapingPhase5WorkJob)) == 0
