"""Regression tests closing the keep/drop eligibility bypass.

A preliminary keep/drop AI decision (the primary Keep/Drop model's "keep",
or the Sonar
fallback's "review") must never be sufficient on its own to establish
final client_eligibility — only compute_client_eligibility, called once
real Phase 2 classification evidence exists (or an explicit, authorized
manual override), may set it to "eligible". These tests exercise the
real production functions end to end: apply_keep_drop ->
compute_client_eligibility -> maps_export_service.build_workbook.
"""

from __future__ import annotations

import json
from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.core.dependencies import AuthContext
from app.db.models import (
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsFacilityType,
    MapsLifecycleStatus,
    MapsOrganizationScope,
    MapsOwnershipStatus,
    MapsPlace,
)
from app.services.scraping.maps_eligibility import compute_client_eligibility
from app.services.scraping.maps_export_service import (
    ELIGIBLE_CENTERS_SHEET,
    maps_export_service,
)
from app.services.scraping.maps_keep_drop_service import KeepDropDecision, apply_keep_drop

KEEP_JSON = json.dumps(
    {"decision": "keep", "reason": "looks residential", "confidence": 0.92, "evidence": []}
)
REVIEW_JSON = json.dumps(
    {"decision": "review", "reason": "uncertain residential status", "confidence": 0.6, "evidence": []}
)


def _place(run_id: str, google_place_id: str, name: str, **overrides) -> MapsPlace:
    payload = {
        "run_id": run_id,
        "google_place_id": google_place_id,
        "raw_name": name,
        "canonical_name": name,
    }
    payload.update(overrides)
    return MapsPlace(**payload)


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


def _completed_classification_fields(**overrides) -> dict:
    fields = {
        "ownership_status": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        "organization_scope": MapsOrganizationScope.FACILITY.value,
        "facility_type": MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        "addiction_focus_confirmed": True,
    }
    fields.update(overrides)
    return fields


# --- 1. Confident keep before classification -> NOT exportable. ---


@pytest.mark.asyncio
async def test_confident_keep_before_classification_not_exportable(db, auth):
    run = await _create_run(db, auth)
    place = _place(run.id, "g1", "Fresh Keep")
    db.add(place)
    await db.commit()

    decision = KeepDropDecision.model_validate(json.loads(KEEP_JSON))
    apply_keep_drop(place, decision, source="nano")
    place.international_phone_number = "+358 40 000 0001"
    await db.commit()

    assert place.client_eligibility != MapsClientEligibility.ELIGIBLE.value
    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    names = [
        row[0].value
        for row in load_workbook(BytesIO(content))[ELIGIBLE_CENTERS_SHEET].iter_rows(min_row=2)
        if row[0].value
    ]
    assert "Fresh Keep" not in names


# --- 2. Keep + classification failure -> NOT exportable. ---


@pytest.mark.asyncio
async def test_keep_with_classification_failure_not_exportable(db, auth):
    """A classification failure leaves facility_type/ownership unresolved;
    compute_client_eligibility must fall into its "unresolved -> review"
    branch, never ELIGIBLE, so the place still cannot export."""
    run = await _create_run(db, auth)
    place = _place(run.id, "g2", "Keep Then Failed Classification")
    db.add(place)
    await db.commit()

    decision = KeepDropDecision.model_validate(json.loads(KEEP_JSON))
    apply_keep_drop(place, decision, source="nano")
    place.international_phone_number = "+358 40 000 0002"
    # Simulate a classification attempt that failed before setting any
    # structured fields, then re-running compute_client_eligibility exactly
    # as maps_classification_service.py does unconditionally after its
    # try/except, regardless of success.
    place.enrichment_error_message = "classification provider timeout"
    place.client_eligibility = compute_client_eligibility(place)
    await db.commit()

    assert place.client_eligibility != MapsClientEligibility.ELIGIBLE.value
    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    names = [
        row[0].value
        for row in load_workbook(BytesIO(content))[ELIGIBLE_CENTERS_SHEET].iter_rows(min_row=2)
        if row[0].value
    ]
    assert "Keep Then Failed Classification" not in names


# --- 3. Keep + completed verified-eligible classification -> exportable
# if phone exists. ---


@pytest.mark.asyncio
async def test_keep_with_completed_eligible_classification_exports_with_phone(db, auth):
    run = await _create_run(db, auth)
    place = _place(run.id, "g3", "Keep Then Verified Eligible")
    db.add(place)
    await db.commit()

    decision = KeepDropDecision.model_validate(json.loads(KEEP_JSON))
    apply_keep_drop(place, decision, source="nano")
    for key, value in _completed_classification_fields().items():
        setattr(place, key, value)
    place.client_eligibility = compute_client_eligibility(place)
    place.international_phone_number = "+358 40 000 0003"
    await db.commit()

    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    names = [
        row[0].value
        for row in load_workbook(BytesIO(content))[ELIGIBLE_CENTERS_SHEET].iter_rows(min_row=2)
        if row[0].value
    ]
    assert "Keep Then Verified Eligible" in names


# --- 4. Keep + completed review/excluded classification -> NOT exportable. ---


@pytest.mark.asyncio
async def test_keep_with_completed_excluded_classification_not_exportable(db, auth):
    run = await _create_run(db, auth)
    place = _place(run.id, "g4", "Keep Then Turned Out Outpatient")
    db.add(place)
    await db.commit()

    decision = KeepDropDecision.model_validate(json.loads(KEEP_JSON))
    apply_keep_drop(place, decision, source="nano")
    for key, value in _completed_classification_fields(
        facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value
    ).items():
        setattr(place, key, value)
    place.client_eligibility = compute_client_eligibility(place)
    place.international_phone_number = "+358 40 000 0004"
    await db.commit()

    assert place.client_eligibility == MapsClientEligibility.EXCLUDED.value
    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    names = [
        row[0].value
        for row in load_workbook(BytesIO(content))[ELIGIBLE_CENTERS_SHEET].iter_rows(min_row=2)
        if row[0].value
    ]
    assert "Keep Then Turned Out Outpatient" not in names


# --- 5. Review + completed verified-eligible classification -> exportable. ---


@pytest.mark.asyncio
async def test_review_with_completed_eligible_classification_exports(db, auth):
    run = await _create_run(db, auth)
    place = _place(run.id, "g5", "Review Then Verified Eligible")
    db.add(place)
    await db.commit()

    decision = KeepDropDecision.model_validate(json.loads(REVIEW_JSON))
    apply_keep_drop(place, decision, source="sonar")
    assert place.keep_drop_decision == "review"
    for key, value in _completed_classification_fields().items():
        setattr(place, key, value)
    place.client_eligibility = compute_client_eligibility(place)
    place.international_phone_number = "+358 40 000 0005"
    await db.commit()

    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    names = [
        row[0].value
        for row in load_workbook(BytesIO(content))[ELIGIBLE_CENTERS_SHEET].iter_rows(min_row=2)
        if row[0].value
    ]
    assert "Review Then Verified Eligible" in names


# --- 6. Manual eligible override -> respects existing authorization and
# audit semantics (compute_client_eligibility is bypassed deliberately by
# an explicit, human-authorized action, not by a bare AI keep/drop call). ---


@pytest.mark.asyncio
async def test_manual_eligible_override_still_exports(db, auth):
    run = await _create_run(db, auth)
    place = _place(
        run.id,
        "g6",
        "Manually Confirmed",
        keep_drop_decision="keep",
        lifecycle_status=MapsLifecycleStatus.PROBABLE_ELIGIBLE.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        international_phone_number="+358 40 000 0006",
    )
    db.add(place)
    await db.commit()

    # Same action the admin review endpoint performs for mark_eligible —
    # an explicit, authorized human decision, not a bare AI guess.
    place.client_eligibility = MapsClientEligibility.ELIGIBLE.value
    place.lifecycle_status = MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value
    await db.commit()

    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    names = [
        row[0].value
        for row in load_workbook(BytesIO(content))[ELIGIBLE_CENTERS_SHEET].iter_rows(min_row=2)
        if row[0].value
    ]
    assert "Manually Confirmed" in names


# --- 7. Retry/resume -> no premature eligibility. A place re-judged after
# a prior incomplete attempt must not inherit a stale ELIGIBLE value. ---


@pytest.mark.asyncio
async def test_retry_after_incomplete_attempt_does_not_leave_premature_eligible(db, auth):
    run = await _create_run(db, auth)
    place = _place(
        run.id,
        "g7",
        "Retried Candidate",
        enrichment_status="failed",
        enrichment_error_message="previous attempt timed out",
    )
    db.add(place)
    await db.commit()

    decision = KeepDropDecision.model_validate(json.loads(KEEP_JSON))
    apply_keep_drop(place, decision, source="nano")
    await db.commit()

    # Re-running keep/drop's apply step (as a retry would) must still never
    # produce ELIGIBLE on its own.
    assert place.client_eligibility != MapsClientEligibility.ELIGIBLE.value
    assert place.enrichment_status == "pending"


# --- 8. Export requested while classification is in progress -> no
# premature eligibility (the export simply reads current DB state; a
# place mid-classification has not yet had compute_client_eligibility
# called with real evidence, so it stays REVIEW/PROBABLE_ELIGIBLE and is
# correctly excluded). ---


@pytest.mark.asyncio
async def test_export_while_classification_in_progress_excludes_place(db, auth):
    run = await _create_run(db, auth)
    place = _place(run.id, "g8", "Mid Classification")
    db.add(place)
    await db.commit()

    decision = KeepDropDecision.model_validate(json.loads(KEEP_JSON))
    apply_keep_drop(place, decision, source="nano")
    place.international_phone_number = "+358 40 000 0008"
    place.enrichment_status = "running"
    await db.commit()

    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    names = [
        row[0].value
        for row in load_workbook(BytesIO(content))[ELIGIBLE_CENTERS_SHEET].iter_rows(min_row=2)
        if row[0].value
    ]
    assert "Mid Classification" not in names
