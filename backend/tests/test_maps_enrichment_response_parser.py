"""Tests for resilient Maps enrichment Sonar JSON parsing."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.services.scraping.maps_enrichment_response_parser import (
    EnrichmentParseStats,
    extract_first_json_value,
    parse_enrichment_response_text,
    prepare_response_text,
    truncate_for_log,
    validate_enrichment_batch,
)
from app.services.scraping.maps_place_enrichment_service import MapsPlaceEnrichmentBatch


def _valid_batch(place_id: str = "place-1") -> dict:
    return {
        "results": [
            {
                "place_id": place_id,
                "operator_type": "nonprofit",
                "ownership_status": "confirmed_non_government",
                "facility_type": "residential_addiction_rehab",
                "organization_scope": "facility",
                "addiction_focus_confirmed": True,
                "classification_evidence": {},
                "classification_confidence": 0.9,
            }
        ]
    }


def test_prepare_response_text_strips_markdown_fence():
    raw = "```json\n{\"results\": []}\n```"
    assert prepare_response_text(raw).startswith("{")


def test_prepare_response_text_strips_leading_prose():
    raw = "Here is the JSON you requested:\n\n{\"results\": []}"
    assert prepare_response_text(raw).startswith("{")


def test_extract_first_json_object_from_prose():
    raw = "Analysis complete.\n\n{\"results\": [{\"place_id\": \"abc\"}]}"
    snippet = extract_first_json_value(raw)
    assert json.loads(snippet)["results"][0]["place_id"] == "abc"


def test_extract_first_json_object_handles_unterminated_string():
    raw = '{"results": [{"place_id": "abc", "note": "unterminated}'
    with pytest.raises(ValueError, match="unterminated"):
        extract_first_json_value(raw)


def test_parse_enrichment_response_text_fenced_json():
    batch = _valid_batch("p1")
    raw = f"```json\n{json.dumps(batch)}\n```"
    parsed = parse_enrichment_response_text(raw)
    assert parsed["results"][0]["place_id"] == "p1"


def test_parse_enrichment_response_text_truncated_json_raises():
    batch = json.dumps(_valid_batch("p1"))
    truncated = batch[: len(batch) // 2]
    with pytest.raises(ValueError):
        parse_enrichment_response_text(truncated)


def test_validate_enrichment_batch_accepts_valid_payload():
    batch = validate_enrichment_batch(_valid_batch("p2"))
    assert isinstance(batch, MapsPlaceEnrichmentBatch)
    assert batch.results[0].place_id == "p2"


def test_validate_enrichment_batch_rejects_schema_errors():
    payload = _valid_batch("p3")
    payload["results"][0]["classification_confidence"] = 4.2
    with pytest.raises(ValidationError):
        validate_enrichment_batch(payload)


def test_truncate_for_log_limits_length():
    text = "x" * 5000
    assert len(truncate_for_log(text, max_chars=2000)) == 2000


def test_enrichment_parse_stats_merge():
    left = EnrichmentParseStats(parse_failures=2, repair_attempts=1, repair_successes=1)
    right = EnrichmentParseStats(final_failed=3)
    merged = left.merge(right)
    assert merged.parse_failures == 2
    assert merged.repair_attempts == 1
    assert merged.repair_successes == 1
    assert merged.final_failed == 3
