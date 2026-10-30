"""Regression test for the real PostgreSQL syntax error in
``eligible_candidates_query`` (maps_evidence_backfill.py).

SQLAlchemy's ``Column.isnot("drop")`` compiles to ``IS NOT $1``.
PostgreSQL's ``IS NOT`` only accepts NULL or a boolean literal on its
right-hand side — never an arbitrary string — so asyncpg rejects the
compiled query outright:

    asyncpg.exceptions.PostgresSyntaxError: syntax error at or near "$2"

SQLite silently accepts the same SQL (it has no such restriction), which
is exactly why every other test for this module — all running against the
in-memory SQLite ``db`` fixture — passed while the real, PostgreSQL-backed
deployment failed. This test runs the ACTUAL query against a real
PostgreSQL server rather than SQLite or a compiled-SQL string check, so a
regression here can never hide behind SQLite's looser dialect again.

Requires a real PostgreSQL server reachable at $MAPS_TEST_POSTGRES_URL
(or localhost:55432 by default, matching the ephemeral, unrelated-to-
production container used to verify this fix). Skips cleanly when no
PostgreSQL server is reachable rather than failing the whole suite.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import (
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsPlace,
    Organization,
    User,
)
from app.services.scraping.maps_evidence_backfill import eligible_candidates_query

_DEFAULT_TEST_POSTGRES_URL = (
    "postgresql+asyncpg://postgres:test@127.0.0.1:55432/test_db"
)
_POSTGRES_URL = os.environ.get("MAPS_TEST_POSTGRES_URL", _DEFAULT_TEST_POSTGRES_URL)


@pytest.fixture
async def postgres_db():
    engine = create_async_engine(_POSTGRES_URL)
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql("SELECT 1")
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f"No reachable PostgreSQL test server at {_POSTGRES_URL}: {exc}")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session

    # Base.metadata.drop_all can't topologically sort an unrelated
    # circular FK (chats <-> turns) elsewhere in the shared metadata —
    # irrelevant to this test. This is a throwaway database create for
    # this test alone, so just drop and recreate the whole schema.
    async with engine.begin() as conn:
        await conn.exec_driver_sql("DROP SCHEMA public CASCADE")
        await conn.exec_driver_sql("CREATE SCHEMA public")
    await engine.dispose()


async def _create_run(db: AsyncSession) -> MapsCensusRun:
    org = Organization(name="Test Org", slug=f"test-org-{os.urandom(4).hex()}")
    user = User(email=f"{os.urandom(4).hex()}@example.com", hashed_password="x", full_name="Test")
    db.add_all([org, user])
    await db.flush()
    run = MapsCensusRun(
        organization_id=org.id,
        created_by=user.id,
        country_code="FI",
        country_name="Finland",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run


def _place(run_id: str, google_place_id: str, **overrides) -> MapsPlace:
    base = dict(
        run_id=run_id,
        google_place_id=google_place_id,
        raw_name=google_place_id,
        canonical_name=google_place_id,
        is_relevant=True,
    )
    base.update(overrides)
    return MapsPlace(**base)


@pytest.mark.asyncio
async def test_eligible_candidates_query_executes_without_syntax_error_on_postgres(postgres_db):
    """The actual regression: this must not raise
    asyncpg.exceptions.PostgresSyntaxError. Compiling the query to a SQL
    string is not enough — the bug only manifests when PostgreSQL itself
    parses and plans it."""
    db = postgres_db
    run = await _create_run(db)

    kept = _place(run.id, "kept-1", keep_drop_decision="keep")
    reviewed = _place(run.id, "reviewed-1", keep_drop_decision="review")
    dropped = _place(run.id, "dropped-1", keep_drop_decision="drop", is_relevant=False)
    never_decided = _place(run.id, "never-decided-1", keep_drop_decision=None)
    db.add_all([kept, reviewed, dropped, never_decided])
    await db.commit()

    # This is the line that raised PostgresSyntaxError before the fix.
    result = await db.execute(eligible_candidates_query(run.id))
    rows = result.scalars().all()

    ids = {row.google_place_id for row in rows}
    assert ids == {"kept-1", "reviewed-1"}
    assert "dropped-1" not in ids
    # A place that was never decided has keep_drop_decision=None and no
    # keep_drop_decision in {KEEP, REVIEW} either — it correctly does NOT
    # appear (it's never a backfill candidate until keep/drop judges it),
    # but critically its presence must not itself cause the NULL-vs-"drop"
    # comparison to raise — that is exactly what IS DISTINCT FROM gets
    # right and a naive IS NOT / != would not.
    assert "never-decided-1" not in ids


@pytest.mark.asyncio
async def test_eligible_candidates_query_excludes_manual_exclusion_on_postgres(postgres_db):
    db = postgres_db
    run = await _create_run(db)

    from datetime import UTC, datetime

    excluded = _place(
        run.id,
        "excluded-1",
        keep_drop_decision="keep",
        manually_excluded_at=datetime.now(UTC),
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
    )
    kept = _place(run.id, "kept-2", keep_drop_decision="keep")
    db.add_all([excluded, kept])
    await db.commit()

    result = await db.execute(eligible_candidates_query(run.id))
    ids = {row.google_place_id for row in result.scalars().all()}
    assert ids == {"kept-2"}
