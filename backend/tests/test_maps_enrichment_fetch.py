"""Tests for resilient enrichment fetch/repair loop."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.scraping.maps_enrichment_fetch import EnrichmentFetchError, fetch_enrichment_batch
from app.services.scraping.maps_enrichment_response_parser import EnrichmentParseStats


class _SequenceProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0
        self.last_user = ""

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 4096, temperature=None):
        self.calls += 1
        self.last_user = user
        index = min(self.calls - 1, len(self.responses) - 1)
        return SimpleNamespace(text=self.responses[index])


def _valid_batch(place_id: str = "p1") -> dict:
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


@pytest.mark.asyncio
async def test_fetch_enrichment_batch_repairs_malformed_json():
    provider = _SequenceProvider(
        [
            '{"results": [{"place_id": "p1", "note": "broken',
            json.dumps(_valid_batch("p1")),
        ]
    )
    stats = EnrichmentParseStats()
    results = await fetch_enrichment_batch(
        provider,
        model_slug="mock-model",
        country_code="DZ",
        country_name="Algeria",
        payloads=[{"place_id": "p1", "name": "Clinic"}],
        stats=stats,
    )
    assert results[0].place_id == "p1"
    assert stats.parse_failures >= 1
    assert stats.repair_attempts == 1
    assert stats.repair_successes == 1
    assert stats.final_failed == 0
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_fetch_enrichment_batch_failed_repair_remains_retryable():
    provider = _SequenceProvider(
        [
            "not json at all",
            "still not json",
            '{"results": [{"place_id": "p1", "note": "broken',
        ]
    )
    stats = EnrichmentParseStats()
    with pytest.raises(EnrichmentFetchError):
        await fetch_enrichment_batch(
            provider,
            model_slug="mock-model",
            country_code="DZ",
            country_name="Algeria",
            payloads=[{"place_id": "p1", "name": "Clinic"}],
            stats=stats,
        )
    assert stats.repair_attempts == 1
    assert stats.repair_successes == 0
    assert stats.final_failed == 1
