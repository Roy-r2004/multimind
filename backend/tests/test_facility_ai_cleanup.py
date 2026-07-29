"""Unit tests for final AI facility cleanup application logic."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.scraping.facility_ai_cleanup_service import (
    FacilityCleanupDecision,
    apply_cleanup_decisions,
)


def _facility(**kwargs):
    defaults = {
        "id": "fac-1",
        "canonical_name": "Clinic",
        "primary_website": "https://www.euda.europa.eu/node/2620_en",
        "publication_class": "review_required",
        "duplicate_status": "unique",
        "human_review_status": "required",
        "hard_gate_results_json": {},
        "country_containment_reason": None,
        "contacts": [],
        "is_mock": False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_apply_cleanup_excludes_bad_source_and_duplicate_and_fixes_website():
    keep = _facility(
        id="keep-1",
        canonical_name="A-Clinic",
        primary_website="https://wrong.example/path",
        publication_class="verified",
    )
    dup = _facility(
        id="dup-1",
        canonical_name="A Clinic Helsinki",
        primary_website="https://a-clinic.example",
        publication_class="review_required",
    )
    bad = _facility(
        id="bad-1",
        canonical_name="Free drugs info",
        primary_website="https://www.euda.europa.eu/node/2620_en",
        publication_class="review_required",
    )

    summary = apply_cleanup_decisions(
        facilities_by_id={"keep-1": keep, "dup-1": dup, "bad-1": bad},
        decisions=[
            FacilityCleanupDecision(
                facility_id="keep-1",
                action="keep",
                corrected_website="https://a-clinic.example/",
                reason="Official clinic site",
            ),
            FacilityCleanupDecision(
                facility_id="dup-1",
                action="exclude_duplicate",
                keep_facility_id="keep-1",
                reason="Same clinic as keep-1",
            ),
            FacilityCleanupDecision(
                facility_id="bad-1",
                action="exclude_bad_source",
                reason="EUDA info page, not a rehab center",
            ),
        ],
    )

    assert summary["excluded"] == 2
    assert summary["websites_fixed"] == 1
    assert keep.primary_website == "https://a-clinic.example/"
    assert dup.publication_class == "excluded"
    assert dup.duplicate_status == "merged"
    assert bad.publication_class == "excluded"
    assert "EUDA" in (bad.country_containment_reason or "")


def test_apply_cleanup_ignores_invalid_duplicate_keep_target():
    facility = _facility(id="only-1", publication_class="verified")
    summary = apply_cleanup_decisions(
        facilities_by_id={"only-1": facility},
        decisions=[
            FacilityCleanupDecision(
                facility_id="only-1",
                action="exclude_duplicate",
                keep_facility_id="missing",
                reason="bad target",
            )
        ],
    )
    assert summary["excluded"] == 0
    assert facility.publication_class == "verified"
