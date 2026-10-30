"""Tests for the Phase 2-only retry action: retry_unresolved_eligibility_evidence
and its dry-run preview, eligibility_retry_projection.

Targets ONLY places whose inpatient_treatment AND residential_accommodation
are both still unresolved — never re-scrapes Google Maps, never resets Phase
1 discovery, never touches an already-confirmed eligible decision, and is
idempotent/safe against duplicate execution (see
maps_admin_service._eligibility_retry_candidates_query).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import (
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
)
from app.core.exceptions import ConflictError
from app.services.scraping.maps_admin_service import maps_admin_service
from app.services.scraping.maps_census_service import maps_census_service
from app.services.scraping.maps_enrichment_processing_state import MapsEnrichmentPipelineState


def _run(auth, **kwargs) -> MapsCensusRun:
    base = dict(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="BY",
        country_name="Belarus",
        status=MapsCensusStatus.COMPLETED_WITH_WARNINGS,
        cells_total=1,
        cells_completed=1,
        places_found=10,
        completed_at=datetime.now(UTC),
    )
    base.update(kwargs)
    return MapsCensusRun(**base)


def _place(run_id: str, google_id: str, name: str, **kwargs) -> MapsPlace:
    base = dict(
        run_id=run_id,
        google_place_id=google_id,
        raw_name=name,
        canonical_name=name,
        place_types=["health"],
        is_relevant=True,
        keep_drop_decision="review",
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        enrichment_status=MapsPlaceEnrichmentStatus.COMPLETED.value,
        enrichment_pipeline_state=MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value,
        facility_type="unknown",
        inpatient_treatment=None,
        residential_accommodation=None,
        enrichment_attempts=3,
    )
    base.update(kwargs)
    return MapsPlace(**base)


async def _stub_request_enrichment(monkeypatch) -> list[str]:
    calls: list[str] = []

    async def fake_request_enrichment(_db, _auth, run_id):
        calls.append(run_id)

    monkeypatch.setattr(maps_census_service, "request_enrichment", fake_request_enrichment)
    return calls


@pytest.mark.asyncio
async def test_targets_only_unresolved_non_eligible_relevant_places(db, auth, monkeypatch):
    """Root-cause-targeted selection: both fields unresolved, relevant,
    not manually excluded, not already eligible, not actively running."""
    run = _run(auth)
    db.add(run)
    await db.flush()

    unresolved = _place(run.id, "g1", "Unresolved Rehab")
    already_eligible = _place(
        run.id,
        "g2",
        "Already Eligible Rehab",
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        lifecycle_status=MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value,
        facility_type="residential_addiction_rehab",
    )
    manually_excluded = _place(
        run.id,
        "g3",
        "Manually Excluded Rehab",
        manually_excluded_at=datetime.now(UTC),
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
    )
    actively_running = _place(
        run.id,
        "g4",
        "Currently Processing Rehab",
        enrichment_status=MapsPlaceEnrichmentStatus.RUNNING.value,
    )
    not_relevant = _place(run.id, "g5", "Dropped Rehab", is_relevant=False)
    partially_resolved = _place(
        run.id, "g6", "Confirmed Outpatient Only Rehab",
        inpatient_treatment=False, residential_accommodation=False,
    )
    db.add_all(
        [unresolved, already_eligible, manually_excluded, actively_running, not_relevant, partially_resolved]
    )
    await db.commit()

    calls = await _stub_request_enrichment(monkeypatch)
    result = await maps_admin_service.retry_unresolved_eligibility_evidence(db, auth, run.id)

    assert "1 places" in result.message
    assert calls == [run.id]

    for place in (unresolved, already_eligible, manually_excluded, actively_running, not_relevant, partially_resolved):
        await db.refresh(place)

    assert unresolved.enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value
    assert unresolved.enrichment_pipeline_state == MapsEnrichmentPipelineState.PREFILTER_PENDING.value
    assert unresolved.enrichment_attempts == 0

    # Everything else untouched.
    assert already_eligible.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
    assert already_eligible.client_eligibility == MapsClientEligibility.ELIGIBLE.value
    assert manually_excluded.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
    assert actively_running.enrichment_status == MapsPlaceEnrichmentStatus.RUNNING.value
    assert not_relevant.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
    assert partially_resolved.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_never_touches_keep_drop_decision_or_confirmed_fields(db, auth, monkeypatch):
    """Preserves existing confirmed classifications/website evidence —
    only enrichment_status/pipeline_state/error/completed_at/attempts are
    reset, nothing else."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _place(
        run.id,
        "g1",
        "Partially Confirmed Rehab",
        keep_drop_decision="review",
        keep_drop_reason="genuine addiction signal",
        facility_type="residential_addiction_rehab",
        ownership_status="confirmed_non_government",
        official_website="https://example.by/",
        classification_evidence={"facility_type": {"value": "residential_addiction_rehab"}},
    )
    db.add(place)
    await db.commit()

    await _stub_request_enrichment(monkeypatch)
    await maps_admin_service.retry_unresolved_eligibility_evidence(db, auth, run.id)
    await db.refresh(place)

    assert place.keep_drop_decision == "review"
    assert place.keep_drop_reason == "genuine addiction signal"
    assert place.facility_type == "residential_addiction_rehab"
    assert place.ownership_status == "confirmed_non_government"
    assert place.official_website == "https://example.by/"
    assert place.classification_evidence == {"facility_type": {"value": "residential_addiction_rehab"}}


@pytest.mark.asyncio
async def test_no_candidates_returns_clear_message_without_enqueuing(db, auth, monkeypatch):
    run = _run(auth)
    db.add(run)
    await db.flush()
    resolved = _place(run.id, "g1", "Fully Resolved Rehab", inpatient_treatment=True)
    db.add(resolved)
    await db.commit()

    calls = await _stub_request_enrichment(monkeypatch)
    result = await maps_admin_service.retry_unresolved_eligibility_evidence(db, auth, run.id)

    assert "No places" in result.message
    assert calls == []


@pytest.mark.asyncio
async def test_idempotent_second_call_finds_nothing_once_resolved(db, auth, monkeypatch):
    """Re-running after the first retry's places became resolved must not
    re-select or re-spend on them — the selection naturally shrinks to zero
    on its own, no separate "already retried" bookkeeping needed."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _place(run.id, "g1", "Unresolved Rehab")
    db.add(place)
    await db.commit()

    calls = await _stub_request_enrichment(monkeypatch)
    first = await maps_admin_service.retry_unresolved_eligibility_evidence(db, auth, run.id)
    assert "1 places" in first.message

    # Simulate the reprocessing having resolved the evidence.
    place.inpatient_treatment = True
    place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
    await db.commit()

    second = await maps_admin_service.retry_unresolved_eligibility_evidence(db, auth, run.id)
    assert "No places" in second.message
    assert calls == [run.id]  # only the first call enqueued anything


@pytest.mark.asyncio
async def test_rejects_when_a_batch_is_already_actively_running(db, auth, monkeypatch):
    run = _run(auth)
    run.processing_state = {
        "active_batch_lock": {
            "batch_id": "in-flight",
            "acquired_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        }
    }
    db.add(run)
    await db.flush()
    db.add(_place(run.id, "g1", "Unresolved Rehab"))
    await db.commit()

    calls = await _stub_request_enrichment(monkeypatch)
    with pytest.raises(ConflictError):
        await maps_admin_service.retry_unresolved_eligibility_evidence(db, auth, run.id)
    assert calls == []


@pytest.mark.asyncio
async def test_dry_run_projection_never_mutates_and_reports_counts(db, auth, monkeypatch):
    run = _run(auth)
    db.add(run)
    await db.flush()
    unresolved_a = _place(run.id, "g1", "Unresolved Rehab A")
    unresolved_b = _place(run.id, "g2", "Unresolved Rehab B")
    already_eligible = _place(
        run.id, "g3", "Eligible Rehab",
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        facility_type="residential_addiction_rehab",
    )
    db.add_all([unresolved_a, unresolved_b, already_eligible])
    await db.commit()

    projection = await maps_admin_service.eligibility_retry_projection(db, auth, run.id)

    assert projection["candidate_count"] == 2
    assert projection["relevant_total_in_run"] == 3
    assert projection["max_structured_classification_calls"] == 2
    assert projection["max_total_calls_upper_bound"] >= 2

    for place in (unresolved_a, unresolved_b, already_eligible):
        await db.refresh(place)
    assert unresolved_a.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
    assert unresolved_b.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_dry_run_projection_sonar_cap_is_run_wide_not_per_candidate(db, auth, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_sonar_fallback_max_percent", 50.0)
    monkeypatch.setattr(settings, "maps_sonar_fallback_max_per_campaign", 200)

    run = _run(auth)
    db.add(run)
    await db.flush()
    # 4 relevant total, only 1 unresolved candidate.
    db.add(_place(run.id, "g1", "Unresolved Rehab"))
    db.add_all(
        _place(
            run.id, f"g{i}", f"Resolved Rehab {i}",
            inpatient_treatment=True, residential_accommodation=True,
        )
        for i in range(2, 5)
    )
    await db.commit()

    projection = await maps_admin_service.eligibility_retry_projection(db, auth, run.id)
    assert projection["candidate_count"] == 1
    assert projection["relevant_total_in_run"] == 4
    # 50% of 4 relevant = 2, capped by max_per_campaign=200 -> 2.
    assert projection["max_sonar_fallback_calls_for_whole_run"] == 2
