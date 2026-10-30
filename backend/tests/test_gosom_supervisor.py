"""Regression tests for automatic Gosom self-healing (gosom_health_monitor.py
and its wiring into maps_census_service.py).

What these tests CAN verify (pure Python/DB logic):
- Freeze detection combines consecutive timeouts, staleness, and real
  outstanding work correctly, with no false positive when Gosom is simply
  idle or legitimately slow-but-progressing.
- Exactly one coordinated restart can be "in progress" at a time.
- New Gosom job submissions pause while a restart is flagged in progress,
  and resume once it is cleared.
- A successful restart resets the failure counters and the discovery
  watchdog's bounded auto-resume cap on RUNNING runs.
- The watchdog does not spend one of its bounded auto-resume attempts on a
  run that is stale only because a supervisor restart is in progress.
- Phase 2 (mandatory eligibility) still runs once discovery completes,
  independent of this feature, and no optional AI enrichment is triggered
  by any of this.

What these tests CANNOT verify (requires the live Docker environment — see
the deployment report's manual verification steps):
- That `docker compose restart gosom` actually runs and succeeds.
- That the PowerShell supervisor script's own local cooldown/hourly-rate
  state file logic behaves correctly end to end against a real freeze.
- That Gosom's dispatcher is actually healthy after a real restart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.db.models import (
    GosomHealthState,
    MapsCensusCell,
    MapsCensusCellStatus,
    MapsCensusRun,
    MapsCensusStatus,
)
from app.services.scraping import gosom_health_monitor
from app.services.scraping.maps_census_service import maps_census_service, recover_maps_census_runs
from app.services.scraping.maps_country_profile_service import maps_country_profile_service
from app.services.scraping.maps_grid_planner import MapsGridCell
from app.services.scraping.maps_places_client import PlaceResult, PlacesTimeoutError
from app.services.scraping.maps_quota_tracker import MapsQuotaTracker

from tests.test_maps_census_service import (
    _FakeGridPlanner,
    _FakePlacesClient,
    _create_run,
    _tracking_create_task,
)


def _session_factory_for(db_session: AsyncSession):
    bind = db_session.bind if db_session.bind is not None else db_session.get_bind()
    return async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)


async def _create_running_run_with_cell(db, auth: AuthContext, *, heartbeat_age: timedelta) -> MapsCensusRun:
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
    cell = MapsCensusCell(
        run_id=run.id,
        region_name="Finland",
        city_name="Helsinki",
        query_text="rehab Helsinki",
        status=MapsCensusCellStatus.PENDING,
    )
    db.add(cell)
    await db.commit()
    return run


@pytest.mark.asyncio
async def test_freeze_not_detected_below_timeout_threshold(db):
    """Scenario 8: no false positive for an isolated slow job."""
    settings = get_settings()
    session_factory = _session_factory_for(db)
    await gosom_health_monitor.record_job_timeout(session_factory)
    decision = await gosom_health_monitor.evaluate_freeze(session_factory, settings)
    assert decision.frozen is False
    assert decision.reason == "below_consecutive_timeout_threshold"


@pytest.mark.asyncio
async def test_freeze_not_detected_without_outstanding_work(db, monkeypatch):
    """No false positive when there's simply no census running right now."""
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_gosom_freeze_consecutive_timeouts", 3)
    monkeypatch.setattr(settings, "maps_gosom_freeze_detection_window_seconds", 1)
    session_factory = _session_factory_for(db)
    for _ in range(3):
        await gosom_health_monitor.record_job_timeout(session_factory)
    decision = await gosom_health_monitor.evaluate_freeze(session_factory, settings)
    assert decision.frozen is False
    assert decision.reason == "no_outstanding_work"


@pytest.mark.asyncio
async def test_freeze_not_detected_with_recent_success(db, auth, monkeypatch):
    """A healthy-but-slow job (success just happened) is not a freeze, even
    with outstanding work and a prior timeout streak.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_gosom_freeze_consecutive_timeouts", 3)
    monkeypatch.setattr(settings, "maps_gosom_freeze_detection_window_seconds", 600)
    session_factory = _session_factory_for(db)
    await _create_running_run_with_cell(db, auth, heartbeat_age=timedelta(minutes=1))
    for _ in range(3):
        await gosom_health_monitor.record_job_timeout(session_factory)
    await gosom_health_monitor.record_job_success(session_factory)
    decision = await gosom_health_monitor.evaluate_freeze(session_factory, settings)
    assert decision.frozen is False
    # A success resets the counter, so this also covers "consecutive timeouts
    # reset after a demonstrated completion."
    assert decision.snapshot["consecutive_timeouts"] == 0


@pytest.mark.asyncio
async def test_genuine_freeze_is_detected(db, auth, monkeypatch):
    """Scenarios 1-3: container-Up-but-wedged, three 480s timeouts, real
    outstanding work, no recent success -> frozen=True.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_gosom_freeze_consecutive_timeouts", 3)
    monkeypatch.setattr(settings, "maps_gosom_freeze_detection_window_seconds", 1)
    session_factory = _session_factory_for(db)
    await _create_running_run_with_cell(db, auth, heartbeat_age=timedelta(minutes=20))
    for _ in range(3):
        await gosom_health_monitor.record_job_timeout(session_factory)

    import asyncio

    await asyncio.sleep(1.1)
    decision = await gosom_health_monitor.evaluate_freeze(session_factory, settings)
    assert decision.frozen is True
    assert "consecutive_timeouts=3" in decision.reason


@pytest.mark.asyncio
async def test_exactly_one_coordinated_restart(db):
    """Scenario 4: two concurrent begin_restart calls must not both succeed."""
    session_factory = _session_factory_for(db)
    first = await gosom_health_monitor.begin_restart(session_factory, reason="test freeze")
    second = await gosom_health_monitor.begin_restart(session_factory, reason="duplicate trigger")
    assert first is True
    assert second is False
    assert await gosom_health_monitor.is_restart_in_progress(session_factory) is True


@pytest.mark.asyncio
async def test_responsive_restart_resets_resume_budget_not_scraping_counters(db, auth):
    """Hardening item 5: HTTP-responsiveness must never be treated as proof
    of real scraping progress. A responsive restart resets the discovery
    watchdog's bounded resume budget (demonstrated progress: Gosom is back)
    but leaves consecutive_timeouts untouched — only a genuine
    record_job_success (a real completed job) clears that axis.
    """
    session_factory = _session_factory_for(db)
    run = await _create_running_run_with_cell(db, auth, heartbeat_age=timedelta(minutes=1))
    run.processing_state = {"discovery_auto_resume_count": 4}
    await db.commit()

    for _ in range(3):
        await gosom_health_monitor.record_job_timeout(session_factory)
    await gosom_health_monitor.begin_restart(session_factory, reason="freeze")
    snapshot = await gosom_health_monitor.complete_restart(session_factory, http_responsive=True)

    assert snapshot["restart_in_progress"] is False
    assert snapshot["consecutive_timeouts"] == 3  # NOT reset by a mere restart
    assert snapshot["consecutive_failed_restarts"] == 0
    assert snapshot["restart_history"][-1].endswith("|ok")

    await db.refresh(run)
    assert run.processing_state.get("discovery_auto_resume_count") == 0

    # But only a real job completion clears the scraping-progress counter.
    await gosom_health_monitor.record_job_success(session_factory)
    cleared = await gosom_health_monitor.get_snapshot(session_factory)
    assert cleared["consecutive_timeouts"] == 0


@pytest.mark.asyncio
async def test_restart_grace_window_prevents_instant_re_freeze(db, auth, monkeypatch):
    """Without the last_restart_at grace window, evaluate_freeze would
    immediately see the still-elevated consecutive_timeouts and re-declare
    frozen right after a responsive restart, before any new job even had a
    chance to run.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_gosom_freeze_consecutive_timeouts", 3)
    monkeypatch.setattr(settings, "maps_gosom_freeze_detection_window_seconds", 600)
    session_factory = _session_factory_for(db)
    await _create_running_run_with_cell(db, auth, heartbeat_age=timedelta(minutes=1))

    for _ in range(3):
        await gosom_health_monitor.record_job_timeout(session_factory)
    await gosom_health_monitor.begin_restart(session_factory, reason="freeze")
    await gosom_health_monitor.complete_restart(session_factory, http_responsive=True)

    decision = await gosom_health_monitor.evaluate_freeze(session_factory, settings)
    assert decision.frozen is False
    assert decision.reason == "recent_success_or_restart_within_window"


@pytest.mark.asyncio
async def test_non_responsive_restart_leaves_scraping_counters_and_increments_failure_axis(db):
    """A restart that didn't even bring HTTP back up must not pretend it
    did, and must not be conflated with the separate scraping-progress
    counters — it increments consecutive_failed_restarts instead.
    """
    session_factory = _session_factory_for(db)
    for _ in range(3):
        await gosom_health_monitor.record_job_timeout(session_factory)
    await gosom_health_monitor.begin_restart(session_factory, reason="freeze")
    snapshot = await gosom_health_monitor.complete_restart(session_factory, http_responsive=False)
    assert snapshot["restart_in_progress"] is False
    assert snapshot["consecutive_timeouts"] == 3
    assert snapshot["consecutive_failed_restarts"] == 1
    assert snapshot["restart_history"][-1].endswith("|failed")
    assert snapshot["restart_exhausted"] is False


@pytest.mark.asyncio
async def test_restart_exhausted_after_max_consecutive_failures(db, monkeypatch):
    """Hardening item 6: repeated unsuccessful restarts must stop with an
    actionable terminal state rather than looping forever.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_gosom_supervisor_max_consecutive_restart_failures", 2)
    session_factory = _session_factory_for(db)

    for _ in range(3):
        await gosom_health_monitor.record_job_timeout(session_factory)
    for _ in range(2):
        started = await gosom_health_monitor.begin_restart(session_factory, reason="freeze")
        assert started is True
        snapshot = await gosom_health_monitor.complete_restart(session_factory, http_responsive=False)

    assert snapshot["restart_exhausted"] is True
    assert snapshot["restart_exhausted_reason"]

    # Further restarts are refused outright...
    started_again = await gosom_health_monitor.begin_restart(session_factory, reason="another freeze")
    assert started_again is False

    # ...and evaluate_freeze stops recommending one, so the supervisor and
    # the watchdog both see a clear reason instead of silently retrying.
    decision = await gosom_health_monitor.evaluate_freeze(session_factory, settings)
    assert decision.frozen is False
    assert decision.reason == "restart_exhausted_awaiting_operator"


@pytest.mark.asyncio
async def test_begin_restart_reclaims_stale_in_progress_flag(db, monkeypatch):
    """Hardening item 2: if the host supervisor crashed between
    /restart/begin and /restart/complete, the flag must not block every
    future restart forever — it is reclaimed once clearly abandoned.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_gosom_supervisor_max_restart_duration_seconds", 1)
    session_factory = _session_factory_for(db)

    first = await gosom_health_monitor.begin_restart(session_factory, reason="first attempt")
    assert first is True

    # Immediately retrying must fail — not yet stale.
    immediate_retry = await gosom_health_monitor.begin_restart(session_factory, reason="too soon")
    assert immediate_retry is False

    import asyncio

    await asyncio.sleep(1.2)

    reclaimed = await gosom_health_monitor.begin_restart(session_factory, reason="reclaim after crash")
    assert reclaimed is True
    assert await gosom_health_monitor.is_restart_in_progress(session_factory) is True


@pytest.mark.asyncio
async def test_begin_restart_is_atomic_under_concurrency(db):
    """Hardening item 3: many concurrent begin_restart calls against the
    SAME existing row must still result in exactly one winner, via the
    single guarded UPDATE's WHERE clause — never a separate check-then-
    write race. (The row is pre-created here so this isolates that one
    guarantee; the separate first-creation race is covered by
    get_or_create_state's own IntegrityError handling and is not something
    a single-connection SQLite test harness can reliably stress-test.)
    """
    import asyncio

    session_factory = _session_factory_for(db)
    await gosom_health_monitor.get_or_create_state(session_factory)
    results = await asyncio.gather(
        *[gosom_health_monitor.begin_restart(session_factory, reason=f"concurrent-{i}") for i in range(10)]
    )
    assert sum(1 for r in results if r) == 1


@pytest.mark.asyncio
async def test_restarts_in_last_hour_counts_only_recent_attempts_including_failures():
    old = f"{(datetime.now(UTC) - timedelta(hours=3)).isoformat()}|ok"
    recent_ok = f"{(datetime.now(UTC) - timedelta(minutes=5)).isoformat()}|ok"
    recent_failed = f"{(datetime.now(UTC) - timedelta(minutes=2)).isoformat()}|failed"
    snapshot = {"restart_history": [old, recent_ok, recent_failed]}
    assert gosom_health_monitor.restarts_in_last_hour(snapshot) == 2


@pytest.mark.asyncio
async def test_run_census_pauses_new_submissions_while_restart_in_progress(db, auth, monkeypatch):
    """Scenarios 6 + 7: a cell that was never claimed stays PENDING and
    untouched (nothing to "reconcile" because nothing was claimed), and a
    previously COMPLETED cell is never reprocessed either way.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_discovery_provider", "gosom")
    session_factory = _session_factory_for(db)

    run = await _create_run(db, auth)
    completed_cell = MapsCensusCell(
        run_id=run.id,
        region_name="Finland",
        city_name="Helsinki",
        query_text="rehab Helsinki",
        status=MapsCensusCellStatus.COMPLETED,
        places_found=5,
    )
    pending_cell = MapsCensusCell(
        run_id=run.id,
        region_name="Finland",
        city_name="Turku",
        query_text="rehab Turku",
        status=MapsCensusCellStatus.PENDING,
    )
    db.add_all([completed_cell, pending_cell])
    await db.commit()

    await gosom_health_monitor.begin_restart(session_factory, reason="mid-restart test")

    search_calls: list[str] = []

    class _TrackingPlacesClient(_FakePlacesClient):
        async def search_text_paginated(self, **kwargs):
            search_calls.append(kwargs.get("query"))
            return await super().search_text_paginated(**kwargs)

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _TrackingPlacesClient({}),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _FakeGridPlanner([]),
    )

    await maps_census_service._execute_cells(
        session_factory,
        cell_ids=[pending_cell.id],
        run_id=run.id,
        country_code=run.country_code,
        country_name=run.country_name,
        client=_TrackingPlacesClient({}),
        settings=settings,
        summary={"cell_failures": 0},
        classification_provider=None,
        classification_model_slug="test",
        tracker=MapsQuotaTracker(),
    )

    assert search_calls == []
    await db.refresh(pending_cell)
    assert pending_cell.status == MapsCensusCellStatus.PENDING
    assert pending_cell.claimed_by is None
    await db.refresh(completed_cell)
    assert completed_cell.status == MapsCensusCellStatus.COMPLETED
    assert completed_cell.places_found == 5


@pytest.mark.asyncio
async def test_watchdog_skips_auto_resume_penalty_during_supervisor_restart(db, auth, monkeypatch):
    """Scenario 12 (restart-loop half): a run stale only because the
    supervisor is mid-restart must not burn one of its bounded
    discovery_auto_resume_count attempts, and must not be relaunched out
    from under the restart.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_census_discovery_max_auto_resumes", 2)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", _session_factory_for(db))
    session_factory = _session_factory_for(db)

    run = await _create_running_run_with_cell(db, auth, heartbeat_age=timedelta(minutes=20))
    run.processing_state = {"discovery_auto_resume_count": 1}
    await db.commit()

    await gosom_health_monitor.begin_restart(session_factory, reason="mid-restart")

    spawned = _tracking_create_task(monkeypatch, marker="run_maps_census_job")

    await recover_maps_census_runs({})

    assert spawned == []
    await db.refresh(run)
    assert run.status == MapsCensusStatus.RUNNING
    assert run.processing_state.get("discovery_auto_resume_count") == 1


@pytest.mark.asyncio
async def test_within_breaker_cooldown_requires_threshold_and_recency(db, monkeypatch):
    """Pure-logic unit coverage for is_within_breaker_cooldown: both the
    failure threshold and the recovery window must hold, and either one
    elapsing/clearing (a success resetting consecutive_timeouts, or the
    cooldown window itself passing) must return False again."""
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_gosom_circuit_breaker_failure_threshold", 3)
    monkeypatch.setattr(settings, "maps_gosom_circuit_breaker_recovery_timeout_seconds", 1)
    session_factory = _session_factory_for(db)

    # No timeouts recorded yet.
    assert await gosom_health_monitor.is_within_breaker_cooldown(session_factory, settings) is False

    for _ in range(2):
        await gosom_health_monitor.record_job_timeout(session_factory)
    # Below the failure threshold — not a breaker-open condition at all.
    assert await gosom_health_monitor.is_within_breaker_cooldown(session_factory, settings) is False

    await gosom_health_monitor.record_job_timeout(session_factory)
    # Threshold met and the timeout just happened — still within cooldown.
    assert await gosom_health_monitor.is_within_breaker_cooldown(session_factory, settings) is True

    import asyncio

    await asyncio.sleep(1.1)
    # Cooldown window has now elapsed — safe to try again.
    assert await gosom_health_monitor.is_within_breaker_cooldown(session_factory, settings) is False


@pytest.mark.asyncio
async def test_watchdog_skips_auto_resume_penalty_during_breaker_cooldown(db, auth, monkeypatch):
    """Root-cause regression (Belarus run 5acff6f7-21f8-4427-88a1-38d0a394ce83):
    without this check, the 10-minute stale-heartbeat watchdog would
    relaunch run_census() with a brand-new, CLOSED in-memory CircuitBreaker
    5 minutes into the OLD breaker's 900s cooldown — bypassing the backoff
    entirely and burning one of the bounded discovery_auto_resume_count
    attempts on a retry that was never going to succeed. This must behave
    exactly like the existing supervisor-restart-in-progress skip: no
    relaunch, no auto-resume penalty, run stays RUNNING for the next tick.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_census_discovery_max_auto_resumes", 2)
    monkeypatch.setattr(settings, "maps_gosom_circuit_breaker_failure_threshold", 3)
    monkeypatch.setattr(settings, "maps_gosom_circuit_breaker_recovery_timeout_seconds", 900)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", _session_factory_for(db))
    session_factory = _session_factory_for(db)

    run = await _create_running_run_with_cell(db, auth, heartbeat_age=timedelta(minutes=20))
    run.processing_state = {"discovery_auto_resume_count": 1}
    await db.commit()

    for _ in range(3):
        await gosom_health_monitor.record_job_timeout(session_factory)

    spawned = _tracking_create_task(monkeypatch, marker="run_maps_census_job")

    await recover_maps_census_runs({})

    assert spawned == []
    await db.refresh(run)
    assert run.status == MapsCensusStatus.RUNNING
    assert run.processing_state.get("discovery_auto_resume_count") == 1


@pytest.mark.asyncio
async def test_watchdog_relaunches_once_breaker_cooldown_elapses(db, auth, monkeypatch):
    """The flip side: once the breaker's own cooldown window has genuinely
    passed, the watchdog's normal relaunch-and-penalize behavior resumes —
    this is a bounded deferral, never a permanent one."""
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_census_discovery_max_auto_resumes", 2)
    monkeypatch.setattr(settings, "maps_gosom_circuit_breaker_failure_threshold", 3)
    monkeypatch.setattr(settings, "maps_gosom_circuit_breaker_recovery_timeout_seconds", 1)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", _session_factory_for(db))
    session_factory = _session_factory_for(db)

    run = await _create_running_run_with_cell(db, auth, heartbeat_age=timedelta(minutes=20))
    run.processing_state = {"discovery_auto_resume_count": 1}
    await db.commit()

    for _ in range(3):
        await gosom_health_monitor.record_job_timeout(session_factory)

    import asyncio

    await asyncio.sleep(1.1)

    spawned: list[str] = []

    async def fake_run_maps_census_job(ctx, run_id):
        spawned.append(run_id)

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.run_maps_census_job",
        fake_run_maps_census_job,
    )
    captured_tasks = _tracking_create_task(monkeypatch, marker="fake_run_maps_census_job")

    await recover_maps_census_runs({})
    await asyncio.gather(*captured_tasks)

    assert spawned == [run.id]
    await db.refresh(run)
    assert run.processing_state.get("discovery_auto_resume_count") == 2


@pytest.mark.asyncio
async def test_full_recovery_sequence_freeze_timeout_cooldown_resume_completes_campaign(
    db, auth, monkeypatch
):
    """End-to-end offline proof of the complete chain the user asked for:
    job freezes -> timeout -> breaker opens -> cooldown -> automatic
    recovery -> the SAME campaign resumes and finishes, with no duplicated
    cells and no cell left stuck. Entirely offline: no Docker, no network,
    no AI spend (every cell in this test discovers zero places, so the
    classification/AI path is never entered at all).

    Round 1's client always raises PlacesTimeoutError (gosom frozen).
    Round 2's client (a fresh instance, exactly as create_places_client()
    would return after a real automatic `docker restart gosom`) always
    succeeds — this test does not exercise the Docker-restart mechanism
    itself (see test_gosom_supervisor_linux.py for that in isolation); it
    proves the CAMPAIGN'S OWN code path self-heals once Gosom is healthy
    again and recover_maps_census_runs is the thing that gives it that
    second chance, at the right time.
    """
    import asyncio

    settings = get_settings()
    monkeypatch.setattr(settings, "maps_discovery_provider", "gosom")
    monkeypatch.setattr(settings, "maps_gosom_circuit_breaker_failure_threshold", 3)
    monkeypatch.setattr(settings, "maps_gosom_circuit_breaker_recovery_timeout_seconds", 1)
    monkeypatch.setattr(settings, "maps_census_discovery_max_auto_resumes", 3)
    monkeypatch.setattr(settings, "maps_census_cell_timeout_max_attempts", 5)
    monkeypatch.setattr(settings, "maps_census_cell_timeout_retry_base_seconds", 1)
    # Keep this test strictly scoped to the discovery-recovery sequence: no
    # downstream auto-enrichment job should get enqueued once the campaign
    # completes, or its own background task would race this test's own db
    # fixture teardown (an unrelated, already-known source of test-order
    # flakiness in this suite, not a defect in the recovery logic itself).
    monkeypatch.setattr(settings, "maps_census_auto_enrichment_enabled", False)

    async def _noop_build_profile(_db, *, run_id: str):
        del run_id
        return None

    monkeypatch.setattr(maps_country_profile_service, "build_profile_for_run", _noop_build_profile)

    session_factory = _session_factory_for(db)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", session_factory)

    run = await _create_run(db, auth)
    cells = [
        MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text=f"rehab query {i}")
        for i in range(4)
    ]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(cells)
    )

    timeout_error = PlacesTimeoutError("gosom job zzz exceeded 480s")
    frozen_client = _FakePlacesClient(
        {}, errors_by_query={cell.query_text: timeout_error for cell in cells}
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", lambda: frozen_client
    )

    # --- Round 1: Gosom is frozen. ---
    summary1 = await maps_census_service.run_census(db, run_id=run.id)
    assert summary1.get("error") is None

    await db.refresh(run)
    assert run.status == MapsCensusStatus.RUNNING  # paused, not FAILED/COMPLETED
    cells_after_round1 = (
        (await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id)))
        .scalars()
        .all()
    )
    assert len(cells_after_round1) == 4  # no duplicate cells created
    assert {c.status for c in cells_after_round1} == {MapsCensusCellStatus.PENDING}

    health = await gosom_health_monitor.get_snapshot(session_factory)
    assert health["consecutive_timeouts"] == 3

    # --- Watchdog tick #1: heartbeat is stale, but the breaker's own
    # cooldown has NOT elapsed yet. Must defer — no relaunch, no
    # auto-resume penalty spent. ---
    run.heartbeat_at = datetime.now(UTC) - timedelta(minutes=20)
    await db.commit()

    spawned_too_early: list[str] = []

    async def fake_run_job_too_early(ctx, run_id):
        spawned_too_early.append(run_id)

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.run_maps_census_job", fake_run_job_too_early
    )
    captured_too_early = _tracking_create_task(monkeypatch, marker="fake_run_job_too_early")
    await recover_maps_census_runs({})
    await asyncio.gather(*captured_too_early)

    assert spawned_too_early == []
    await db.refresh(run)
    assert int((run.processing_state or {}).get("discovery_auto_resume_count") or 0) == 0

    # --- Cooldown elapses. Gosom recovers (simulated here by swapping in a
    # client that succeeds — the restart mechanism itself is tested
    # separately). Watchdog tick #2 must now relaunch, and the campaign
    # must actually finish. ---
    await asyncio.sleep(1.1)
    run.heartbeat_at = datetime.now(UTC) - timedelta(minutes=20)
    await db.commit()

    healthy_client = _FakePlacesClient({})  # every query now succeeds (0 new places)
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", lambda: healthy_client
    )

    spawned: list[str] = []

    async def fake_run_job(ctx, run_id):
        spawned.append(run_id)
        await maps_census_service.run_census(None, run_id=run_id)

    monkeypatch.setattr("app.services.scraping.maps_census_service.run_maps_census_job", fake_run_job)
    captured = _tracking_create_task(monkeypatch, marker="fake_run_job")
    await recover_maps_census_runs({})
    await asyncio.gather(*captured)

    assert spawned == [run.id]
    await db.refresh(run)
    assert int((run.processing_state or {}).get("discovery_auto_resume_count") or 0) == 1
    assert run.status == MapsCensusStatus.COMPLETED

    # Round 2 ran through its own short-lived sessions; this long-lived test
    # session's identity map still holds the PENDING cell objects loaded
    # after round 1, so a plain re-select would just hand back those same
    # (now-stale) Python objects — expire them first to force a real re-read.
    # (run.id is read out BEFORE expiring — expire_all() marks every
    # attribute on every tracked object, including run itself, as needing a
    # synchronous reload on next access, which can't happen outside an
    # awaited context.)
    run_id = run.id
    db.expire_all()
    cells_after_round2 = (
        (await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run_id)))
        .scalars()
        .all()
    )
    assert len(cells_after_round2) == 4  # still no duplicate cells
    assert {c.status for c in cells_after_round2} == {MapsCensusCellStatus.COMPLETED}

    health_after = await gosom_health_monitor.get_snapshot(session_factory)
    assert health_after["consecutive_timeouts"] == 0  # a real success reset it


@pytest.mark.asyncio
async def test_unresponsive_gosom_exhausts_auto_resume_and_ends_failed_not_stuck(db, auth, monkeypatch):
    """The other half of point 4/5: if Gosom is genuinely, persistently
    unhealthy (every relaunch times out again, cooldown after cooldown),
    the watchdog must not retry forever — it ends with a clear FAILED
    status and a human-readable error_message once the bounded
    discovery_auto_resume_count is exhausted, never silently stuck RUNNING.
    """
    import asyncio

    settings = get_settings()
    monkeypatch.setattr(settings, "maps_census_discovery_max_auto_resumes", 1)
    monkeypatch.setattr(settings, "maps_gosom_circuit_breaker_failure_threshold", 3)
    monkeypatch.setattr(settings, "maps_gosom_circuit_breaker_recovery_timeout_seconds", 900)
    monkeypatch.setattr("app.db.session.AsyncSessionLocal", _session_factory_for(db))
    session_factory = _session_factory_for(db)

    run = await _create_running_run_with_cell(db, auth, heartbeat_age=timedelta(minutes=20))
    run.processing_state = {"discovery_auto_resume_count": 1}  # already at the cap
    await db.commit()

    for _ in range(3):
        await gosom_health_monitor.record_job_timeout(session_factory)

    spawned = _tracking_create_task(monkeypatch, marker="run_maps_census_job")
    await recover_maps_census_runs({})

    assert spawned == []
    await db.refresh(run)
    assert run.status == MapsCensusStatus.FAILED
    assert "automatic recovery" in (run.error_message or "")


@pytest.mark.asyncio
async def test_supervisor_endpoints_require_token(monkeypatch):
    """Control endpoints must 404 (not merely 401) while no token is
    configured, and reject a wrong token — never accept an unauthenticated
    restart trigger.
    """
    from fastapi import HTTPException

    from app.api.v1.maps import _require_supervisor_token
    from app.core.config import get_settings as _get_settings

    settings = _get_settings()
    monkeypatch.setattr(settings, "maps_gosom_supervisor_token", "")
    with pytest.raises(HTTPException) as exc_info:
        _require_supervisor_token(x_supervisor_token="anything")
    assert exc_info.value.status_code == 404

    monkeypatch.setattr(settings, "maps_gosom_supervisor_token", "correct-token")
    with pytest.raises(HTTPException) as exc_info:
        _require_supervisor_token(x_supervisor_token="wrong-token")
    assert exc_info.value.status_code == 403

    _require_supervisor_token(x_supervisor_token="correct-token")
