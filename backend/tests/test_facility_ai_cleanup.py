"""Unit tests for final AI facility cleanup application logic."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.scraping.facility_ai_cleanup_service import (
    FacilityCleanupDecision,
    _is_ai_reviewed,
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


def test_apply_cleanup_records_plain_keep_for_resumability():
    """A plain 'keep' with no website fix must still be marked reviewed.

    Without this, a restart mid-cleanup would resend every already-kept
    facility to the LLM again instead of resuming from where it left off.
    """
    facility = _facility(id="keep-only", publication_class="review_required")
    assert not _is_ai_reviewed(facility)

    apply_cleanup_decisions(
        facilities_by_id={"keep-only": facility},
        decisions=[
            FacilityCleanupDecision(
                facility_id="keep-only",
                action="keep",
                reason="Legitimate rehabilitation clinic",
            )
        ],
    )

    assert _is_ai_reviewed(facility)
    assert facility.hard_gate_results_json["ai_cleanup"]["action"] == "keep"
    assert facility.hard_gate_results_json["ai_cleanup"]["website_fixed"] is False


def test_apply_cleanup_batch_failure_leaves_facility_unreviewed():
    """A provider/parse failure for a batch must not be recorded as reviewed.

    This keeps the facility eligible for retry on the next resumed attempt
    instead of permanently skipping it because of a transient LLM failure.
    """
    facility = _facility(id="retry-me", publication_class="review_required")

    apply_cleanup_decisions(
        facilities_by_id={"retry-me": facility},
        decisions=[
            FacilityCleanupDecision(
                facility_id="retry-me",
                action="keep",
                reason="cleanup_batch_failed",
            )
        ],
    )

    assert not _is_ai_reviewed(facility)
    assert facility.hard_gate_results_json == {}


def test_apply_cleanup_provider_skip_marks_reviewed_so_poison_cannot_wedge():
    """After solo fallback also fails, mark reviewed so resume advances past it."""
    facility = _facility(id="poison", publication_class="review_required")

    apply_cleanup_decisions(
        facilities_by_id={"poison": facility},
        decisions=[
            FacilityCleanupDecision(
                facility_id="poison",
                action="keep",
                reason="cleanup_skipped_provider_error",
            )
        ],
    )

    assert _is_ai_reviewed(facility)
    assert facility.hard_gate_results_json["ai_cleanup"]["action"] == "keep"


def test_apply_website_promotes_existing_contact_instead_of_duplicating():
    """Updating primary website to a URL already on the facility must not collide."""
    from app.services.scraping.facility_ai_cleanup_service import _apply_website

    existing = SimpleNamespace(
        id="c-existing",
        contact_type="website",
        value="https://www.paihdepalvelusaatio.fi/",
        normalized_value="https://www.paihdepalvelusaatio.fi/",
        is_primary=False,
    )
    primary = SimpleNamespace(
        id="c-primary",
        contact_type="website",
        value="https://wrong.example/",
        normalized_value="https://wrong.example/",
        is_primary=True,
    )
    facility = _facility(
        id="fac-web",
        primary_website="https://wrong.example/",
        contacts=[primary, existing],
    )

    assert _apply_website(facility, "https://www.paihdepalvelusaatio.fi/") is True
    assert facility.primary_website == "https://www.paihdepalvelusaatio.fi/"
    assert existing.is_primary is True
    assert primary.is_primary is False
    assert primary.value == "https://wrong.example/"
    assert len(facility.contacts) == 2
