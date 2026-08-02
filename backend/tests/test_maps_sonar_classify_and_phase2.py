"""Regression tests for Sonar classify parse + Phase 2 routing fixes."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.db.models import MapsClientEligibility, MapsLifecycleStatus
from app.services.scraping.maps_eligibility import compute_client_eligibility
from app.services.scraping.maps_enrichment_selection import is_detail_enrichment_candidate
from app.services.scraping.maps_place_enrichment_service import _normalize_batch_payload
from app.services.scraping.maps_sonar_classify import (
    map_sonar_classify_to_enrichment,
    parse_sonar_classify_response,
    SonarClassifyResult,
)


def test_normalize_accepts_single_facility_object():
    raw = {
        "facility_type": "outpatient_addiction_center",
        "operator_type": "nonprofit",
        "ownership_status": "probable_non_government",
        "confidence": 0.8,
    }
    assert len(_normalize_batch_payload(raw)["results"]) == 1


def test_parse_sonar_classify_single_object():
    text = json.dumps(
        {
            "classification_bucket": "eligible_candidate",
            "facility_type": "outpatient_addiction_center",
            "operator_type": "association",
            "addiction_treatment_mission": "confirmed",
            "confidence": 0.82,
            "reason": "Association treating toxicomanie",
            "evidence": [
                {"quote": "lutte contre la toxicomanie", "source_url": "https://example.org/about"}
            ],
        }
    )
    result = parse_sonar_classify_response(text)
    assert result.classification_bucket == "eligible_candidate"
    mapped = map_sonar_classify_to_enrichment(result, place_id="p1")
    assert mapped.facility_type == "outpatient_addiction_center"
    assert mapped.addiction_focus_confirmed is True
    assert mapped.addictions_treated == []


def test_parse_sonar_classify_ignores_markdown_fence():
    text = """```json
{"classification_bucket":"public","facility_type":null,"operator_type":"public_hospital",
 "addiction_treatment_mission":"unknown","confidence":0.9,"reason":"CHU",
 "evidence":[{"quote":"Centre Hospitalier Universitaire","source_url":"https://example.org"}]}
```"""
    result = parse_sonar_classify_response(text)
    assert result.classification_bucket == "public"


def test_unresolved_unknown_facility_is_review_not_excluded():
    place = SimpleNamespace(
        operating_status="open",
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        ownership_status="ownership_unknown",
        operator_type="unknown",
        facility_type="unknown",
        organization_scope="unknown",
        addiction_focus_confirmed=None,
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


def test_needs_review_enters_phase2_public_does_not():
    needs_review = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        facility_type="unknown",
    )
    needs_review_excluded_stale = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        facility_type="unknown",
    )
    public = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.CONFIRMED_PUBLIC.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        facility_type="general_mental_health_clinic",
    )
    individual = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        facility_type="individual_addictologist",
    )
    unrelated = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.UNRELATED.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        facility_type="unrelated",
    )
    assert is_detail_enrichment_candidate(needs_review) is True
    assert is_detail_enrichment_candidate(needs_review_excluded_stale) is True
    assert is_detail_enrichment_candidate(public) is False
    assert is_detail_enrichment_candidate(individual) is False
    assert is_detail_enrichment_candidate(unrelated) is False


def test_map_eligible_candidate_bucket():
    result = SonarClassifyResult(
        classification_bucket="eligible_candidate",
        facility_type="residential_addiction_rehab",
        operator_type="nonprofit",
        addiction_treatment_mission="confirmed",
        confidence=0.9,
        reason="residential rehab",
        evidence=[],
    )
    mapped = map_sonar_classify_to_enrichment(result, place_id="x")
    assert mapped.ownership_status == "probable_non_government"
    assert mapped.addiction_focus_confirmed is True
