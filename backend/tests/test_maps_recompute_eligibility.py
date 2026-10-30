"""Regression tests: recompute_eligibility must be usable on real-world Maps
census places (which always have a keep_drop_decision — see apply_keep_drop)
to apply an eligibility rule change (e.g. the inpatient/residential
override) WITHOUT any paid AI call, and must never invent an inpatient
designation where the stored evidence is insufficient.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.dependencies import AuthContext
from app.db.models import (
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsFacilityType,
    MapsLifecycleStatus,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
    MapsPlace,
)
from app.services.scraping.maps_admin_service import maps_admin_service


async def _create_run(db, auth: AuthContext) -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FI",
        country_name="Finland",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run


def _base_place(run_id: str, google_place_id: str, **overrides) -> MapsPlace:
    base = dict(
        run_id=run_id,
        google_place_id=google_place_id,
        raw_name=google_place_id,
        canonical_name=google_place_id,
        is_relevant=True,
        keep_drop_decision="review",
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        operator_type=MapsOperatorType.NONPROFIT.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
    )
    base.update(overrides)
    return MapsPlace(**base)


@pytest.mark.asyncio
async def test_recompute_eligibility_applies_inpatient_override_without_ai_call(db, auth):
    """A previously-classified place stuck at REVIEW (outpatient category,
    no explicit inpatient evidence recorded at the time) that already has
    inpatient_treatment=True stored (e.g. backfilled once the fix to stop
    discarding it ships, or a place reclassified after that fix) must
    become ELIGIBLE purely from recomputing against already-stored fields —
    no new AI call. The old filter (keep_drop_decision IS NULL) would have
    matched zero real places here, since every KEEP/REVIEW candidate always
    has a non-null keep_drop_decision."""
    run = await _create_run(db, auth)
    place = _base_place(
        run.id,
        "mixed-setting-1",
        facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        inpatient_treatment=True,
    )
    db.add(place)
    await db.commit()

    result = await maps_admin_service.recompute_eligibility(db, auth, run.id)
    assert "1 places" in result.message or "Recomputed eligibility for 1" in result.message

    await db.refresh(place)
    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
    assert place.keep_drop_decision == "review"  # untouched — never overwritten


@pytest.mark.asyncio
async def test_recompute_eligibility_never_invents_inpatient_designation(db, auth):
    """The same outpatient-category place but with inpatient_treatment
    still unset (the stored evidence is genuinely insufficient) must stay
    unresolved, never be auto-promoted to ELIGIBLE by the recompute pass."""
    run = await _create_run(db, auth)
    place = _base_place(
        run.id,
        "outpatient-no-evidence-1",
        facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        inpatient_treatment=None,
    )
    db.add(place)
    await db.commit()

    await maps_admin_service.recompute_eligibility(db, auth, run.id)

    await db.refresh(place)
    assert place.client_eligibility == MapsClientEligibility.EXCLUDED.value
    assert place.client_eligibility != MapsClientEligibility.ELIGIBLE.value


@pytest.mark.asyncio
async def test_recompute_eligibility_never_touches_dropped_places(db, auth):
    """A confident keep/drop rejection must never be reopened by a bulk
    eligibility recompute, even if its (irrelevant, pre-drop) stored
    facility_type would otherwise look eligible."""
    run = await _create_run(db, auth)
    place = _base_place(
        run.id,
        "dropped-1",
        keep_drop_decision="drop",
        is_relevant=False,
        lifecycle_status=MapsLifecycleStatus.UNRELATED.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        residential_accommodation=True,
    )
    db.add(place)
    await db.commit()

    await maps_admin_service.recompute_eligibility(db, auth, run.id)

    await db.refresh(place)
    assert place.client_eligibility == MapsClientEligibility.EXCLUDED.value
    assert place.keep_drop_decision == "drop"


@pytest.mark.asyncio
async def test_recompute_eligibility_never_overrides_manual_exclusion(db, auth):
    run = await _create_run(db, auth)
    from datetime import UTC, datetime

    place = _base_place(
        run.id,
        "manually-excluded-1",
        facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        inpatient_treatment=True,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        manually_excluded_at=datetime.now(UTC),
    )
    db.add(place)
    await db.commit()

    await maps_admin_service.recompute_eligibility(db, auth, run.id)

    await db.refresh(place)
    assert place.client_eligibility == MapsClientEligibility.EXCLUDED.value


@pytest.mark.asyncio
async def test_recompute_eligibility_makes_no_ai_provider_calls(db, auth, monkeypatch):
    """Hard guarantee: patch every AI entry point this module could
    plausibly reach: if recompute_eligibility ever calls one, the test
    fails loudly instead of silently passing."""
    run = await _create_run(db, auth)
    place = _base_place(
        run.id,
        "no-ai-call-1",
        facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
        inpatient_treatment=True,
    )
    db.add(place)
    await db.commit()

    def _fail(*_args, **_kwargs):
        raise AssertionError("recompute_eligibility must never call an AI provider")

    monkeypatch.setattr(
        "app.services.scraping.maps_classification_service.create_structured_classification_provider",
        _fail,
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_keep_drop_service.classify_place_keep_drop",
        _fail,
    )

    await maps_admin_service.recompute_eligibility(db, auth, run.id)

    await db.refresh(place)
    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
