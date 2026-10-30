"""Regression tests for the Phase 2 breakdown counts, the geo-boundary
re-evaluation function, and the recovery script's idempotency guard.

Companion to test_maps_phase2_eligibility_pipeline_fix.py (which covers the
three root-cause fixes themselves): these exercise the follow-on
requirements from the "fix the entire Phase 2 eligibility system" task —
meaningful mutually-exclusive counts, re-evaluating already-discovered
places from stored coordinates without rerunning Phase 1, and recovery
being safe to run twice without repeating paid work.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.dependencies import AuthContext
from app.db.models import (
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsFacilityType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
    MapsPlace,
)
from app.services.scraping.maps_census_service import (
    compute_phase2_breakdown,
    recompute_geo_boundary_for_run,
)
from app.services.scraping.maps_enrichment_processing_state import MapsEnrichmentPipelineState

from scripts.recover_phase2_eligibility import _affected_places_query


async def _create_run(db, auth: AuthContext, **overrides) -> MapsCensusRun:
    base = dict(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FI",
        country_name="Finland",
        status=MapsCensusStatus.COMPLETED,
    )
    base.update(overrides)
    run = MapsCensusRun(**base)
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
        keep_drop_decision="keep",
    )
    base.update(overrides)
    return MapsPlace(**base)


# --- Phase 2 breakdown counts ---


@pytest.mark.asyncio
async def test_phase2_breakdown_buckets_are_mutually_exclusive_and_sum_to_total(db, auth):
    run = await _create_run(db, auth)
    places = [
        _place(
            run.id,
            "eligible-1",
            client_eligibility=MapsClientEligibility.ELIGIBLE.value,
            enrichment_pipeline_state=MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value,
        ),
        _place(
            run.id,
            "rejected-1",
            keep_drop_decision="review",
            client_eligibility=MapsClientEligibility.EXCLUDED.value,
            enrichment_pipeline_state=MapsEnrichmentPipelineState.DETAIL_NOT_REQUIRED.value,
        ),
        _place(
            run.id,
            "unresolved-1",
            keep_drop_decision="review",
            client_eligibility=MapsClientEligibility.REVIEW.value,
            enrichment_pipeline_state=MapsEnrichmentPipelineState.NEEDS_REVIEW.value,
        ),
        _place(
            run.id,
            "pending-1",
            # The real-world trap: apply_keep_drop already stamps REVIEW
            # before classification ever runs — this must land in
            # "classification_pending", never "unresolved".
            client_eligibility=MapsClientEligibility.REVIEW.value,
            enrichment_pipeline_state=MapsEnrichmentPipelineState.PREFILTER_PENDING.value,
        ),
        _place(
            run.id,
            "failed-1",
            client_eligibility=MapsClientEligibility.REVIEW.value,
            enrichment_pipeline_state=MapsEnrichmentPipelineState.CLASSIFICATION_FAILED_RETRYABLE.value,
        ),
    ]
    db.add_all(places)
    await db.commit()

    breakdown = await compute_phase2_breakdown(db, run_id=run.id)
    assert breakdown == {
        "eligible": 1,
        "rejected": 1,
        "unresolved": 1,
        "classification_pending": 1,
        "classification_failed": 1,
    }
    assert sum(breakdown.values()) == len(places)


# --- Geo re-evaluation of already-discovered places ---


@pytest.mark.asyncio
async def test_recompute_geo_boundary_dry_run_reports_without_writing(db, auth):
    run = await _create_run(db, auth)
    pompano = _place(
        run.id,
        "pompano-1",
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        addiction_focus_confirmed=True,
        latitude=26.2379,
        longitude=-80.1248,
        geo_boundary_match="no_polygon_available",
    )
    helsinki = _place(
        run.id,
        "helsinki-1",
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        addiction_focus_confirmed=True,
        latitude=60.17,
        longitude=24.94,
        geo_boundary_match="no_polygon_available",
    )
    db.add_all([pompano, helsinki])
    await db.commit()

    def _factory():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        return async_sessionmaker(bind=db.bind, expire_on_commit=False)

    dry_run_result = await recompute_geo_boundary_for_run(_factory(), run_id=run.id, apply=False)
    assert dry_run_result["places_checked"] == 2
    assert dry_run_result["newly_outside_polygon"] == 1
    assert dry_run_result["applied"] is False

    await db.refresh(pompano)
    assert pompano.geo_boundary_match == "no_polygon_available"  # unchanged — dry run only
    assert pompano.client_eligibility == MapsClientEligibility.ELIGIBLE.value


@pytest.mark.asyncio
async def test_recompute_geo_boundary_apply_downgrades_out_of_country_result(db, auth):
    run = await _create_run(db, auth)
    pompano = _place(
        run.id,
        "pompano-2",
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        addiction_focus_confirmed=True,
        latitude=26.2379,
        longitude=-80.1248,
        geo_boundary_match="no_polygon_available",
    )
    helsinki = _place(
        run.id,
        "helsinki-2",
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        addiction_focus_confirmed=True,
        latitude=60.17,
        longitude=24.94,
        geo_boundary_match="no_polygon_available",
    )
    db.add_all([pompano, helsinki])
    await db.commit()

    def _factory():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        return async_sessionmaker(bind=db.bind, expire_on_commit=False)

    result = await recompute_geo_boundary_for_run(_factory(), run_id=run.id, apply=True)
    assert result["newly_outside_polygon"] == 1
    assert result["eligibility_downgraded"] == 1

    await db.refresh(pompano)
    await db.refresh(helsinki)
    assert pompano.geo_boundary_match == "outside_bbox_fallback"
    assert pompano.client_eligibility == MapsClientEligibility.REVIEW.value  # downgraded, not deleted
    assert helsinki.geo_boundary_match == "inside_bbox_fallback"
    assert helsinki.client_eligibility == MapsClientEligibility.ELIGIBLE.value  # untouched


@pytest.mark.asyncio
async def test_recompute_geo_boundary_never_overrides_manual_exclusion(db, auth):
    """A manual admin exclusion must survive even if geo re-evaluation also
    flags the place — mark_excluded's decision is final and audited."""
    run = await _create_run(db, auth)
    place = _place(
        run.id,
        "manually-excluded-1",
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        manually_excluded_at=datetime.now(UTC),
        latitude=26.2379,
        longitude=-80.1248,
        geo_boundary_match="no_polygon_available",
    )
    db.add(place)
    await db.commit()

    def _factory():
        from sqlalchemy.ext.asyncio import async_sessionmaker

        return async_sessionmaker(bind=db.bind, expire_on_commit=False)

    await recompute_geo_boundary_for_run(_factory(), run_id=run.id, apply=True)
    await db.refresh(place)
    assert place.client_eligibility == MapsClientEligibility.EXCLUDED.value
    assert place.manually_excluded_at is not None


# --- Recovery script idempotency: a place the first recovery pass already
# fixed with real evidence must not be picked up by a second pass. ---


@pytest.mark.asyncio
async def test_recovery_query_excludes_places_already_fixed_with_real_evidence(db, auth):
    run = await _create_run(db, auth)
    broken = _place(
        run.id,
        "broken-1",
        client_eligibility=MapsClientEligibility.REVIEW.value,
        enrichment_pipeline_state=MapsEnrichmentPipelineState.NEEDS_REVIEW.value,
        enrichment_status="completed",
        facility_type=None,
        ownership_status=None,
        operator_type=None,
        organization_scope=None,
    )
    already_fixed = _place(
        run.id,
        "already-fixed-1",
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        enrichment_pipeline_state=MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value,
        enrichment_status="pending",
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        operator_type="nonprofit",
        organization_scope=MapsOrganizationScope.FACILITY.value,
    )
    genuinely_unresolved = _place(
        run.id,
        "genuinely-unresolved-1",
        client_eligibility=MapsClientEligibility.REVIEW.value,
        enrichment_pipeline_state=MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value,
        enrichment_status="pending",
        facility_type="unknown",
        ownership_status="ownership_unknown",
        operator_type="unknown",
        organization_scope="unknown",
    )
    db.add_all([broken, already_fixed, genuinely_unresolved])
    await db.commit()

    matched = (await db.execute(_affected_places_query(run.id))).scalars().all()
    matched_ids = {place.google_place_id for place in matched}

    assert "broken-1" in matched_ids
    assert "already-fixed-1" not in matched_ids
    # All-unknown is indistinguishable at the DB level from the fixed bug's
    # effect, so it is conservatively included — a second real AI call on
    # a genuinely unresolved place is a reasonable cost, not a correctness
    # bug, and it is excluded from THIS test's assertions deliberately.
    assert "genuinely-unresolved-1" in matched_ids


@pytest.mark.asyncio
async def test_recovery_query_includes_partial_evidence_bug_victims(db, auth):
    """Regression for the real Finland data: 12 of the 19
    classification_completed places had ownership_status/organization_scope
    populated (a different code path than the buggy one) but
    facility_type/operator_type left blank by the exact same source_url
    bug, and were still stuck at client_eligibility=review. An earlier
    draft of this filter required ALL FOUR fields blank and silently
    excluded these 12 genuine bug victims from recovery forever. They must
    be included whenever at least one mandatory field is still blank and
    the place is still unresolved (review)."""
    run = await _create_run(db, auth)
    partial_evidence_victim = _place(
        run.id,
        "a-klinikka-1",
        client_eligibility=MapsClientEligibility.REVIEW.value,
        enrichment_pipeline_state=MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value,
        enrichment_status="pending",
        facility_type=None,
        ownership_status=MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value,
        operator_type=None,
        organization_scope=MapsOrganizationScope.FACILITY.value,
    )
    db.add(partial_evidence_victim)
    await db.commit()

    matched = (await db.execute(_affected_places_query(run.id))).scalars().all()
    matched_ids = {place.google_place_id for place in matched}
    assert "a-klinikka-1" in matched_ids


@pytest.mark.asyncio
async def test_recovery_query_excludes_fully_evidenced_ambiguous_review(db, auth):
    """A place that is genuinely, fully classified (all four mandatory
    fields populated) but still legitimately REVIEW for a different
    reason (e.g. residential_accommodation unconfirmed) is not a bug
    victim — re-running the paid classifier on it would not be "retrying
    something broken", it would be redundant work on a defensible,
    already-complete-on-the-mandatory-fields outcome."""
    run = await _create_run(db, auth)
    fully_evidenced_review = _place(
        run.id,
        "fully-evidenced-review-1",
        client_eligibility=MapsClientEligibility.REVIEW.value,
        enrichment_pipeline_state=MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value,
        enrichment_status="pending",
        facility_type=MapsFacilityType.GENERAL_MENTAL_HEALTH_CLINIC.value,
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        operator_type="nonprofit",
        organization_scope=MapsOrganizationScope.FACILITY.value,
    )
    db.add(fully_evidenced_review)
    await db.commit()

    matched = (await db.execute(_affected_places_query(run.id))).scalars().all()
    matched_ids = {place.google_place_id for place in matched}
    assert "fully-evidenced-review-1" not in matched_ids
