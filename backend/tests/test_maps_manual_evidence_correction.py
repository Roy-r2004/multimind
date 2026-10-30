"""Regression tests for the generic, reusable manual-evidence-correction
mechanism, exercised against the exact real case it was built for
(A-klinikkasäätiön Päihdesairaala, Finland run 0afe4ca7-2dd3-47aa-a2cd-
566e6b19b208, place_id 9d2506c5-9ea2-4130-ad63-e9f7b51e1386) — a genuine
inpatient addiction hospital that primary/structured classification left
unresolved because (a) its ownership evidence was pure name-suffix
inference ("säätiö" = foundation) rather than the real, documented
Attendo acquisition, (b) its facility_type evidence never went beyond
the generic Google Maps category, and (c) its organization_scope was
set to "network" from a fact about its PARENT foundation's nationwide
footprint, not this one, individually identified, single-address
hospital.

Also proves the mechanism is genuinely reusable, not a Finland-specific
exception: nothing about the module itself references Finland, this
facility's name, or any hardcoded place_id — the test fixtures below are
the only facility-specific data, supplied as plain function arguments.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.dependencies import AuthContext
from app.db.models import (
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsOrganizationScope,
    MapsPlace,
)
from app.services.scraping.maps_manual_evidence_correction import (
    MANUAL_CORRECTION_SOURCE_TAG,
    ManualFieldCorrection,
    apply_manual_correction,
    plan_manual_correction,
    preview_manual_correction,
)


def _factory(db):
    return async_sessionmaker(bind=db.bind, expire_on_commit=False)


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


def _paihdesairaala_place(run_id: str) -> MapsPlace:
    """The real, current (post-generic-backfill) state of this record."""
    return MapsPlace(
        run_id=run_id,
        google_place_id="paihdesairaala-1",
        raw_name="A-klinikkasäätiön Päihdesairaala",
        canonical_name="A-klinikkasäätiön Päihdesairaala",
        formatted_address="Leppävaarankatu 10, 02600 Espoo, Finland",
        city_name="Helsinki",
        is_relevant=True,
        keep_drop_decision="keep",
        client_eligibility=MapsClientEligibility.REVIEW.value,
        lifecycle_status="needs_review",
        operator_type="unknown",
        ownership_status=None,
        facility_type=None,
        organization_scope=MapsOrganizationScope.NETWORK.value,
        care_setting="mixed",
        residential_accommodation=True,
        inpatient_treatment=None,
        addiction_focus_confirmed=True,
        medical_detox=True,
        geo_boundary_match="inside_bbox_fallback",
    )


def _real_corrections() -> list[ManualFieldCorrection]:
    return [
        ManualFieldCorrection(
            field="ownership_status",
            new_value="confirmed_non_government",
            evidence_quote=(
                "A-klinikka Oy is owned by Attendo Suomi effective August 1, 2026 "
                "(corporate ownership transfer)."
            ),
            source_url="https://a-klinikkasaatio.fi/uutiset/yrityskauppa-a-klinikka-attendolle/",
        ),
        ManualFieldCorrection(
            field="operator_type",
            new_value="private_company",
            evidence_quote=(
                "Operating entity is A-klinikka Oy, owned by Attendo Suomi "
                "(a private healthcare company) effective August 1, 2026."
            ),
            source_url="https://a-klinikkasaatio.fi/uutiset/yrityskauppa-a-klinikka-attendolle/",
        ),
        ManualFieldCorrection(
            field="facility_type",
            new_value="residential_addiction_rehab",
            evidence_quote=(
                "Inpatient addiction detoxification and rehabilitation are the primary "
                "services at this location, with approximately 40 client places."
            ),
            source_url="https://www.a-klinikka.fi/toimipisteet/paihdesairaala/",
        ),
        ManualFieldCorrection(
            field="inpatient_treatment",
            new_value=True,
            evidence_quote=(
                "Inpatient addiction detoxification and rehabilitation are the primary "
                "services; approximately 40 client places; separate detox and "
                "rehabilitation phone lines for this specific facility."
            ),
            source_url="https://www.a-klinikka.fi/toimipisteet/paihdesairaala/",
        ),
        ManualFieldCorrection(
            field="organization_scope",
            new_value="facility",
            evidence_quote=(
                "This is a single, specifically identified physical hospital location "
                "at Leppävaarankatu 10, 02600 Espoo, with its own dedicated services "
                "page — not a generic reference to the parent network's footprint."
            ),
            source_url="https://www.a-klinikka.fi/toimipisteet/paihdesairaala/",
        ),
    ]


# --- Evidence requirement: a correction without a cited source is refused
# at construction time, never silently accepted. ---


def test_correction_without_source_url_is_rejected():
    with pytest.raises(ValueError):
        ManualFieldCorrection(
            field="ownership_status",
            new_value="confirmed_non_government",
            evidence_quote="Some claim.",
            source_url="",
        )


def test_correction_without_evidence_quote_is_rejected():
    with pytest.raises(ValueError):
        ManualFieldCorrection(
            field="ownership_status",
            new_value="confirmed_non_government",
            evidence_quote="",
            source_url="https://example.org/",
        )


# --- Dry run reaches ELIGIBLE and matches real documented facts ---


@pytest.mark.asyncio
async def test_dry_run_for_genuine_inpatient_hospital_reaches_eligible(db, auth):
    run = await _create_run(db, auth)
    place = _paihdesairaala_place(run.id)
    db.add(place)
    await db.commit()

    plan = await preview_manual_correction(
        _factory(db), place_id=place.id, corrections=_real_corrections()
    )

    assert plan.eligibility_before == MapsClientEligibility.REVIEW.value
    assert plan.eligibility_after == MapsClientEligibility.ELIGIBLE.value
    changed = {c.field: c.new_value for c in plan.changes}
    assert changed == {
        "ownership_status": "confirmed_non_government",
        "operator_type": "private_company",
        "facility_type": "residential_addiction_rehab",
        "inpatient_treatment": True,
        "organization_scope": "facility",
    }

    # Dry run must not write anything.
    await db.refresh(place)
    assert place.ownership_status is None
    assert place.client_eligibility == MapsClientEligibility.REVIEW.value


@pytest.mark.asyncio
async def test_apply_persists_exactly_the_dry_run_plan_with_cited_sources(db, auth):
    run = await _create_run(db, auth)
    place = _paihdesairaala_place(run.id)
    db.add(place)
    await db.commit()

    dry_run_plan = await preview_manual_correction(
        _factory(db), place_id=place.id, corrections=_real_corrections()
    )

    result = await apply_manual_correction(
        _factory(db),
        place_id=place.id,
        corrections=_real_corrections(),
        reviewer_note="Verified via official A-klinikka/Attendo sources.",
    )
    assert result["changed"] is True
    assert result["plan"].eligibility_after == dry_run_plan.eligibility_after

    await db.refresh(place)
    assert place.ownership_status == "confirmed_non_government"
    assert place.operator_type == "private_company"
    assert place.facility_type == "residential_addiction_rehab"
    assert place.inpatient_treatment is True
    assert place.organization_scope == "facility"
    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
    assert place.lifecycle_status == "confirmed_eligible"
    assert place.is_relevant is True

    # Evidence is replaced with the cited sources, not deleted/blanked.
    assert place.classification_evidence["facility_type"]["source_url"] == (
        "https://www.a-klinikka.fi/toimipisteet/paihdesairaala/"
    )
    audit = place.classification_evidence["_manual_correction_audit"]
    assert len(audit) == 1
    assert audit[0]["source"] == MANUAL_CORRECTION_SOURCE_TAG
    assert audit[0]["eligibility_before"] == MapsClientEligibility.REVIEW.value
    assert audit[0]["eligibility_after"] == MapsClientEligibility.ELIGIBLE.value
    assert any(s["field"] == "facility_type" for s in audit[0]["sources"])


@pytest.mark.asyncio
async def test_applying_the_same_correction_twice_is_idempotent(db, auth):
    run = await _create_run(db, auth)
    place = _paihdesairaala_place(run.id)
    db.add(place)
    await db.commit()

    first = await apply_manual_correction(
        _factory(db), place_id=place.id, corrections=_real_corrections()
    )
    assert first["changed"] is True

    second = await apply_manual_correction(
        _factory(db), place_id=place.id, corrections=_real_corrections()
    )
    assert second["changed"] is False

    await db.refresh(place)
    assert len(place.classification_evidence["_manual_correction_audit"]) == 1


# --- Scope limits: never touches a manual exclusion or a confident drop ---


@pytest.mark.asyncio
async def test_manually_excluded_place_is_refused(db, auth):
    from datetime import UTC, datetime

    run = await _create_run(db, auth)
    place = _paihdesairaala_place(run.id)
    place.manually_excluded_at = datetime.now(UTC)
    place.client_eligibility = MapsClientEligibility.EXCLUDED.value
    db.add(place)
    await db.commit()

    with pytest.raises(ValueError):
        await apply_manual_correction(
            _factory(db), place_id=place.id, corrections=_real_corrections()
        )

    await db.refresh(place)
    assert place.ownership_status is None
    assert place.client_eligibility == MapsClientEligibility.EXCLUDED.value


@pytest.mark.asyncio
async def test_confidently_dropped_place_is_refused(db, auth):
    run = await _create_run(db, auth)
    place = _paihdesairaala_place(run.id)
    place.keep_drop_decision = "drop"
    place.is_relevant = False
    place.client_eligibility = MapsClientEligibility.EXCLUDED.value
    db.add(place)
    await db.commit()

    with pytest.raises(ValueError):
        await apply_manual_correction(
            _factory(db), place_id=place.id, corrections=_real_corrections()
        )


# --- The precise requirement: this genuine inpatient hospital qualifies,
# while the outpatient/organization-wide-evidence A-klinikka branches
# (the exact same chain, different records) remain unresolved. ---


@pytest.mark.asyncio
async def test_genuine_inpatient_hospital_eligible_outpatient_branches_stay_unresolved(db, auth):
    from app.services.scraping.maps_evidence_backfill import plan_backfill_for_place

    run = await _create_run(db, auth)
    hospital = _paihdesairaala_place(run.id)
    outpatient_branch = MapsPlace(
        run_id=run.id,
        google_place_id="a-klinikka-branch-1",
        raw_name="A-klinikka",
        canonical_name="A-klinikka",
        city_name="Raahe",
        is_relevant=True,
        keep_drop_decision="review",
        client_eligibility=MapsClientEligibility.REVIEW.value,
        addiction_focus_confirmed=True,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        residential_accommodation=True,
        classification_evidence={
            "ownership_status": {
                "value": "Private",
                "evidence_quote": "A-klinikka Oy is owned by Attendo, a private company.",
                "source_url": "https://www.a-klinikka.fi/",
            },
            "residential_accommodation": {
                "value": True,
                "evidence_quote": (
                    "A-klinikkakoti Hämeenlinna and Pyhtää offer around-the-clock "
                    "supported housing and communal living for addiction rehabilitation clients."
                ),
                "source_url": "https://www.a-klinikka.fi/toimipisteet/a-klinikkakoti-hameenlinna/",
            },
        },
    )
    db.add_all([hospital, outpatient_branch])
    await db.commit()

    # The hospital: manual, source-backed correction -> ELIGIBLE.
    hospital_result = await apply_manual_correction(
        _factory(db), place_id=hospital.id, corrections=_real_corrections()
    )
    assert hospital_result["plan"].eligibility_after == MapsClientEligibility.ELIGIBLE.value

    # The unrelated branch: automated backfill only (no manual evidence
    # exists for it) -> stays REVIEW, residential downgraded, never
    # promoted by the hospital's correction touching a different record.
    branch_plan = plan_backfill_for_place(outpatient_branch)
    assert branch_plan.eligibility_after == MapsClientEligibility.REVIEW.value

    await db.refresh(hospital)
    await db.refresh(outpatient_branch)
    assert hospital.client_eligibility == MapsClientEligibility.ELIGIBLE.value
    assert outpatient_branch.client_eligibility == MapsClientEligibility.REVIEW.value


# --- Reusability: the mechanism takes arbitrary place_id/corrections —
# nothing Finland- or facility-specific is hardcoded in the module. ---


@pytest.mark.asyncio
async def test_mechanism_is_generic_and_reusable_for_any_place_and_country(db, auth):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="DE",
        country_name="Germany",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    await db.commit()

    place = MapsPlace(
        run_id=run.id,
        google_place_id="some-german-facility-1",
        raw_name="Beispiel Reha gGmbH",
        canonical_name="Beispiel Reha gGmbH",
        city_name="Berlin",
        is_relevant=True,
        keep_drop_decision="keep",
        client_eligibility=MapsClientEligibility.REVIEW.value,
        addiction_focus_confirmed=True,
        organization_scope=MapsOrganizationScope.FACILITY.value,
    )
    db.add(place)
    await db.commit()

    corrections = [
        ManualFieldCorrection(
            field="ownership_status",
            new_value="confirmed_non_government",
            evidence_quote="Beispiel Reha gGmbH is a wholly-owned subsidiary of a private foundation, per its own official registry filing.",
            source_url="https://example-registry.de/beispiel-reha",
        ),
        ManualFieldCorrection(
            field="facility_type",
            new_value="residential_addiction_rehab",
            evidence_quote="The facility operates a 60-bed residential addiction rehabilitation unit on site.",
            source_url="https://beispiel-reha.de/leistungen",
        ),
    ]
    result = await apply_manual_correction(_factory(db), place_id=place.id, corrections=corrections)
    assert result["changed"] is True
    await db.refresh(place)
    assert place.ownership_status == "confirmed_non_government"
    assert place.facility_type == "residential_addiction_rehab"
