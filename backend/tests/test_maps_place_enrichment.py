"""Tests for Maps Census structured web-search enrichment."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import (
    MapsCensusRun,
    MapsClientEligibility,
    MapsFacilityType,
    MapsLifecycleStatus,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
)
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


@pytest.fixture(autouse=True)
def _disable_website_crawl_by_default(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_website_crawl_enabled", False)


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


def _structured_result(place_id: str, **overrides) -> dict:
    payload = {
        "place_id": place_id,
        "operator_type": MapsOperatorType.NONPROFIT.value,
        "ownership_status": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        "funding_type": "donation_based",
        "facility_type": MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        "care_setting": "residential",
        "organization_scope": MapsOrganizationScope.FACILITY.value,
        "addiction_focus_confirmed": True,
        "medical_detox": False,
        "residential_accommodation": True,
        "operating_status": "open",
        "classification_evidence": {
            "operator_type": {
                "value": MapsOperatorType.NONPROFIT.value,
                "confidence": 0.9,
                "evidence_quote": "Independent nonprofit addiction treatment provider.",
                "source_url": "https://example.org/about",
                "source_type": "official_site",
            },
            "ownership_status": {
                "value": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
                "confidence": 0.94,
                "evidence_quote": "Association a but non lucratif dediee au traitement des addictions.",
                "source_url": "https://example.org/about",
                "source_type": "official_site",
            },
            "facility_type": {
                "value": MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
                "confidence": 0.9,
                "evidence_quote": "Residential addiction rehabilitation centre.",
                "source_url": "https://example.org/programs",
                "source_type": "official_site",
            },
            "organization_scope": {
                "value": MapsOrganizationScope.FACILITY.value,
                "confidence": 0.88,
                "evidence_quote": "Standalone treatment facility.",
                "source_url": "https://example.org/about",
                "source_type": "official_site",
            },
        },
        "confidence": 0.91,
        "addictions_treated": [
            {"value": "Heroin", "evidence_quote": "Traitement de l'heroine.", "source_url": "https://example.org/programs"},
            {"value": "hashish", "evidence_quote": "Prise en charge du cannabis.", "source_url": "https://example.org/programs"},
        ],
        "languages_spoken": [
            {"value": "Arabic", "evidence_quote": "Consultations en arabe.", "source_url": "https://example.org/contact"},
            {"value": "French", "evidence_quote": "Entretiens en francais.", "source_url": "https://example.org/contact"},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_unknown_candidate_becomes_needs_review_without_demotion(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = await _completed_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="oasis",
        raw_name="Oasis Recovery",
        canonical_name="Oasis Recovery",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.PLAUSIBLE.value,
        confidence_score=0.9,
        formatted_address="Rue N 2, Biskra",
        international_phone_number="+213 656 89 34 39",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    provider = _FakeProvider(
        {
            "results": [
                _structured_result(
                    place.id,
                    operator_type=MapsOperatorType.UNKNOWN.value,
                    ownership_status=MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value,
                    funding_type="unknown",
                    facility_type=MapsFacilityType.INPATIENT_DETOX_CENTER.value,
                    care_setting="inpatient",
                    organization_scope=MapsOrganizationScope.FACILITY.value,
                    addiction_focus_confirmed=None,
                    medical_detox=True,
                    residential_accommodation=True,
                    classification_evidence={
                        "facility_type": {
                            "value": MapsFacilityType.INPATIENT_DETOX_CENTER.value,
                            "confidence": 0.61,
                            "evidence_quote": "Centre de desintoxication avec hebergement.",
                            "source_url": "https://example.org/listing",
                            "source_type": "directory",
                        }
                    },
                    confidence=0.58,
                    addictions_treated=[],
                    languages_spoken=[],
                )
            ]
        }
    )
    _patch_provider(monkeypatch, provider)

    await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    await db.refresh(place)
    await db.refresh(run)

    assert place.is_relevant is True
    assert place.lifecycle_status == MapsLifecycleStatus.NEEDS_REVIEW.value
    assert place.client_eligibility == MapsClientEligibility.REVIEW.value
    assert place.verification_verdict == "unknown"
    assert place.ownership_status == MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value
    assert place.medical_detox is True
    assert run.places_classified_relevant == 1


@pytest.mark.asyncio
async def test_association_with_public_funding_stays_non_government_and_saves_evidence(
    db, auth, monkeypatch
):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = await _completed_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="assoc-public-funded",
        raw_name="ALT Association",
        canonical_name="ALT Association",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.PLAUSIBLE.value,
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
                _structured_result(
                    place.id,
                    operator_type=MapsOperatorType.ASSOCIATION.value,
                    ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
                    funding_type="public",
                    facility_type=MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
                    care_setting="outpatient",
                    organization_scope=MapsOrganizationScope.FACILITY.value,
                    addiction_focus_confirmed=True,
                    medical_detox=False,
                    residential_accommodation=False,
                    classification_evidence={
                        "operator_type": {
                            "value": MapsOperatorType.ASSOCIATION.value,
                            "confidence": 0.9,
                            "evidence_quote": "Association de traitement des addictions.",
                            "source_url": "https://example.org/about",
                            "source_type": "official_site",
                        },
                        "ownership_status": {
                            "value": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
                            "confidence": 0.93,
                            "evidence_quote": "Association independante subventionnee par la wilaya.",
                            "source_url": "https://example.org/about",
                            "source_type": "official_site",
                        },
                        "facility_type": {
                            "value": MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value,
                            "confidence": 0.88,
                            "evidence_quote": "Centre ambulatoire de prise en charge des addictions.",
                            "source_url": "https://example.org/programs",
                            "source_type": "official_site",
                        },
                        "organization_scope": {
                            "value": MapsOrganizationScope.FACILITY.value,
                            "confidence": 0.86,
                            "evidence_quote": "Structure de soin autonome.",
                            "source_url": "https://example.org/about",
                            "source_type": "official_site",
                        },
                        "funding_type": {
                            "value": "public",
                            "confidence": 0.77,
                            "evidence_quote": "Soutien du ministere de la sante.",
                            "source_url": "https://example.org/funding",
                            "source_type": "news",
                        },
                    },
                    confidence=0.89,
                    addictions_treated=[{"value": "Alcohol", "evidence_quote": "Traitement alcool.", "source_url": "https://example.org/programs"}],
                    languages_spoken=[],
                )
            ]
        }
    )
    _patch_provider(monkeypatch, provider)

    summary = await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    assert summary["enriched"] == 1
    await db.refresh(place)

    assert place.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
    assert place.lifecycle_status == MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value
    assert place.client_eligibility == MapsClientEligibility.ELIGIBLE.value
    assert place.ownership_status == MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value
    assert place.operator_type == MapsOperatorType.ASSOCIATION.value
    assert place.funding_type == "public"
    assert place.verification_verdict == "confirmed"
    assert place.addictions_treated == ["Alcohol"]
    assert place.classification_evidence["ownership_status"]["source_url"] == "https://example.org/about"
    assert float(place.classification_confidence) == pytest.approx(0.89)


@pytest.mark.asyncio
async def test_public_hospital_becomes_confirmed_public_and_excluded(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = await _completed_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="public-hospital",
        raw_name="Hopital Psychiatrique",
        canonical_name="Hopital Psychiatrique",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.PLAUSIBLE.value,
        confidence_score=0.95,
        formatted_address="Batna, Algeria",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    provider = _FakeProvider(
        {
            "results": [
                _structured_result(
                    place.id,
                    operator_type=MapsOperatorType.PUBLIC_HOSPITAL.value,
                    ownership_status=MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value,
                    funding_type="public",
                    facility_type=MapsFacilityType.PSYCHIATRIC_CLINIC_WITH_ADDICTION_PROGRAM.value,
                    care_setting="inpatient",
                    organization_scope=MapsOrganizationScope.FACILITY.value,
                    addiction_focus_confirmed=True,
                    medical_detox=True,
                    residential_accommodation=False,
                    classification_evidence={
                        "operator_type": {
                            "value": MapsOperatorType.PUBLIC_HOSPITAL.value,
                            "confidence": 0.97,
                            "evidence_quote": "Etablissement public hospitalier specialise.",
                            "source_url": "https://example.gov.dz/hospital",
                            "source_type": "government",
                        }
                    },
                    confidence=0.96,
                )
            ]
        }
    )
    _patch_provider(monkeypatch, provider)

    summary = await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    assert summary["enriched"] == 0

    await db.refresh(place)
    await db.refresh(run)
    assert place.is_relevant is False
    assert place.lifecycle_status == MapsLifecycleStatus.CONFIRMED_PUBLIC.value
    assert place.client_eligibility == MapsClientEligibility.EXCLUDED.value
    assert place.verification_verdict == "contradicted"
    assert run.places_classified_relevant == 0


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
        lifecycle_status=MapsLifecycleStatus.PLAUSIBLE.value,
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
    assert place.lifecycle_status == MapsLifecycleStatus.PLAUSIBLE.value
    assert place.is_relevant is True


@pytest.mark.asyncio
async def test_omitted_place_is_retried_not_demoted(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = await _completed_run(db, auth)
    kept = MapsPlace(
        run_id=run.id,
        google_place_id="answered",
        raw_name="Answered Rehab",
        canonical_name="Answered Rehab",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.PLAUSIBLE.value,
        confidence_score=0.9,
        formatted_address="Rue A, Algiers",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    omitted = MapsPlace(
        run_id=run.id,
        google_place_id="ignored",
        raw_name="Ignored Rehab",
        canonical_name="Ignored Rehab",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        confidence_score=0.9,
        formatted_address="Rue B, Algiers",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add_all([kept, omitted])
    await db.commit()

    provider = _FakeProvider({"results": [_structured_result(kept.id)]})
    _patch_provider(monkeypatch, provider)

    await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    await db.refresh(kept)
    await db.refresh(omitted)
    assert kept.is_relevant is True
    assert kept.lifecycle_status == MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value
    assert omitted.is_relevant is True
    assert omitted.lifecycle_status == MapsLifecycleStatus.NEEDS_REVIEW.value
    assert omitted.enrichment_status == MapsPlaceEnrichmentStatus.FAILED.value


@pytest.mark.asyncio
async def test_empty_result_set_fails_batch_without_dropping(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = await _completed_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="survivor",
        raw_name="Survivor Rehab",
        canonical_name="Survivor Rehab",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.PLAUSIBLE.value,
        confidence_score=0.9,
        formatted_address="Rue C, Algiers",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    _patch_provider(monkeypatch, _FakeProvider({"results": []}))

    summary = await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    assert summary["enriched"] == 0
    await db.refresh(place)
    assert place.is_relevant is True
    assert place.lifecycle_status == MapsLifecycleStatus.PLAUSIBLE.value
    assert place.enrichment_status == MapsPlaceEnrichmentStatus.FAILED.value


class _DynamicFakeProvider:
    """Returns a valid structured result for every ``place_id`` embedded in
    the prompt, regardless of batch size — lets a single fake stand in for
    every LLM call across the resumable enrichment loop."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *, system: str, user: str, model: str, max_tokens: int = 4096):
        del system, model, max_tokens
        self.calls += 1
        place_ids = re.findall(r'"place_id":\s*"([^"]+)"', user)
        results = [_structured_result(pid) for pid in place_ids]
        return SimpleNamespace(text=json.dumps({"results": results}))


@pytest.mark.asyncio
async def test_enrich_run_processes_more_than_250_places_without_truncation(db, auth, monkeypatch):
    """Phase 2 gap #4: the legacy 250-place cap must no longer silently drop
    places — the resumable batch loop must process every pending place in one
    call when the call budget is not exhausted."""
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    monkeypatch.setattr(get_settings(), "maps_census_enrichment_max_places_per_run", 250)
    run = await _completed_run(db, auth)

    total_places = 300
    for i in range(total_places):
        db.add(
            MapsPlace(
                run_id=run.id,
                google_place_id=f"place-{i:04d}",
                raw_name=f"Rehab Center {i}",
                canonical_name=f"Rehab Center {i}",
                is_relevant=True,
                lifecycle_status=MapsLifecycleStatus.PLAUSIBLE.value,
                confidence_score=0.9,
                formatted_address=f"{i} Recovery Rd, Algiers",
                enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
            )
        )
    await db.commit()

    provider = _DynamicFakeProvider()
    _patch_provider(monkeypatch, provider)

    summary = await maps_place_enrichment_service.enrich_run(db, run_id=run.id)

    assert summary["enriched"] == total_places

    remaining_pending = (
        await db.execute(
            select(MapsPlace).where(
                MapsPlace.run_id == run.id,
                MapsPlace.enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value,
            )
        )
    ).scalars().all()
    assert remaining_pending == []

    completed = (
        await db.execute(
            select(MapsPlace).where(
                MapsPlace.run_id == run.id,
                MapsPlace.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value,
            )
        )
    ).scalars().all()
    assert len(completed) == total_places

    await db.refresh(run)
    assert run.processing_state is not None
    assert run.processing_state.get("enrichment_paused") is False
    assert run.quota_metrics is not None
    assert run.quota_metrics.get("enrichment_calls", 0) >= total_places / 5


def test_addiction_taxonomy_includes_substance_and_behavioral():
    assert "Alcohol" in ADDICTION_TAXONOMY
    assert "Gambling" in ADDICTION_TAXONOMY
    assert "Cryptocurrency Trading" in ADDICTION_TAXONOMY


@pytest.mark.asyncio
async def test_enrichment_includes_website_crawl_excerpt_in_prompt(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    monkeypatch.setattr(get_settings(), "maps_census_website_crawl_enabled", True)

    run = await _completed_run(db, auth)
    run.country_profile = {"website_path_keywords": ["about"]}
    place = MapsPlace(
        run_id=run.id,
        google_place_id="crawl-1",
        raw_name="Crawl Centre",
        canonical_name="Crawl Centre",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.PLAUSIBLE.value,
        official_website="https://example.org/",
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    provider = _FakeProvider({"results": [_structured_result(place.id)]})
    _patch_provider(monkeypatch, provider)

    async def _fake_crawl(_db, *, website_url, path_keywords=None, force_refresh=False):
        from app.services.scraping.maps_website_crawl_service import CrawledPage, WebsiteCrawlOutcome

        return WebsiteCrawlOutcome(
            normalized_domain="example.org",
            pages=[
                CrawledPage(
                    url=website_url,
                    title="About",
                    text_excerpt="Official residential addiction rehab centre.",
                    http_status=200,
                )
            ],
            page_urls=[website_url],
            cache_hit=False,
        )

    monkeypatch.setattr(
        "app.services.scraping.maps_place_enrichment_service.maps_website_crawl_service.crawl_website",
        _fake_crawl,
    )

    await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    await db.refresh(place)

    assert "website_crawl_excerpt" in provider.last_user
    assert "Official residential addiction rehab centre." in provider.last_user
    assert place.enrichment_pages_crawled == ["https://example.org/"]


@pytest.mark.asyncio
async def test_enrichment_drops_structured_field_without_evidence(db, auth, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    run = await _completed_run(db, auth)
    place = MapsPlace(
        run_id=run.id,
        google_place_id="no-evidence",
        raw_name="No Evidence Centre",
        canonical_name="No Evidence Centre",
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.PLAUSIBLE.value,
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
    )
    db.add(place)
    await db.commit()

    provider = _FakeProvider(
        {
            "results": [
                _structured_result(
                    place.id,
                    operator_type=MapsOperatorType.NONPROFIT.value,
                    ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
                    facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
                    classification_evidence={},
                )
            ]
        }
    )
    _patch_provider(monkeypatch, provider)

    await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    await db.refresh(place)

    assert place.facility_type is None
    assert place.operator_type is None
