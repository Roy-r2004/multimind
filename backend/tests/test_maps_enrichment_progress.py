"""Tests for enrichment progress persistence and silent-exit guards."""

from __future__ import annotations

from datetime import UTC, datetime

from app.services.scraping.maps_enrichment_progress import (
    ENRICHMENT_STATUS_COMPLETED,
    ENRICHMENT_STATUS_FAILED_RETRYABLE,
    ENRICHMENT_STATUS_RUNNING,
    enrichment_status_from_run,
)


class _FakeRun:
    def __init__(self, **kwargs):
        self.processing_state = kwargs.get("processing_state")
        self.enrichment_refresh_completed_at = kwargs.get("enrichment_refresh_completed_at")
        self.enrichment_refresh_attempts = kwargs.get("enrichment_refresh_attempts", 0)
        self.heartbeat_at = kwargs.get("heartbeat_at")


def test_enrichment_status_running_when_heartbeat_present_and_not_completed():
    run = _FakeRun(
        processing_state={
            "enrichment_heartbeat_at": datetime.now(UTC).isoformat(),
            "enrichment_status": ENRICHMENT_STATUS_RUNNING,
        },
        enrichment_refresh_completed_at=None,
        enrichment_refresh_attempts=1,
    )
    assert enrichment_status_from_run(run) == ENRICHMENT_STATUS_RUNNING


def test_enrichment_status_failed_retryable_explicit():
    run = _FakeRun(
        processing_state={"enrichment_status": ENRICHMENT_STATUS_FAILED_RETRYABLE},
        enrichment_refresh_completed_at=None,
    )
    assert enrichment_status_from_run(run) == ENRICHMENT_STATUS_FAILED_RETRYABLE


def test_enrichment_status_completed_when_refresh_finished():
    run = _FakeRun(
        processing_state={},
        enrichment_refresh_completed_at=datetime.now(UTC),
    )
    assert enrichment_status_from_run(run) == ENRICHMENT_STATUS_COMPLETED


def test_derive_current_stage_enrichment_while_discovery_completed():
    from app.db.models import MapsCensusStatus
    from app.services.scraping.maps_admin_service import _derive_current_stage

    run = _FakeRun(
        processing_state={
            "enrichment_status": ENRICHMENT_STATUS_FAILED_RETRYABLE,
            "enrichment_heartbeat_at": datetime.now(UTC).isoformat(),
        },
        enrichment_refresh_completed_at=None,
        enrichment_refresh_attempts=6,
    )
    run.status = MapsCensusStatus.COMPLETED
    run.country_profile_status = None
    run.cells_completed = 10
    run.cells_total = 10
    assert _derive_current_stage(run) == "enrichment"
