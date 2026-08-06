"""Regression test: detail enrichment must persist a phone number the model
returns, and the prompt/schema must actually ask for one.

Root cause of Andorra's recovered facility having a real website + email but
no phone despite the number being plain visible text on its crawled contact
page: (1) maps_detail_enricher.j2 never asked the model for a phone at all,
and (2) even if it had, MapsDetailEnrichmentService._enrich_batch never
applied result.contact_phone onto the place — only contact_email was wired.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import MapsCensusRun, MapsPlace, MapsPlaceEnrichmentStatus
from app.services.scraping.maps_detail_enrichment_service import (
    MapsDetailEnrichmentService,
)
from app.services.scraping.maps_enrichment_response_parser import EnrichmentParseStats
from app.services.scraping.maps_quota_tracker import MapsQuotaTracker


def _run(auth):
    return MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="AD",
        country_name="Andorra",
        status="completed",
    )


def _place(run_id: str, key: str, **kwargs) -> MapsPlace:
    base = dict(
        run_id=run_id,
        google_place_id=key,
        raw_name=key,
        canonical_name=key,
        is_relevant=True,
        keep_drop_decision="keep",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    base.update(kwargs)
    return MapsPlace(**base)


def test_prompts_ask_for_a_phone_number():
    """The Andorra bug's real root cause: neither production enrichment
    prompt ever requested a phone field at all."""
    from pathlib import Path

    prompts_dir = Path(__file__).resolve().parents[1] / "app" / "prompts" / "scraping"
    for filename in ("maps_place_enricher.j2", "maps_detail_enricher.j2"):
        text = (prompts_dir / filename).read_text(encoding="utf-8")
        assert '"phone"' in text, f"{filename} never asks the model to return a phone field"


@pytest.mark.asyncio
async def test_enrich_batch_persists_phone_from_model_result(db, auth, monkeypatch):
    run = _run(auth)
    db.add(run)
    await db.flush()
    place = _place(run.id, "la-coma")
    db.add(place)
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)

    fake_result = SimpleNamespace(
        place_id=place.id,
        addictions_treated=[],
        languages_spoken=[],
        treatment_price=None,
        # Public webmail domain so _email_belongs_to_facility accepts it
        # without needing a real website set on this website-less test place
        # (which would otherwise trigger a real network crawl attempt).
        contact_email="lacoma@gmail.com",
        contact_phone="932 376 824",
    )

    async def fake_fetch_detail_batch(self, payloads, **kwargs):
        return [fake_result]

    monkeypatch.setattr(
        MapsDetailEnrichmentService, "_fetch_detail_batch", fake_fetch_detail_batch
    )

    service = MapsDetailEnrichmentService()
    completed = await service._enrich_batch(
        factory,
        places=[place],
        country_code="AD",
        country_name="Andorra",
        parse_stats=EnrichmentParseStats(),
        tracker=MapsQuotaTracker(),
    )
    assert completed == 1

    async with factory() as session:
        refreshed = await session.get(MapsPlace, place.id)
        assert refreshed.international_phone_number == "932 376 824"
        assert refreshed.contact_email == "lacoma@gmail.com"
