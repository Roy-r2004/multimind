"""Tests for Maps Census Phase 2 website enrichment."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.dependencies import AuthContext
from app.db.models import MapsCensusRun, MapsPlace, MapsPlaceEnrichmentStatus
from app.services.scraping.maps_place_enrichment_service import (
    ADDICTION_TAXONOMY,
    maps_place_enrichment_service,
)


class _FakeProvider:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls = 0

    async def complete(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(text=json.dumps(self._payload))


@pytest.mark.asyncio
async def test_enrich_place_extracts_addictions_and_languages_not_price(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FI",
        country_name="Finland",
        status="completed",
    )
    db.add(run)
    await db.flush()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="enrich-1",
        raw_name="Example Rehab",
        canonical_name="Example Rehab",
        is_relevant=True,
        confidence_score=0.95,
        formatted_address="1 Rehab Street, Helsinki",
        official_website="https://example-rehab.test/",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    html = """
    <html><body>
    <h1>Example Rehab</h1>
    <p>We treat alcohol addiction and gambling disorder in our inpatient program.</p>
    <p>Treatment available in English and Finnish.</p>
    <p>Inpatient program from €4,500 per month.</p>
    </body></html>
    """

    async def fake_fetch(url: str):
        return SimpleNamespace(url=url, html=html, final_url=url)

    monkeypatch.setattr(
        "app.services.scraping.maps_place_enrichment_service._fetch_html",
        fake_fetch,
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_place_enrichment_service.get_model",
        lambda _slug: SimpleNamespace(provider="mock", provider_model="mock-model"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_place_enrichment_service.get_provider_registry",
        lambda: SimpleNamespace(
            get_provider=lambda _name: _FakeProvider(
                {
                    "addictions_treated": [
                        {
                            "value": "Alcohol",
                            "evidence_quote": "We treat alcohol addiction and gambling disorder",
                        },
                        {
                            "value": "Gambling",
                            "evidence_quote": "We treat alcohol addiction and gambling disorder",
                        },
                    ],
                    "languages_spoken": [
                        {"value": "English", "evidence_quote": "Treatment available in English and Finnish"},
                        {"value": "Finnish", "evidence_quote": "Treatment available in English and Finnish"},
                    ],
                    "treatment_price": {
                        "value": "€4,500 per month",
                        "evidence_quote": "Inpatient program from €4,500 per month",
                    },
                }
            )
        ),
    )

    summary = await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    assert summary["enriched"] == 1

    await db.refresh(place)
    await db.refresh(run)
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
    assert place.addictions_treated == ["Alcohol", "Gambling"]
    assert place.languages_spoken == ["English", "Finnish"]
    assert place.treatment_price is None  # price extraction is intentionally disabled
    assert run.places_enriched == 1


@pytest.mark.asyncio
async def test_enrich_skips_facebook_contact_pages(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="DZ",
        country_name="Algeria",
        status="completed",
    )
    db.add(run)
    await db.flush()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="fb-only",
        raw_name="ALT Association",
        canonical_name="ALT Association",
        is_relevant=True,
        official_website="https://web.facebook.com/ALT.Association/",
        website_source="places_social",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    summary = await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    assert summary["enriched"] == 0
    await db.refresh(place)
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.SKIPPED.value
    assert place.addictions_treated is None


@pytest.mark.asyncio
async def test_enrich_skips_places_without_website(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FI",
        country_name="Finland",
        status="completed",
    )
    db.add(run)
    await db.flush()
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="no-site",
            raw_name="No Website Rehab",
            canonical_name="No Website Rehab",
            is_relevant=True,
            confidence_score=0.92,
            formatted_address="2 Street, Helsinki",
            enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
        )
    )
    await db.commit()

    await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    place = (
        await db.execute(select(MapsPlace).where(MapsPlace.google_place_id == "no-site"))
    ).scalar_one()
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.SKIPPED.value


def test_addiction_taxonomy_includes_substance_and_behavioral():
    assert "Alcohol" in ADDICTION_TAXONOMY
    assert "Gambling" in ADDICTION_TAXONOMY
    assert "Cryptocurrency Trading" in ADDICTION_TAXONOMY