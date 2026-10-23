"""Regression tests for the Phase 1 -> Phase 2 transition race fixed in
``maps_census_service.run_census``:

- A watchdog-triggered duplicate ``run_census()`` execution must never let
  the superseded execution mark the campaign COMPLETED or enqueue Phase 2
  (enrichment) out from under the execution that actually owns the run.
- A campaign with cells still pending/in_progress (paused, cancelled
  mid-flight, or raced by a sibling execution) must never be marked COMPLETED
  by a batch finishing — that would both corrupt the completed/total counters
  and fire Phase 2 early.
- A parent cell that gets subdivided twice (the direct cause of the observed
  duplicate child-cell queries and flat place counts) must only subdivide
  once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.db.models import MapsCensusCell, MapsCensusCellStatus, MapsCensusRun, MapsCensusStatus
from app.services.scraping.maps_cell_runner import (
    RunExecutionSuperseded,
    ensure_run_execution_current as _real_ensure_run_execution_current,
)
from app.services.scraping.maps_census_service import (
    maps_census_service,
    recover_maps_census_runs,
)
from app.services.scraping.maps_grid_planner import MapsGridCell

from tests.test_maps_census_service import (
    _FakeGridPlanner,
    _FakePlacesClient,
    _create_run,
    _tracking_create_task,
)


@pytest.mark.asyncio
async def test_superseded_execution_never_completes_or_enqueues_phase2(db, auth, monkeypatch):
    """Simulates a watchdog-triggered duplicate taking over mid-discovery:
    the in-flight execution's next fencing check must stop it cold, before
    keep/drop, website validation, or the COMPLETED commit that would
    otherwise enqueue Phase 2.
    """
    run = await _create_run(db, auth)
    seed = [
        MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="rehab Minsk"),
    ]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _FakeGridPlanner(seed),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient({}),
    )

    enqueue_calls: list[str] = []

    async def fake_enqueue(self, run_id: str) -> None:
        enqueue_calls.append(run_id)

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._maybe_enqueue_enrichment",
        fake_enqueue,
    )

    # The per-cell heartbeat check (inside _execute_cells) passes once, then a
    # second execution "takes over" before the post-discovery stages run.
    calls = {"n": 0}

    async def flaky_ensure(session_factory, *, run_id, token):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RunExecutionSuperseded(run_id)
        return await _real_ensure_run_execution_current(
            session_factory, run_id=run_id, token=token
        )

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.ensure_run_execution_current",
        flaky_ensure,
    )

    with pytest.raises(RunExecutionSuperseded):
        await maps_census_service.run_census(db, run_id=run.id)

    await db.refresh(run)
    assert run.status != MapsCensusStatus.COMPLETED
    assert enqueue_calls == []


@pytest.mark.asyncio
async def test_paused_campaign_with_pending_cells_is_not_marked_completed(db, auth, monkeypatch):
    """A campaign paused mid-flight leaves cells PENDING. That must never be
    read as "discovery finished" — a completed batch (zero, in this case)
    must never trigger the COMPLETED transition or Phase 2 while real work
    remains queued.
    """
    run = await _create_run(db, auth)
    run.processing_state = {"campaign_paused": True}
    await db.commit()

    seed = [
        MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="rehab Minsk"),
        MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="rehab Minsk 2"),
    ]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _FakeGridPlanner(seed),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FakePlacesClient({}),
    )

    enqueue_calls: list[str] = []

    async def fake_enqueue(self, run_id: str) -> None:
        enqueue_calls.append(run_id)

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._maybe_enqueue_enrichment",
        fake_enqueue,
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    await db.refresh(run)
    assert run.status != MapsCensusStatus.COMPLETED
    assert enqueue_calls == []

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    assert len(cells) == 2
    assert all(c.status.value == "pending" for c in cells)


@pytest.mark.asyncio
async def test_already_subdivided_parent_cell_is_not_subdivided_twice(db, auth, monkeypatch):
    """Guards against the exact defect found in production data: the same
    capped parent cell subdivided twice (by a cell claimed/processed more
    than once, e.g. by a since-superseded concurrent execution) persisted
    duplicate child queries that only rediscovered places already in the
    run, with zero new unique places.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.db.models import MapsCensusCellStatus
    from app.services.scraping.maps_quota_tracker import MapsQuotaTracker

    run = await _create_run(db, auth)
    # PENDING, not IN_PROGRESS: this models a cell reset back to PENDING for
    # reprocessing (e.g. by recover_stale_running_cells after a crash) that
    # already has one child left over from its first attempt — exactly the
    # state the atomic claim_cell_by_id requires to claim it at all.
    parent = MapsCensusCell(
        run_id=run.id,
        region_name="Minsk Region",
        city_name="Minsk",
        query_text="rehab Minsk",
        status=MapsCensusCellStatus.PENDING,
    )
    db.add(parent)
    await db.flush()
    existing_child = MapsCensusCell(
        run_id=run.id,
        region_name="Minsk Region",
        city_name="Minsk",
        query_text="pre-existing child query",
        parent_cell_id=parent.id,
        status=MapsCensusCellStatus.COMPLETED,
    )
    db.add(existing_child)
    await db.commit()

    class _CappedPlacesClient(_FakePlacesClient):
        async def search_text_paginated(self, **kwargs):
            outcome = await super().search_text_paginated(**kwargs)
            outcome.result_cap_reached = True
            return outcome

    def _session_factory_for(db_session):
        bind = db_session.bind if db_session.bind is not None else db_session.get_bind()
        return async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)

    session_factory = _session_factory_for(db)
    summary: dict = {"cells": 0, "places_found": 0, "cell_failures": 0}
    await maps_census_service._execute_cells(
        session_factory,
        cell_ids=[parent.id],
        run_id=run.id,
        country_code=run.country_code,
        client=_CappedPlacesClient({}),
        settings=get_settings(),
        summary=summary,
        classification_provider=None,
        classification_model_slug="",
        country_name=run.country_name,
        tracker=MapsQuotaTracker(),
        campaign_budget={"used": 100, "max": 1500},
        seen_query_texts=set(),
        execution_token=None,
    )

    children = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.parent_cell_id == parent.id))
    ).scalars().all()
    # Only the pre-existing child — the guard must have skipped re-subdividing.
    assert len(children) == 1
    assert children[0].id == existing_child.id

    await db.refresh(parent)
    assert parent.status == MapsCensusCellStatus.COMPLETED


def _session_factory_for(db_session: AsyncSession):
    bind = db_session.bind if db_session.bind is not None else db_session.get_bind()
    return async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)


async def _create_running_run(db, auth: AuthContext, *, heartbeat_age: timedelta) -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="US",
        country_name="United States",
        status=MapsCensusStatus.RUNNING,
        heartbeat_at=datetime.now(UTC) - heartbeat_age,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run


@pytest.mark.asyncio
async def test_watchdog_never_duplicates_a_run_with_a_fresh_cell_heartbeat(db, auth, monkeypatch):
    """Models exactly the reported failure mode: a single Gosom scrape runs
    long enough that the coarse run-level heartbeat looks stale (>10 min),
    but the cell actually being worked has a heartbeat refreshed seconds ago
    (via heartbeat_cb during the poll loop). The watchdog must treat this
    run as alive, not duplicate it.
    """
    run = await _create_running_run(db, auth, heartbeat_age=timedelta(minutes=20))
    in_progress_cell = MapsCensusCell(
        run_id=run.id,
        region_name="Alabama",
        city_name="Birmingham",
        query_text="rehab Birmingham",
        status=MapsCensusCellStatus.IN_PROGRESS,
        heartbeat_at=datetime.now(UTC) - timedelta(seconds=20),
    )
    db.add(in_progress_cell)
    await db.commit()

    session_factory = _session_factory_for(db)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", session_factory)

    spawned: list[str] = []

    async def fake_run_maps_census_job(ctx, run_id):
        spawned.append(run_id)

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.run_maps_census_job",
        fake_run_maps_census_job,
    )

    await recover_maps_census_runs({})

    assert spawned == []


@pytest.mark.asyncio
async def test_watchdog_recovers_a_genuinely_crashed_run(db, auth, monkeypatch):
    """The flip side: if the run heartbeat AND every in-progress cell's own
    heartbeat are stale, there is no evidence anything is alive — this is
    a genuine crash, and the watchdog must still requeue it (crash recovery
    must keep working, not just become permanently cautious)."""
    run = await _create_running_run(db, auth, heartbeat_age=timedelta(minutes=20))
    stuck_cell = MapsCensusCell(
        run_id=run.id,
        region_name="Alabama",
        city_name="Birmingham",
        query_text="rehab Birmingham",
        status=MapsCensusCellStatus.IN_PROGRESS,
        heartbeat_at=datetime.now(UTC) - timedelta(minutes=25),
    )
    db.add(stuck_cell)
    await db.commit()

    session_factory = _session_factory_for(db)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", session_factory)

    spawned: list[str] = []

    async def fake_run_maps_census_job(ctx, run_id):
        spawned.append(run_id)

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.run_maps_census_job",
        fake_run_maps_census_job,
    )
    captured_tasks = _tracking_create_task(monkeypatch, marker="fake_run_maps_census_job")

    await recover_maps_census_runs({})
    import asyncio

    await asyncio.gather(*captured_tasks)

    assert spawned == [run.id]
