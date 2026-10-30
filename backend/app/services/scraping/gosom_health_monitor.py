"""Detects a frozen Gosom internal dispatcher — container Up, HTTP API still
answering, but no job is actually completing — and coordinates a bounded,
externally-executed restart with the rest of the Maps Census pipeline.

Split in two, deliberately:

1. Detection + coordination state lives here, persisted in the single
   ``gosom_health_state`` row (survives every ``run_census()`` relaunch,
   unlike the in-memory ``CircuitBreaker``, which starts CLOSED again each
   time). ``maps_census_service.py`` feeds it job outcomes and checks
   ``is_restart_in_progress`` before claiming a cell, the same way it
   already checks ``gosom_breaker.state``.

2. The actual ``docker ... restart gosom`` action is NOT performed by this
   process. The API/worker containers are not given Docker socket access
   (an unrestricted Docker socket mount is a container-escape risk this
   task explicitly forbids), so a host-side PowerShell supervisor
   (``backend/scripts/gosom_supervisor.ps1``) polls the ``/status`` control
   endpoint, performs the restart itself via the Docker CLI it already has
   on the host, and reports back via ``/restart/begin`` and
   ``/restart/complete``. This module only ever flips flags and counters —
   it never shells out to Docker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    GosomHealthState,
    MapsCensusCell,
    MapsCensusCellStatus,
    MapsCensusRun,
    MapsCensusStatus,
)

logger = logging.getLogger(__name__)

_RESTART_HISTORY_WINDOW = timedelta(hours=24)


@dataclass
class FreezeDecision:
    frozen: bool
    reason: str
    snapshot: dict[str, Any]


async def get_or_create_state(session_factory) -> GosomHealthState:
    """Get the singleton row, creating it on first use.

    ``GosomHealthState.id`` is a fixed value ("global"), not the usual
    random-UUID mixin default, precisely so two concurrent callers racing
    to create the row for the first time collide on the same primary key
    instead of each successfully inserting its own distinct "singleton" —
    the second insert raises ``IntegrityError``, caught here by simply
    re-reading the row the first caller just committed.
    """
    async with session_factory() as db:
        state = (await db.execute(select(GosomHealthState).limit(1))).scalars().first()
        if state is not None:
            return state
        state = GosomHealthState(id="global")
        db.add(state)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
        else:
            await db.refresh(state)
            return state

    async with session_factory() as db:
        state = (await db.execute(select(GosomHealthState).limit(1))).scalars().first()
        if state is None:
            raise RuntimeError("gosom_health_state row missing after a concurrent create race")
        return state


async def record_job_success(session_factory) -> None:
    async with session_factory() as db:
        state = (await db.execute(select(GosomHealthState).limit(1))).scalars().first()
        if state is None:
            state = GosomHealthState()
            db.add(state)
        state.consecutive_timeouts = 0
        state.consecutive_successes = (state.consecutive_successes or 0) + 1
        state.last_success_at = datetime.now(UTC)
        await db.commit()


async def record_job_timeout(session_factory) -> None:
    async with session_factory() as db:
        state = (await db.execute(select(GosomHealthState).limit(1))).scalars().first()
        if state is None:
            state = GosomHealthState()
            db.add(state)
        state.consecutive_timeouts = (state.consecutive_timeouts or 0) + 1
        state.consecutive_successes = 0
        state.last_timeout_at = datetime.now(UTC)
        await db.commit()
        logger.warning(
            "gosom_health_timeout_recorded consecutive_timeouts=%s",
            state.consecutive_timeouts,
        )


async def has_outstanding_discovery_work(session_factory) -> bool:
    """True when at least one cell is PENDING/IN_PROGRESS under a RUNNING
    run — i.e. Gosom has real work waiting on it right now. Without this
    check, a quiet period with no census running would look identical to a
    freeze (no successes, growing time-since-last-success) and trigger a
    pointless restart.
    """
    async with session_factory() as db:
        count = await db.scalar(
            select(MapsCensusCell.id)
            .join(MapsCensusRun, MapsCensusRun.id == MapsCensusCell.run_id)
            .where(
                MapsCensusRun.status == MapsCensusStatus.RUNNING,
                MapsCensusCell.status.in_(
                    [MapsCensusCellStatus.PENDING, MapsCensusCellStatus.IN_PROGRESS]
                ),
            )
            .limit(1)
        )
        return count is not None


async def is_restart_in_progress(session_factory) -> bool:
    async with session_factory() as db:
        state = (await db.execute(select(GosomHealthState).limit(1))).scalars().first()
        return bool(state is not None and state.restart_in_progress)


async def is_within_breaker_cooldown(session_factory, settings) -> bool:
    """True while the persisted timeout streak is still inside the SAME
    cooldown window the in-memory ``gosom_breaker`` (CircuitState.OPEN)
    would itself be enforcing right now.

    Root cause this closes: ``recover_maps_census_runs`` (the watchdog
    cron) relaunches a stale ``run_census()`` purely on the run's own
    heartbeat going stale (10 minutes) — a much shorter window than the
    breaker's own ``maps_gosom_circuit_breaker_recovery_timeout_seconds``
    (900s default). Every relaunch constructs a brand-new, in-memory
    ``CircuitBreaker`` that starts CLOSED, so without this check the
    watchdog would repeatedly hammer a Gosom instance the breaker had
    already determined needs 15 minutes to recover — burning the bounded
    ``discovery_auto_resume_count`` budget on attempts that were never
    going to succeed (confirmed in production: Belarus run
    5acff6f7-21f8-4427-88a1-38d0a394ce83 opened its breaker at
    consecutive_timeouts=3 with ``recovery_in_seconds=899``, and the
    watchdog's own 10-minute cutoff would otherwise relaunch it 5 minutes
    into that cooldown).

    Deliberately NOT the same thing as ``evaluate_freeze``: that check has
    no notion of "elapsed enough", so gating on it would defer a stale run
    forever once frozen, with nothing ever attempting a fresh probe again
    in a deployment with no host-side supervisor polling ``/status``. This
    check is time-bounded by construction — once the cooldown window
    elapses, it returns False and the watchdog's normal relaunch resumes.
    """
    state = await get_or_create_state(session_factory)
    threshold = max(1, int(settings.maps_gosom_circuit_breaker_failure_threshold))
    if (state.consecutive_timeouts or 0) < threshold:
        return False
    if state.last_timeout_at is None:
        return False
    cooldown = timedelta(
        seconds=max(1, int(settings.maps_gosom_circuit_breaker_recovery_timeout_seconds))
    )
    last_timeout = state.last_timeout_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - last_timeout < cooldown


def _state_snapshot(state: GosomHealthState) -> dict[str, Any]:
    return {
        "consecutive_timeouts": state.consecutive_timeouts or 0,
        "consecutive_successes": state.consecutive_successes or 0,
        "last_success_at": state.last_success_at.isoformat() if state.last_success_at else None,
        "last_timeout_at": state.last_timeout_at.isoformat() if state.last_timeout_at else None,
        "restart_in_progress": bool(state.restart_in_progress),
        "restart_in_progress_since": (
            state.restart_in_progress_since.isoformat() if state.restart_in_progress_since else None
        ),
        "last_restart_at": state.last_restart_at.isoformat() if state.last_restart_at else None,
        "last_restart_reason": state.last_restart_reason,
        "restart_history": list(state.restart_history or []),
        "consecutive_failed_restarts": state.consecutive_failed_restarts or 0,
        "restart_exhausted": bool(state.restart_exhausted),
        "restart_exhausted_at": (
            state.restart_exhausted_at.isoformat() if state.restart_exhausted_at else None
        ),
        "restart_exhausted_reason": state.restart_exhausted_reason,
    }


async def get_snapshot(session_factory) -> dict[str, Any]:
    state = await get_or_create_state(session_factory)
    return _state_snapshot(state)


async def evaluate_freeze(session_factory, settings) -> FreezeDecision:
    """Combine timeout count, staleness, and real outstanding work into one
    freeze/no-freeze decision. Deliberately requires ALL of:

    - consecutive_timeouts >= threshold (several, not one slow job)
    - no successful job completion within the detection window (a slow but
      progressing job is not a freeze)
    - a cell is actually waiting on Gosom right now (no false positive when
      there is simply no work queued)
    - not already mid-restart (so the supervisor's own in-flight restart
      is never re-flagged as a fresh freeze before it has even finished)
    - not already restart_exhausted (repeated restarts that never brought
      Gosom back up — another automatic attempt would just be noise; this
      needs an operator)

    A restart completing (``complete_restart`` setting ``last_restart_at``)
    is deliberately given the same grace treatment as a real success here,
    even though it does NOT reset ``consecutive_timeouts`` — see
    ``complete_restart``'s docstring for why HTTP-responsiveness is never
    treated as proof of actual scraping. Without this grace window, a
    restart would be immediately re-flagged as a fresh freeze on the very
    next poll (the stale ``consecutive_timeouts`` count never having been
    cleared), before any new job even had a chance to attempt and succeed.
    """
    state = await get_or_create_state(session_factory)
    snapshot = _state_snapshot(state)

    if state.restart_in_progress:
        return FreezeDecision(False, "restart_already_in_progress", snapshot)

    if state.restart_exhausted:
        return FreezeDecision(False, "restart_exhausted_awaiting_operator", snapshot)

    threshold = max(1, int(settings.maps_gosom_freeze_consecutive_timeouts))
    if (state.consecutive_timeouts or 0) < threshold:
        return FreezeDecision(False, "below_consecutive_timeout_threshold", snapshot)

    window_seconds = max(1, int(settings.maps_gosom_freeze_detection_window_seconds))
    now = datetime.now(UTC)
    reference_times = [t for t in (state.last_success_at, state.last_restart_at) if t is not None]
    if reference_times:
        most_recent = max(t.replace(tzinfo=UTC) for t in reference_times)
        stale_seconds = (now - most_recent).total_seconds()
        if stale_seconds < window_seconds:
            return FreezeDecision(False, "recent_success_or_restart_within_window", snapshot)

    if not await has_outstanding_discovery_work(session_factory):
        return FreezeDecision(False, "no_outstanding_work", snapshot)

    return FreezeDecision(
        True,
        f"consecutive_timeouts={state.consecutive_timeouts} with outstanding work "
        f"and no success in >= {window_seconds}s",
        snapshot,
    )


async def begin_restart(session_factory, *, reason: str) -> bool:
    """Atomically mark a restart as starting via a single guarded UPDATE —
    never a read-then-write — so two concurrent callers (two overlapping
    supervisor polls, or a human and the supervisor) can never both believe
    they own the restart: at most one UPDATE can match the WHERE clause and
    actually flip the flag, the other gets rowcount 0.

    The WHERE clause also reclaims an abandoned ``restart_in_progress``: if
    the flag has been set for longer than
    ``maps_gosom_supervisor_max_restart_duration_seconds`` (the supervisor
    presumably crashed between /restart/begin and /restart/complete), a new
    restart is allowed to proceed instead of being blocked forever. This
    reclaim is part of the SAME atomic UPDATE, so it never races a second
    caller either.

    Returns False if a restart is already (genuinely) in progress, or if
    the state is ``restart_exhausted`` (repeated restarts never brought
    Gosom's HTTP API back — see ``complete_restart``).
    """
    from app.core.config import get_settings

    settings = get_settings()
    max_duration = timedelta(
        seconds=max(1, int(settings.maps_gosom_supervisor_max_restart_duration_seconds))
    )
    now = datetime.now(UTC)
    stale_cutoff = now - max_duration

    state = await get_or_create_state(session_factory)
    async with session_factory() as db:
        result = await db.execute(
            update(GosomHealthState)
            .where(
                GosomHealthState.id == state.id,
                GosomHealthState.restart_exhausted.is_(False),
                or_(
                    GosomHealthState.restart_in_progress.is_(False),
                    GosomHealthState.restart_in_progress_since < stale_cutoff,
                ),
            )
            .values(
                restart_in_progress=True,
                restart_in_progress_since=now,
                last_restart_reason=reason[:2000],
            )
        )
        await db.commit()
        acquired = bool(result.rowcount)

    if acquired:
        logger.error("gosom_restart_beginning reason=%s", reason)
    return acquired


async def complete_restart(session_factory, *, http_responsive: bool) -> dict[str, Any]:
    """Clear the in-progress flag and record the outcome of the restart
    ATTEMPT — never confused with whether Gosom's dispatcher is actually
    scraping again.

    ``http_responsive`` means only "the container restarted and its REST
    API answered again" — the exact failure mode this whole feature exists
    for (github.com/gosom/google-maps-scraper#328) is a container that
    answers HTTP while its internal dispatcher is still wedged, so this is
    deliberately NOT treated as proof of real progress:
    ``consecutive_timeouts``/``consecutive_successes`` are only ever reset
    by ``record_job_success``, i.e. by a real job actually completing after
    the restart. ``evaluate_freeze`` still grants a grace window after a
    responsive restart (via ``last_restart_at``) so it isn't instantly
    re-flagged before a new job has had a chance to run.

    What DOES change on a responsive restart: the discovery watchdog's
    bounded ``discovery_auto_resume_count`` is reset on every RUNNING run —
    "failure counters reset appropriately after demonstrated progress",
    where the demonstrated progress is Gosom coming back up, giving the
    run a fresh shot at its own bounded budget rather than being penalized
    for a freeze that (as far as the supervisor can currently tell) has
    been addressed.

    What happens when the restart attempt FAILS (Gosom never became
    responsive again): ``consecutive_failed_restarts`` increments instead.
    Once it reaches ``maps_gosom_supervisor_max_consecutive_restart_failures``,
    the state is marked ``restart_exhausted`` — every further restart is
    refused (see ``begin_restart``) and ``evaluate_freeze`` stops reporting
    frozen=True, so this surfaces as one clear, actionable condition for an
    operator instead of the supervisor quietly retrying forever.
    """
    from app.core.config import get_settings

    settings = get_settings()
    max_failures = max(1, int(settings.maps_gosom_supervisor_max_consecutive_restart_failures))
    now = datetime.now(UTC)

    async with session_factory() as db:
        state = (await db.execute(select(GosomHealthState).limit(1))).scalars().first()
        if state is None:
            state = GosomHealthState()
            db.add(state)
            await db.commit()
            await db.refresh(state)

        state.restart_in_progress = False
        state.restart_in_progress_since = None

        # Every ATTEMPT counts toward the rate-limit history, success or
        # failure — a supervisor hammering a dead Gosom with failed
        # restarts must still be visible to /status's restarts_in_last_hour.
        history = list(state.restart_history or [])
        history.append(f"{now.isoformat()}|{'ok' if http_responsive else 'failed'}")
        cutoff = now - _RESTART_HISTORY_WINDOW
        history = [ts for ts in history if _history_entry_time(ts) and _history_entry_time(ts) >= cutoff]
        state.restart_history = history

        if http_responsive:
            state.last_restart_at = now
            state.consecutive_failed_restarts = 0
        else:
            state.consecutive_failed_restarts = (state.consecutive_failed_restarts or 0) + 1
            if state.consecutive_failed_restarts >= max_failures:
                state.restart_exhausted = True
                state.restart_exhausted_at = now
                state.restart_exhausted_reason = (
                    f"{state.consecutive_failed_restarts} consecutive restarts failed to bring "
                    "Gosom's HTTP API back up — automatic recovery exhausted. An operator must "
                    "investigate Gosom directly (container logs, manual restart) before this "
                    "state can be cleared."
                )

        await db.commit()
        await db.refresh(state)
        snapshot = _state_snapshot(state)

    if http_responsive:
        async with session_factory() as run_db:
            running = (
                (
                    await run_db.execute(
                        select(MapsCensusRun).where(MapsCensusRun.status == MapsCensusStatus.RUNNING)
                    )
                )
                .scalars()
                .all()
            )
            for run in running:
                run_state = dict(run.processing_state or {})
                if run_state.get("discovery_auto_resume_count"):
                    run_state["discovery_auto_resume_count"] = 0
                    run.processing_state = run_state
            await run_db.commit()
        logger.warning(
            "gosom_restart_completed http_responsive=True — discovery auto-resume budget reset "
            "(scraping-progress counters untouched; only a real job completion clears those)"
        )
    elif snapshot["restart_exhausted"]:
        logger.error(
            "gosom_restart_exhausted consecutive_failed_restarts=%s — refusing further automatic "
            "restarts until an operator intervenes",
            snapshot["consecutive_failed_restarts"],
        )
    else:
        logger.error(
            "gosom_restart_completed http_responsive=False consecutive_failed_restarts=%s",
            snapshot["consecutive_failed_restarts"],
        )
    return snapshot


def _history_entry_time(entry: str) -> datetime | None:
    return _parse_iso(entry.split("|", 1)[0])


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def restarts_in_last_hour(snapshot: dict[str, Any]) -> int:
    """Counts every restart ATTEMPT in the last hour — success or failure —
    matching the hourly cap the host-side supervisor enforces for itself.
    """
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    count = 0
    for entry in snapshot.get("restart_history") or []:
        parsed = _history_entry_time(entry)
        if parsed is not None and parsed >= cutoff:
            count += 1
    return count
