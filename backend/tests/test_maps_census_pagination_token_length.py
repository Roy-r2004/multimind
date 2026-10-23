"""Regression tests for the Google Places pagination-token overflow fix.

Root cause (confirmed against a real, disposable PostgreSQL instance in the
implementing session, not just these mocks): ``maps_census_cells
.pagination_resume_token`` was ``VARCHAR(2048)``. Google's real
``nextPageToken`` has no documented length cap and has been observed well
over 2048 characters in dense result sets (Alabama, Michigan, Colorado,
Florida). Saving one raised a Postgres ``DataError`` on commit, which
rolled back the *whole* transaction — including that page's already-flushed
newly-discovered places — and, via the normal retry/terminal-failure path,
eventually marked the cell FAILED even though Google Places had returned
valid results.

IMPORTANT CAVEAT, demonstrated here deliberately rather than glossed over:
SQLite (this repo's test/dev database) does not enforce VARCHAR(n) length at
all, regardless of the declared type — a mocked/SQLite test asserting a
10,000-character token round-trips would have passed identically against
the *old*, buggy ``VARCHAR(2048)`` column. These tests prove the
application and ORM layers never truncate or otherwise restrict the token
(there was never a second, Python-side limit beyond the database column —
confirmed by inspecting ``PlacesSearchOutcome``, the Pydantic API schemas,
and every call site). They do NOT by themselves prove the production
Postgres restriction is gone — that was verified separately, for real,
against a disposable Postgres 16 container applying the actual migration
chain (see the session report): the pre-fix column genuinely raised
``StringDataRightTruncationError`` for a 2049-character token with the old
schema, and the post-fix (TEXT) column accepted 2049- and 10,000-character
tokens with an exact round-trip, in the same before/after reproduction.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models import MapsCensusCell, MapsCensusCellStatus
from app.services.scraping.maps_census_service import maps_census_service
from app.services.scraping.maps_places_client import PlacesSearchOutcome
from app.services.scraping.maps_quota_tracker import MapsQuotaTracker

from tests.test_maps_census_service import _FakePlacesClient, _create_run

SHORT_TOKEN = "CpQBmgNf4oWqs...short-example-token"
TOKEN_2049_CHARS = "a" * 2049
TOKEN_10000_CHARS = "b" * 10000


def _session_factory_for(db_session: AsyncSession):
    bind = db_session.bind if db_session.bind is not None else db_session.get_bind()
    return async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)


class _TokenReturningClient(_FakePlacesClient):
    """Returns a configured outcome (places + resume token) for one query,
    and records the exact ``resume_page_token`` it was called with, so a
    test can assert what the *next* attempt actually resumed from."""

    def __init__(self, outcomes_by_query: dict[str, PlacesSearchOutcome]) -> None:
        super().__init__({})
        self._outcomes_by_query = outcomes_by_query
        self.calls_with_resume_token: list[tuple[str, str | None]] = []

    async def search_text_paginated(self, *, query, resume_page_token=None, **kwargs):
        self.calls_with_resume_token.append((query, resume_page_token))
        outcome = self._outcomes_by_query.get(query)
        if outcome is not None:
            if kwargs.get("heartbeat_cb") is not None:
                await kwargs["heartbeat_cb"]()
            return outcome
        return await super().search_text_paginated(
            query=query, resume_page_token=resume_page_token, **kwargs
        )


async def _run_one_cell(
    db,
    run_id: str,
    cell: MapsCensusCell,
    client,
) -> None:
    session_factory = _session_factory_for(db)
    summary: dict = {"cells": 0, "places_found": 0, "cell_failures": 0}
    await maps_census_service._execute_cells(
        session_factory,
        cell_ids=[cell.id],
        run_id=run_id,
        country_code="US",
        client=client,
        settings=get_settings(),
        summary=summary,
        classification_provider=None,
        classification_model_slug="",
        country_name="United States",
        tracker=MapsQuotaTracker(),
        campaign_budget={"used": 0, "max": 1500},
        seen_query_texts=set(),
        execution_token=None,
    )


@pytest.mark.parametrize(
    "token",
    [SHORT_TOKEN, TOKEN_2049_CHARS, TOKEN_10000_CHARS],
    ids=["short_token", "token_over_2048_chars", "token_10000_chars"],
)
@pytest.mark.asyncio
async def test_pagination_token_of_any_length_saves_without_error_or_cell_failure(
    db, auth, monkeypatch, token: str
):
    """A page that leaves more results available must never fail to persist
    regardless of how long Google's token is — and must never mark the cell
    FAILED when Places actually returned valid results."""
    run = await _create_run(db, auth)
    cell = MapsCensusCell(
        run_id=run.id,
        region_name="Michigan",
        city_name="Detroit",
        query_text="rehab Detroit",
        status=MapsCensusCellStatus.PENDING,
    )
    db.add(cell)
    await db.commit()

    outcome = PlacesSearchOutcome(
        places=[],
        pages_fetched=1,
        raw_results_found=20,
        unique_results_found=20,
        duplicates_found=0,
        next_page_available=True,
        result_cap_reached=False,
        resume_page_token=token,
    )
    client = _TokenReturningClient({"rehab Detroit": outcome})
    monkeypatch.setattr(get_settings(), "maps_census_max_subdivision_depth", 0)

    await _run_one_cell(db, run.id, cell, client)

    await db.refresh(cell)
    assert cell.status != MapsCensusCellStatus.FAILED
    assert cell.pagination_resume_token == token
    assert len(cell.pagination_resume_token) == len(token)


@pytest.mark.asyncio
async def test_nullable_token_stays_null(db, auth, monkeypatch):
    """A cell whose pagination is fully exhausted (no further pages) must
    still save a NULL token cleanly — nullability is unaffected by widening
    the column to TEXT."""
    run = await _create_run(db, auth)
    cell = MapsCensusCell(
        run_id=run.id,
        region_name="Michigan",
        city_name="Lansing",
        query_text="rehab Lansing",
        status=MapsCensusCellStatus.PENDING,
    )
    db.add(cell)
    await db.commit()

    outcome = PlacesSearchOutcome(
        places=[],
        pages_fetched=1,
        raw_results_found=3,
        unique_results_found=3,
        duplicates_found=0,
        next_page_available=False,
        result_cap_reached=False,
        resume_page_token=None,
    )
    client = _TokenReturningClient({"rehab Lansing": outcome})

    await _run_one_cell(db, run.id, cell, client)

    await db.refresh(cell)
    assert cell.pagination_resume_token is None
    assert cell.status == MapsCensusCellStatus.COMPLETED


@pytest.mark.asyncio
async def test_saved_long_token_is_reused_exactly_on_the_next_attempt(db, auth, monkeypatch):
    """The actual resumption workflow: a cell already holds a long token
    from an earlier, interrupted attempt (reloaded from the database, not
    held in memory) — the next time this cell is processed, the provider
    must receive that exact token as resume_page_token, byte for byte."""
    run = await _create_run(db, auth)
    cell = MapsCensusCell(
        run_id=run.id,
        region_name="Colorado",
        city_name="Denver",
        query_text="rehab Denver",
        status=MapsCensusCellStatus.PENDING,
        pagination_resume_token=TOKEN_10000_CHARS,
    )
    db.add(cell)
    await db.commit()
    cell_id = cell.id

    final_outcome = PlacesSearchOutcome(
        places=[],
        pages_fetched=1,
        raw_results_found=5,
        unique_results_found=5,
        duplicates_found=0,
        next_page_available=False,
        result_cap_reached=False,
        resume_page_token=None,
    )
    client = _TokenReturningClient({"rehab Denver": final_outcome})

    session_factory = _session_factory_for(db)
    async with session_factory() as fresh_db:
        reloaded_cell = await fresh_db.get(MapsCensusCell, cell_id)
        assert reloaded_cell is not None

    await _run_one_cell(db, run.id, reloaded_cell, client)

    assert client.calls_with_resume_token == [("rehab Denver", TOKEN_10000_CHARS)]

    await db.refresh(cell)
    assert cell.status == MapsCensusCellStatus.COMPLETED
    assert cell.pagination_resume_token is None  # pagination genuinely finished this time


def test_migration_revision_identity_and_parent():
    """Static identity check — the ALTER COLUMN ... TYPE TEXT operation this
    migration performs is PostgreSQL-specific syntax (confirmed: running it
    against plain SQLite raises a syntax error, not a behavioral
    difference), so unlike the purely-additive add_column migrations
    elsewhere in this test suite, its upgrade()/downgrade() cannot be
    exercised against an in-memory SQLite connection. The real schema change
    was verified end to end against a disposable PostgreSQL 16 container in
    the implementing session (full base-to-head apply, live
    StringDataRightTruncationError reproduced pre-fix, live exact-round-trip
    of a 2049- and a 10000-character token post-fix) — this test only pins
    the revision graph wiring, which `alembic heads`/`history` also confirm.
    """
    import importlib.util
    from pathlib import Path

    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    path = next(versions_dir.glob("gosom061_*.py"))
    spec = importlib.util.spec_from_file_location("gosom061_migration", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module.revision == "gosom061_pagination_token_text"
    assert module.down_revision == "merge_main_060_gosom060"
