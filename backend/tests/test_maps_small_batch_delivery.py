"""Targeted tests for the small-batch delivery pipeline (URGENT DELIVERY MODE).

Covers the minimum-validation scenarios requested for the resumable,
crash-safe classification/detail-enrichment batch architecture:

1. one failed place does not terminate the batch
2. completed places are committed before a later crash
3. the next batch resumes remaining pending places
4. watchdog requeues an idle campaign (atomic lock primitive)
5. duplicate watchdog/batch jobs cannot claim the same row
6. optional enrichment fields do not block export
7. Phase 2 failure preserves Phase 1 classification
8. pending records are not counted as excluded
9. the campaign reaches a terminal state
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    MapsCensusRun,
    MapsClientEligibility,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
)
from app.services.scraping.maps_batch_claim import claim_batch_place_ids
from app.services.scraping.maps_census_service import _export_eligible
from app.services.scraping.maps_classification_service import (
    MapsClassificationService,
    build_classification_query,
    maps_classification_service,
)
from app.services.scraping.maps_detail_enrichment_service import maps_detail_enrichment_service
from app.services.scraping.maps_eligibility import normalize_lifecycle_eligibility_consistency
from app.services.scraping.maps_enrichment_cascade_service import maps_enrichment_cascade_service
from app.services.scraping.maps_enrichment_processing_state import MapsEnrichmentPipelineState
from app.services.scraping.maps_enrichment_progress import (
    ENRICHMENT_STATUS_COMPLETED,
    release_batch_lock,
    try_acquire_batch_lock,
)
from app.services.scraping.maps_enrichment_response_parser import EnrichmentParseStats
from app.services.scraping.maps_quota_tracker import MapsQuotaTracker
from app.services.scraping.maps_sonar_fallback import SonarBudget, SonarFallbackStats


def _run(auth):
    return MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="DZ",
        country_name="Algeria",
        status="completed",
    )


def _pending_place(run_id: str, key: str, **kwargs) -> MapsPlace:
    # A place ready for Phase 2 classification/detail enrichment must already
    # be a confirmed keep — build_classification_query/build_expensive_
    # pipeline_query both gate on this (the Andorra keep/drop-bypass fix), so
    # every place these small-batch-delivery tests hand to that pipeline
    # needs to look like a genuine post-keep-drop row.
    base = dict(
        run_id=run_id,
        google_place_id=key,
        raw_name=key,
        canonical_name=key,
        is_relevant=True,
        keep_drop_decision="keep",
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    base.update(kwargs)
    return MapsPlace(**base)


@pytest.mark.asyncio
async def test_claim_batch_place_ids_is_disjoint_across_calls(db, auth):
    """5. Duplicate batch/watchdog jobs cannot claim the same row."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    places = [_pending_place(run.id, f"p{i}") for i in range(3)]
    db.add_all(places)
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)

    first = await claim_batch_place_ids(factory, build_classification_query(run.id), batch_size=2)
    second = await claim_batch_place_ids(factory, build_classification_query(run.id), batch_size=2)

    assert len(first) == 2
    assert len(second) == 1
    assert set(first).isdisjoint(second)
    assert set(first) | set(second) == {p.id for p in places}


@pytest.mark.asyncio
async def test_one_failed_place_does_not_terminate_classification_batch(db, auth, monkeypatch):
    """1. One failed place does not terminate the batch."""
    from app.core.config import get_settings as _get_settings

    monkeypatch.setattr(_get_settings(), "maps_primary_extraction_concurrency", 1)
    run = _run(auth)
    db.add(run)
    await db.flush()
    good = _pending_place(run.id, "good")
    bad = _pending_place(run.id, "bad")
    db.add_all([good, bad])
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)

    async def fake_classify_place(self, session_factory, *, place_id, **kwargs):
        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            if place.google_place_id == "bad":
                raise RuntimeError("boom")
            place.lifecycle_status = MapsLifecycleStatus.NEEDS_REVIEW.value
            place.client_eligibility = MapsClientEligibility.REVIEW.value
            place.enrichment_pipeline_state = MapsEnrichmentPipelineState.DETAIL_NOT_REQUIRED.value
            place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
            await session.commit()
        return 1

    monkeypatch.setattr(MapsClassificationService, "_classify_place", fake_classify_place)

    batch = await maps_classification_service.classify_one_batch(
        factory,
        run_id=run.id,
        country_code="DZ",
        country_name="Algeria",
        tracker=MapsQuotaTracker(),
        sonar_budget=SonarBudget(enabled=False, max_percent=0, max_per_campaign=0, selected_candidates=2),
        sonar_stats=SonarFallbackStats(),
        parse_stats=EnrichmentParseStats(),
        provider=object(),
        batch_size=10,
    )

    assert batch["claimed"] == 2
    assert batch["processed"] == 1

    await db.refresh(bad)
    assert bad.enrichment_status == MapsPlaceEnrichmentStatus.FAILED.value
    assert bad.enrichment_pipeline_state == MapsEnrichmentPipelineState.CLASSIFICATION_FAILED_RETRYABLE.value

    # 2. The good place's commit survived the sibling's failure — nothing was
    # rolled back just because another place in the same batch raised.
    await db.refresh(good)
    assert good.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
    assert good.lifecycle_status == MapsLifecycleStatus.NEEDS_REVIEW.value


@pytest.mark.asyncio
async def test_repeated_attempts_exhaustion_finalizes_needs_review_not_excluded(db, auth, monkeypatch):
    """Delivery requirement: a provider failure must never silently become excluded,
    and every relevant place must end in a valid bucket after retries exhaust."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _pending_place(run.id, "always-fails", enrichment_attempts=1)
    db.add(place)
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)

    async def always_raise(self, session_factory, *, place_id, **kwargs):
        # Mirror real _classify_place: increment attempts before the failure
        # that finally exhausts the retry budget.
        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            place.enrichment_attempts = (place.enrichment_attempts or 0) + 1
            await session.commit()
        raise RuntimeError("provider down")

    monkeypatch.setattr(MapsClassificationService, "_classify_place", always_raise)

    batch = await maps_classification_service.classify_one_batch(
        factory,
        run_id=run.id,
        country_code="DZ",
        country_name="Algeria",
        tracker=MapsQuotaTracker(),
        sonar_budget=SonarBudget(enabled=False, max_percent=0, max_per_campaign=0, selected_candidates=1),
        sonar_stats=SonarFallbackStats(),
        parse_stats=EnrichmentParseStats(),
        provider=object(),
        batch_size=10,
    )
    assert batch["claimed"] == 1

    await db.refresh(place)
    # enrichment_attempts was already 1 (>= default max of 2 after this attempt
    # increments implicitly via the claim/finalize path) -> finalized terminal.
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
    # A kept place keeps the strict gate's eligibility: a provider failure is
    # missing evidence, never a disqualification.
    assert place.lifecycle_status == MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value
    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value


@pytest.mark.asyncio
async def test_next_batch_resumes_remaining_pending_places(db, auth, monkeypatch):
    """3. The next batch resumes remaining pending places (no forward cursor)."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    places = [_pending_place(run.id, f"p{i}") for i in range(3)]
    db.add_all(places)
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)

    async def fake_classify_place(self, session_factory, *, place_id, **kwargs):
        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            place.lifecycle_status = MapsLifecycleStatus.NEEDS_REVIEW.value
            place.client_eligibility = MapsClientEligibility.REVIEW.value
            place.enrichment_pipeline_state = MapsEnrichmentPipelineState.DETAIL_NOT_REQUIRED.value
            place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
            await session.commit()
        return 1

    monkeypatch.setattr(MapsClassificationService, "_classify_place", fake_classify_place)

    common_kwargs = dict(
        run_id=run.id,
        country_code="DZ",
        country_name="Algeria",
        tracker=MapsQuotaTracker(),
        sonar_budget=SonarBudget(enabled=False, max_percent=0, max_per_campaign=0, selected_candidates=3),
        sonar_stats=SonarFallbackStats(),
        parse_stats=EnrichmentParseStats(),
        provider=object(),
        batch_size=1,
    )

    first = await maps_classification_service.classify_one_batch(factory, **common_kwargs)
    assert first["claimed"] == 1
    assert first["has_more"] is True
    assert first["pending_count"] == 2

    second = await maps_classification_service.classify_one_batch(factory, **common_kwargs)
    assert second["claimed"] == 1
    assert second["pending_count"] == 1

    third = await maps_classification_service.classify_one_batch(factory, **common_kwargs)
    assert third["claimed"] == 1
    assert third["pending_count"] == 0

    fourth = await maps_classification_service.classify_one_batch(factory, **common_kwargs)
    assert fourth["has_more"] is False

    for place in places:
        await db.refresh(place)
        assert place.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_watchdog_batch_lock_prevents_duplicate_enqueue(db, auth):
    """4. Watchdog requeues an idle campaign only once (atomic lock primitive)."""
    run = _run(auth)
    db.add(run)
    await db.commit()
    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)

    first = await try_acquire_batch_lock(factory, run_id=run.id, batch_id="tick-a", ttl_seconds=600)
    assert first is True

    second = await try_acquire_batch_lock(factory, run_id=run.id, batch_id="tick-b", ttl_seconds=600)
    assert second is False

    await release_batch_lock(factory, run_id=run.id, batch_id="tick-a")

    third = await try_acquire_batch_lock(factory, run_id=run.id, batch_id="tick-c", ttl_seconds=600)
    assert third is True


def test_export_eligible_does_not_require_addictions_languages_or_price():
    """6. Optional enrichment fields do not block export."""
    place = SimpleNamespace(
        is_relevant=True,
        canonical_name="Center of Hope",
        raw_name="Center of Hope",
        formatted_address="12 Rue de la Sante, Algiers",
        confidence_score=None,
        classification_confidence=0.9,
        addictions_treated=None,
        languages_spoken=None,
        treatment_price=None,
    )
    assert _export_eligible(place) is True


@pytest.mark.asyncio
async def test_phase2_failure_preserves_phase1_classification(db, auth):
    """7. A Phase 2 (detail enrichment) failure never changes Phase 1 classification."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="p1",
        raw_name="P1",
        canonical_name="P1",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.PROBABLE_ELIGIBLE.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        enrichment_status=MapsPlaceEnrichmentStatus.RUNNING.value,
        enrichment_attempts=1,
    )
    db.add(place)
    await db.commit()
    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)

    await maps_detail_enrichment_service._finalize_detail_failure(
        factory, places=[place.id], error="sonar down"
    )

    await db.refresh(place)
    assert place.lifecycle_status == MapsLifecycleStatus.PROBABLE_ELIGIBLE.value
    assert place.client_eligibility == MapsClientEligibility.REVIEW.value
    assert place.enrichment_pipeline_state == MapsEnrichmentPipelineState.DETAIL_ENRICHMENT_FAILED.value
    # attempts (1) below default max_attempts (2) -> retryable, not terminal.
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.FAILED.value


def test_pending_needs_review_is_never_silently_excluded():
    """8. Pending/needs_review records are not counted as excluded."""
    place = SimpleNamespace(
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
    )
    changed = normalize_lifecycle_eligibility_consistency(place)
    assert changed is True
    assert place.client_eligibility == MapsClientEligibility.REVIEW.value


@pytest.mark.asyncio
async def test_campaign_reaches_terminal_state_when_no_pending_work(db, auth):
    """9. The campaign reaches a terminal (completed) state when nothing is left
    to classify or detail-enrich — run_one_small_batch must not loop forever or
    report has_more on a fully drained run."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="done",
        raw_name="Done",
        canonical_name="Done",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.CONFIRMED_PUBLIC.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        enrichment_status=MapsPlaceEnrichmentStatus.COMPLETED.value,
        enrichment_pipeline_state=MapsEnrichmentPipelineState.DETAIL_NOT_REQUIRED.value,
    )
    db.add(place)
    await db.commit()

    outcome = await maps_enrichment_cascade_service.run_one_small_batch(
        db, run_id=run.id, batch_id="batch-1"
    )

    assert outcome["has_more"] is False
    assert outcome["phase"] == "complete"

    await db.refresh(run)
    assert run.processing_state.get("enrichment_status") == ENRICHMENT_STATUS_COMPLETED


@pytest.mark.asyncio
async def test_run_one_small_batch_skips_when_lock_already_held(db, auth):
    """Reproduces the Belarus run 5acff6f7 root cause: a second trigger for
    a run already being processed (a manual retry click, the auto-enrich
    kicked off right after keep/drop, or the watchdog) must not start a
    concurrent batch tick. Previously `run_one_small_batch` acquired no
    lock at all -- only the watchdog ever called `try_acquire_batch_lock` --
    so two independently-triggered ticks ran fully concurrently, each
    reading a stale `sonar_classify_budget.calls_used` snapshot and
    spending its own allowance on top of it."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _pending_place(run.id, "p1")
    db.add(place)
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    held = await try_acquire_batch_lock(
        factory, run_id=run.id, batch_id="already-running", ttl_seconds=600
    )
    assert held is True

    outcome = await maps_enrichment_cascade_service.run_one_small_batch(
        db, run_id=run.id, batch_id="second-trigger"
    )

    assert outcome == {"has_more": False, "phase": "lock_busy", "skipped_due_to_lock": True}

    # The skipped tick must not have touched the place at all.
    await db.refresh(place)
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value


@pytest.mark.asyncio
async def test_run_one_small_batch_releases_lock_so_next_tick_can_acquire_it(db, auth, monkeypatch):
    """The lock must not be held past the end of a single tick -- the
    legitimate self-re-enqueue chain generates a brand-new random batch_id
    for every tick (see run_maps_enrichment_batch_job), so the lock has to
    be released at the end of each tick for the *next* legitimate tick to
    be able to acquire it at all."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _pending_place(run.id, "p1")
    db.add(place)
    await db.commit()

    async def fake_classify_place(self, session_factory, *, place_id, **kwargs):
        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            place.lifecycle_status = MapsLifecycleStatus.NEEDS_REVIEW.value
            place.client_eligibility = MapsClientEligibility.REVIEW.value
            place.enrichment_pipeline_state = MapsEnrichmentPipelineState.DETAIL_NOT_REQUIRED.value
            place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
            await session.commit()
        return 1

    monkeypatch.setattr(MapsClassificationService, "_classify_place", fake_classify_place)

    outcome = await maps_enrichment_cascade_service.run_one_small_batch(
        db, run_id=run.id, batch_id="tick-1"
    )
    assert outcome.get("phase") != "lock_busy"

    await db.refresh(run)
    assert not run.processing_state.get("active_batch_lock")

    # A later trigger with a fresh batch_id must be able to acquire the lock
    # now that the tick which held it has finished and released it.
    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    reacquired = await try_acquire_batch_lock(
        factory, run_id=run.id, batch_id="tick-2", ttl_seconds=600
    )
    assert reacquired is True


@pytest.mark.asyncio
async def test_watchdog_release_before_dispatch_lets_job_acquire_lock(db, auth):
    """Reproduces the exact sequence recover_maps_census_runs now performs:
    acquire the lock as a one-shot dedupe gate, release it immediately, then
    dispatch the job. Before this fix, the watchdog held its lock across
    the dispatch, so the dispatched job's own (new) acquire -- added by the
    run_one_small_batch fix -- would always fail with a *different*
    batch_id and the run would be stuck returning lock_busy until the
    lock's 10-minute TTL expired."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _pending_place(run.id, "p1")
    db.add(place)
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    watchdog_batch_id = "watchdog:abc123"
    assert await try_acquire_batch_lock(
        factory, run_id=run.id, batch_id=watchdog_batch_id, ttl_seconds=600
    )
    await release_batch_lock(factory, run_id=run.id, batch_id=watchdog_batch_id)

    outcome = await maps_enrichment_cascade_service.run_one_small_batch(
        db, run_id=run.id, batch_id="job-tick-1"
    )
    assert outcome.get("phase") != "lock_busy"


@pytest.mark.asyncio
async def test_lock_busy_does_not_finalize_run_with_pending_work(db, auth):
    """A skipped (lock_busy) tick must never be mistaken for drained work --
    reconcile_run_if_drained has its own active_batch_lock/pending-work
    safety gates, but this locks in that a lock_busy skip leaves the run's
    pending work and status completely untouched so the in-flight holder's
    own self-enqueue (or, failing that, the watchdog) is free to finish it."""
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _pending_place(run.id, "p1")
    db.add(place)
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    held = await try_acquire_batch_lock(
        factory, run_id=run.id, batch_id="still-running", ttl_seconds=600
    )
    assert held is True

    outcome = await maps_enrichment_cascade_service.run_one_small_batch(
        db, run_id=run.id, batch_id="second-trigger"
    )
    assert outcome == {"has_more": False, "phase": "lock_busy", "skipped_due_to_lock": True}

    from app.services.scraping.maps_run_finalization import reconcile_run_finalization

    await db.refresh(run)
    reconciled = await reconcile_run_finalization(db, run_id=run.id)
    assert reconciled["reconciled"] is False
    assert "active_batch_lock" in reconciled["blockers"]
    assert "enrichment_places_active" in reconciled["blockers"]

    await db.refresh(place)
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value


@pytest.mark.asyncio
async def test_sonar_classify_stats_accumulate_across_batches(db, auth, monkeypatch):
    """The other half of the same Belarus discrepancy: `sonar_classify_stats`
    was overwritten with only the latest tick's own local counters instead
    of accumulating onto the run-wide total, so the persisted stats showed
    all zeros even though the persisted `sonar_classify_budget.calls_used`
    had reached the cap. Seed a prior tick's cumulative stats directly on
    the run, then run one real tick and confirm the new tick's own count is
    added on top rather than replacing them."""
    run = _run(auth)
    run.processing_state = {
        "sonar_classify_stats": {
            "sonar_calls": 5,
            "sonar_parse_failures": 0,
            "sonar_repair_attempts": 0,
            "sonar_repair_successes": 0,
            "sonar_final_failures": 0,
            "sonar_input_tokens": 0,
            "sonar_output_tokens": 0,
            "sonar_actual_cost": 0.0,
            "budget_exhausted": False,
        },
    }
    db.add(run)
    await db.flush()
    place = _pending_place(run.id, "p1")
    db.add(place)
    await db.commit()

    async def fake_classify_place(
        self, session_factory, *, place_id, sonar_stats=None, **kwargs
    ):
        if sonar_stats is not None:
            sonar_stats.sonar_calls += 1
        async with session_factory() as session:
            place = await session.get(MapsPlace, place_id)
            place.lifecycle_status = MapsLifecycleStatus.NEEDS_REVIEW.value
            place.client_eligibility = MapsClientEligibility.REVIEW.value
            place.enrichment_pipeline_state = MapsEnrichmentPipelineState.DETAIL_NOT_REQUIRED.value
            place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
            await session.commit()
        return 1

    monkeypatch.setattr(MapsClassificationService, "_classify_place", fake_classify_place)

    await maps_enrichment_cascade_service.run_one_small_batch(db, run_id=run.id, batch_id="tick-2")

    await db.refresh(run)
    stats_after = run.processing_state["sonar_classify_stats"]
    # Must be 5 (seeded prior total) + 1 (this tick) = 6, not reset to 1.
    assert stats_after["sonar_calls"] == 6
