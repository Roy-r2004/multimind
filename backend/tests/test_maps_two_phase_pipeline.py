"""Tests for two-phase Maps enrichment (classify then detail enrich)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.dependencies import AuthContext
from app.db.models import (
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
)
from app.services.scraping.maps_classification_rules import apply_deterministic_classification
from app.services.scraping.maps_enrichment_selection import (
    build_expensive_pipeline_query,
    is_detail_enrichment_candidate,
    skip_reason_for_place,
)
from app.services.scraping.maps_sonar_classify import needs_sonar_classification


def test_deterministic_public_hospital_name():
    place = SimpleNamespace(
        canonical_name="CHU Mustapha Pacha",
        raw_name=None,
        place_types=["hospital"],
    )
    rule = apply_deterministic_classification(place)
    assert rule is not None
    assert rule.lifecycle_status == MapsLifecycleStatus.CONFIRMED_PUBLIC.value
    assert rule.client_eligibility == MapsClientEligibility.EXCLUDED.value


def test_deterministic_cessation_only():
    place = SimpleNamespace(
        canonical_name="Laserostop Oran",
        raw_name=None,
        place_types=["health"],
    )
    rule = apply_deterministic_classification(place)
    assert rule is not None
    assert rule.lifecycle_status == MapsLifecycleStatus.CONFIRMED_CESSATION_ONLY.value


def test_needs_sonar_when_excluded_not_review():
    place = SimpleNamespace(
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        facility_type="unknown",
        ownership_status="ownership_unknown",
        addiction_focus_confirmed=None,
        classification_confidence=None,
    )
    assert needs_sonar_classification(place) is True


def test_detail_candidate_requires_eligible_or_review():
    eligible = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.PROBABLE_ELIGIBLE.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        facility_type="unknown",
    )
    excluded_public = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.CONFIRMED_PUBLIC.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        facility_type="general_mental_health_clinic",
    )
    needs_review = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        facility_type="unknown",
    )
    assert is_detail_enrichment_candidate(eligible) is True
    assert is_detail_enrichment_candidate(excluded_public) is False
    assert is_detail_enrichment_candidate(needs_review) is True


def test_build_classification_query_filters_pending_relevant():
    from app.services.scraping.maps_classification_service import build_classification_query

    query = build_classification_query("run-1")
    compiled = str(query.whereclause)
    assert "is_relevant" in compiled
    assert "enrichment_status" in compiled
    assert "keep_drop_decision" in compiled


def test_skip_reason_for_place_blocks_undecided_needs_review():
    """The Andorra bug: a needs_review candidate with no keep/drop decision
    yet must never be treated as Phase-2-eligible."""
    undecided = SimpleNamespace(
        keep_drop_decision=None,
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,  # Phase 1's actual default
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    assert skip_reason_for_place(undecided) == "keep_drop_not_decided"


def test_skip_reason_for_place_allows_genuine_keep():
    kept = SimpleNamespace(
        keep_drop_decision="keep",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value,
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    assert skip_reason_for_place(kept) is None


def test_skip_reason_for_place_allows_manual_mark_eligible_override():
    """A manual mark_eligible admin override never touches keep_drop_decision
    but does set client_eligibility=ELIGIBLE — must still pass the gate."""
    manually_overridden = SimpleNamespace(
        keep_drop_decision=None,
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value,
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    assert skip_reason_for_place(manually_overridden) is None


@pytest.mark.asyncio
async def test_expensive_pipeline_query_excludes_undecided_and_includes_kept(
    db, auth: AuthContext
):
    """End-to-end regression for the Andorra fix: build_expensive_pipeline_query
    must select a genuinely kept place but never an undecided review candidate,
    even though both have is_relevant=True and a review-ish lifecycle status."""
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="AD",
        country_name="Andorra",
        status=MapsCensusStatus.RUNNING,
    )
    db.add(run)
    await db.flush()

    undecided = MapsPlace(
        run_id=run.id,
        google_place_id="dianova",
        raw_name="Asociacion Dianova Espana",
        canonical_name="Asociacion Dianova Espana",
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        is_relevant=True,
        keep_drop_decision=None,
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    kept = MapsPlace(
        run_id=run.id,
        google_place_id="aditrea",
        raw_name="ADITREA - Tratamiento de Adicciones",
        canonical_name="ADITREA - Tratamiento de Adicciones",
        lifecycle_status=MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value,
        client_eligibility=MapsClientEligibility.ELIGIBLE.value,
        is_relevant=True,
        keep_drop_decision="keep",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add_all([undecided, kept])
    await db.commit()

    selected = (
        await db.execute(build_expensive_pipeline_query(run.id))
    ).scalars().all()
    selected_ids = {p.id for p in selected}
    assert kept.id in selected_ids
    assert undecided.id not in selected_ids


@pytest.mark.asyncio
async def test_request_enrichment_refuses_while_keep_drop_undecided(db, auth: AuthContext):
    """Defense-in-depth: even if a race lets /enrich fire while candidates are
    still undecided, it must refuse with a clear error instead of silently
    starting a no-op cascade."""
    from app.core.exceptions import ValidationError
    from app.services.scraping.maps_census_service import maps_census_service

    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="AD",
        country_name="Andorra",
        status=MapsCensusStatus.COMPLETED,
        cells_total=1,
        cells_completed=1,
        places_found=1,
    )
    db.add(run)
    await db.flush()
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="undecided-1",
            raw_name="Undecided Candidate",
            canonical_name="Undecided Candidate",
            lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
            client_eligibility=MapsClientEligibility.REVIEW.value,
            is_relevant=True,
            keep_drop_decision=None,
            enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
        )
    )
    await db.commit()

    with pytest.raises(ValidationError, match="Keep/drop gate"):
        await maps_census_service.request_enrichment(db, auth, run.id)
