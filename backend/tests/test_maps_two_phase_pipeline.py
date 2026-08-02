"""Tests for two-phase Maps enrichment (classify then detail enrich)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db.models import MapsClientEligibility, MapsLifecycleStatus, MapsPlaceEnrichmentStatus
from app.services.scraping.maps_classification_rules import apply_deterministic_classification
from app.services.scraping.maps_enrichment_selection import is_detail_enrichment_candidate
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
    )
    excluded_public = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.CONFIRMED_PUBLIC.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
    )
    assert is_detail_enrichment_candidate(eligible) is True
    assert is_detail_enrichment_candidate(excluded_public) is False


def test_build_classification_query_filters_pending_relevant():
    from app.services.scraping.maps_classification_service import build_classification_query

    query = build_classification_query("run-1")
    compiled = str(query.whereclause)
    assert "is_relevant" in compiled
    assert "enrichment_status" in compiled
