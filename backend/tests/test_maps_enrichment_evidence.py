"""Unit tests for the addictions/languages evidence-citation requirement.

Unsupported enrichment values (the AI asserted a value but gave neither a
quote nor a source URL) must never populate the accepted dataset — see
maps_place_enrichment_service._has_supporting_evidence.
"""

from __future__ import annotations

from app.services.scraping.maps_place_enrichment_service import (
    EvidenceField,
    _normalize_addictions,
    _normalize_languages,
)


def test_addiction_without_any_evidence_is_dropped():
    fields = [EvidenceField(value="Alcohol", evidence_quote="", source_url="")]
    assert _normalize_addictions(fields) == []


def test_addiction_with_quote_only_is_kept():
    fields = [EvidenceField(value="Alcohol", evidence_quote="Treats alcohol dependency.", source_url="")]
    assert _normalize_addictions(fields) == ["Alcohol"]


def test_addiction_with_source_url_only_is_kept():
    fields = [EvidenceField(value="Alcohol", evidence_quote="", source_url="https://example.org/programs")]
    assert _normalize_addictions(fields) == ["Alcohol"]


def test_addiction_not_in_taxonomy_is_dropped_even_with_evidence():
    fields = [
        EvidenceField(
            value="Generalized Life Coaching",
            evidence_quote="We help with everything.",
            source_url="https://example.org",
        )
    ]
    assert _normalize_addictions(fields) == []


def test_long_unsupported_addiction_list_is_fully_dropped():
    """An AI response asserting a long list of addictions with no backing
    evidence at all must not silently populate the accepted dataset."""
    fields = [
        EvidenceField(value=label, evidence_quote="", source_url="")
        for label in ["Alcohol", "Opioids", "Cocaine", "Cannabis", "Gambling"]
    ]
    assert _normalize_addictions(fields) == []


def test_language_without_any_evidence_is_dropped():
    fields = [EvidenceField(value="Finnish", evidence_quote="", source_url="")]
    assert _normalize_languages(fields) == []


def test_language_with_evidence_is_kept():
    fields = [EvidenceField(value="Finnish", evidence_quote="Palvelut suomeksi.", source_url="")]
    assert _normalize_languages(fields) == ["Finnish"]


def test_mixed_batch_keeps_only_evidenced_entries():
    fields = [
        EvidenceField(value="Alcohol", evidence_quote="Confirmed alcohol program.", source_url=""),
        EvidenceField(value="Gambling", evidence_quote="", source_url=""),
    ]
    assert _normalize_addictions(fields) == ["Alcohol"]
