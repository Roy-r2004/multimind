"""Tests for Maps Census Phase 2 AI web-search enrichment (no crawling)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.db.models import MapsCensusRun, MapsPlace, MapsPlaceEnrichmentStatus
from app.services.scraping.maps_place_enrichment_service import (
    ADDICTION_TAXONOMY,
    maps_place_enrichment_service,
)


class _FakeProvider:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls = 0
        self.last_user = ""

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 4096):
        self.calls += 1
        self.last_user = user
        return SimpleNamespace(text=json.dumps(self._payload))


class _FailingProvider:
    async def complete(self, **_kwargs):
        raise RuntimeError("search unavailable")


def _patch_provider(monkeypatch, provider) -> None:
    monkeypatch.setattr(
        "app.services.scraping.maps_place_enrichment_service.get_model",
        lambda _slug: SimpleNamespace(provider="mock", provider_model="mock-model"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_place_enrichment_service.get_provider_registry",
        lambda: SimpleNamespace(get_provider=lambda _name: provider),
    )


async def _completed_run(db, auth, *, country_code="DZ", country_name="Algeria") -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code=country_code,
        country_name=country_name,
        status="completed",
    )
    db.add(run)
    await db.flush()
    return run


@pytest.mark.asyncio
async def test_enrich_fills_addictions_and_languages_without_website(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = await _completed_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="no-site",
        raw_name="Centre El Wassit",
        canonical_name="Centre El Wassit",
        is_relevant=True,
        confidence_score=0.9,
        formatted_address="Boumerdès, Algeria",
        international_phone_number="+213 555 11 22 33",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    provider = _FakeProvider(
        {
            "results": [
                {
                    "place_id": place.id,
                    "addictions_treated": [
                        {"value": "Heroin", "evidence_quote": "...", "source_url": "https://x"},
                        {"value": "hashish", "evidence_quote": "...", "source_url": "https://x"},
                    ],
                    "languages_spoken": [
                        {"value": "Arabic", "evidence_quote": "...", "source_url": "https://x"},
                        {"value": "French", "evidence_quote": "...", "source_url": "https://x"},
                    ],
                }
            ]
        }
    )
    _patch_provider(monkeypatch, provider)

    summary = await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    assert summary["enriched"] == 1

    await db.refresh(place)
    await db.refresh(run)
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
    # "hashish" maps into the taxonomy; unknown/behavioral stays canonical.
    assert place.addictions_treated == ["Heroin", "Cannabis (dependency)"]
    assert place.languages_spoken == ["Arabic", "French"]
    assert place.treatment_price is None
    assert run.places_enriched == 1


@pytest.mark.asyncio
async def test_enrich_reprocesses_previously_skipped_places(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = await _completed_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="fb-only",
        raw_name="ALT Association",
        canonical_name="ALT Association",
        is_relevant=True,
        confidence_score=0.9,
        formatted_address="Oran, Algeria",
        official_website="https://www.facebook.com/associationaltoran/",
        website_source="places_social",
        enrichment_status=MapsPlaceEnrichmentStatus.SKIPPED.value,
    )
    db.add(place)
    await db.commit()

    provider = _FakeProvider(
        {
            "results": [
                {
                    "place_id": place.id,
                    "addictions_treated": [{"value": "Alcohol", "evidence_quote": "..."}],
                    "languages_spoken": [],
                }
            ]
        }
    )
    _patch_provider(monkeypatch, provider)

    summary = await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    assert summary["enriched"] == 1
    await db.refresh(place)
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
    assert place.addictions_treated == ["Alcohol"]


@pytest.mark.asyncio
async def test_enrich_marks_failed_on_provider_error(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = await _completed_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="boom",
        raw_name="Broken Clinic",
        canonical_name="Broken Clinic",
        is_relevant=True,
        confidence_score=0.9,
        formatted_address="Algiers, Algeria",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    _patch_provider(monkeypatch, _FailingProvider())

    summary = await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    assert summary["enriched"] == 0
    await db.refresh(place)
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.FAILED.value
    assert place.enrichment_error_message


@pytest.mark.asyncio
async def test_enrich_completes_empty_when_model_omits_place(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = await _completed_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="omitted",
        raw_name="Unknown Center",
        canonical_name="Unknown Center",
        is_relevant=True,
        confidence_score=0.9,
        formatted_address="Batna, Algeria",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    _patch_provider(monkeypatch, _FakeProvider({"results": []}))

    summary = await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    assert summary["enriched"] == 0
    await db.refresh(place)
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
    assert place.addictions_treated == []


@pytest.mark.asyncio
async def test_contradicted_verdict_drops_place_from_census(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = await _completed_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="not-rehab",
        raw_name="عيادة طبية عامة",
        canonical_name="عيادة طبية عامة",
        is_relevant=True,
        confidence_score=0.95,
        formatted_address="Batna, Algeria",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    provider = _FakeProvider(
        {
            "results": [
                {
                    "place_id": place.id,
                    "verification": {
                        "verdict": "contradicted",
                        "reason": "Sources show a general medical clinic with no addiction program.",
                        "source_url": "https://example.dz/clinic",
                    },
                    "addictions_treated": [{"value": "Alcohol", "evidence_quote": "..."}],
                    "languages_spoken": [{"value": "Arabic", "evidence_quote": "..."}],
                }
            ]
        }
    )
    _patch_provider(monkeypatch, provider)

    summary = await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    assert summary["enriched"] == 0

    await db.refresh(place)
    await db.refresh(run)
    assert place.is_relevant is False
    assert place.verification_verdict == "contradicted"
    assert place.verification_source_url == "https://example.dz/clinic"
    # Values the model offered are discarded along with the place.
    assert place.addictions_treated == []
    assert place.languages_spoken == []
    assert run.places_classified_relevant == 0


@pytest.mark.asyncio
async def test_unknown_verdict_keeps_place(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = await _completed_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="unfindable",
        raw_name="مركز الوسيط لعلاج المدمنين",
        canonical_name="مركز الوسيط لعلاج المدمنين",
        is_relevant=True,
        confidence_score=0.9,
        formatted_address="Boumerdès, Algeria",
        international_phone_number="+213 555 00 00 00",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    provider = _FakeProvider(
        {
            "results": [
                {
                    "place_id": place.id,
                    "verification": {"verdict": "unknown", "reason": "No sources found."},
                    "addictions_treated": [],
                    "languages_spoken": [],
                }
            ]
        }
    )
    _patch_provider(monkeypatch, provider)

    await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    await db.refresh(place)
    await db.refresh(run)
    assert place.is_relevant is True
    assert place.verification_verdict == "unknown"
    assert run.places_classified_relevant == 1


@pytest.mark.asyncio
async def test_missing_or_invalid_verdict_defaults_to_unknown(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = await _completed_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="no-verdict",
        raw_name="Clinique Lilas",
        canonical_name="Clinique Lilas",
        is_relevant=True,
        confidence_score=0.9,
        formatted_address="Algiers, Algeria",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    provider = _FakeProvider(
        {
            "results": [
                {
                    "place_id": place.id,
                    "verification": {"verdict": "probably not sure"},
                    "addictions_treated": [{"value": "Alcohol", "evidence_quote": "..."}],
                    "languages_spoken": [],
                }
            ]
        }
    )
    _patch_provider(monkeypatch, provider)

    await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    await db.refresh(place)
    # An unrecognized verdict must never silently delete a facility.
    assert place.verification_verdict == "unknown"
    assert place.is_relevant is True
    assert place.addictions_treated == ["Alcohol"]


def test_addiction_taxonomy_includes_substance_and_behavioral():
    assert "Alcohol" in ADDICTION_TAXONOMY
    assert "Gambling" in ADDICTION_TAXONOMY
    assert "Cryptocurrency Trading" in ADDICTION_TAXONOMY
