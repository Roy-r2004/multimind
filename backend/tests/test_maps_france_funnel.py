"""End-to-end Maps census funnel validation for a mocked France run.

Drives ``run_census`` → ``run_website_refresh`` → ``enrich_run`` end to end
with fully mocked Google Places / LLM providers, then asserts the funnel
report surfaces every metric Phase 2 completion promised: regions/cities
covered, cells executed, pages fetched, capped/subdivision cells, raw vs.
unique results, eligible/review/public/individuals/unrelated candidate
counts, duplicate rate, per-region saturation, provider request counts
(quota metrics), and processing-limit flags.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.db.models import (
    MapsCensusCell,
    MapsCensusCellStatus,
    MapsCensusRegion,
    MapsCensusRun,
    MapsCensusStatus,
    MapsPlace,
)
from app.services.scraping.maps_census_service import (
    MapsRelevanceDecision,
    MapsWebsiteDecision,
    maps_census_service,
)
from app.services.scraping.maps_country_profile_service import maps_country_profile_service
from app.services.scraping.maps_grid_planner import MapsGridCell
from app.services.scraping.maps_place_enrichment_service import maps_place_enrichment_service
from app.services.scraping.maps_places_client import PlaceResult, PlacesSearchOutcome


@pytest.fixture(autouse=True)
def _stub_country_profile_stage(monkeypatch):
    async def _noop_build_profile(_db, *, run_id: str):
        del run_id
        return None

    monkeypatch.setattr(maps_country_profile_service, "build_profile_for_run", _noop_build_profile)


class _FrancePlacesClient:
    """Returns a configurable :class:`PlacesSearchOutcome` per query text —
    one query is deliberately over its pagination cap to exercise
    capped-cell subdivision end to end."""

    def __init__(self, outcomes: dict[str, PlacesSearchOutcome]) -> None:
        self._outcomes = outcomes

    def is_configured(self) -> bool:
        return True

    async def search_text(self, *, query: str, region_code: str, max_results: int) -> list[PlaceResult]:
        outcome = self._outcomes.get(query)
        return list(outcome.places) if outcome else []

    async def search_text_paginated(
        self,
        *,
        query: str,
        region_code: str,
        page_size: int | None = None,
        max_pages: int | None = None,
        resume_page_token: str | None = None,
        cancel_check=None,
    ) -> PlacesSearchOutcome:
        del region_code, page_size, max_pages, resume_page_token, cancel_check
        return self._outcomes.get(query, PlacesSearchOutcome())


class _FakeGridPlanner:
    def __init__(self, cells: list[MapsGridCell]) -> None:
        self._cells = cells

    async def plan(self, **_kwargs) -> list[MapsGridCell]:
        return self._cells


def _place(google_id: str, *, name: str) -> PlaceResult:
    return PlaceResult(
        google_place_id=google_id,
        raw_name=name,
        formatted_address=f"{google_id} Rue de la Sante, Paris",
        place_types=["health"],
    )


async def _create_run(db, auth: AuthContext) -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FR",
        country_name="France",
        status=MapsCensusStatus.QUEUED,
    )
    db.add(run)
    await db.flush()
    run.country_profile = {
        "provider_terms": {
            "rehab": ["centre de soins"],
            "clinic": ["clinique addiction"],
        }
    }
    await db.commit()
    return run


@pytest.mark.asyncio
async def test_france_census_run_produces_full_funnel_report(db, auth, monkeypatch):
    monkeypatch.setattr(get_settings(), "maps_census_max_pages_per_cell", 2)
    monkeypatch.setattr(get_settings(), "maps_census_saturation_window", 10)

    run = await _create_run(db, auth)

    seed_cells = [
        MapsGridCell(
            region_name="Ile-de-France",
            city_name="Paris",
            query_text="rehab paris",
            query_family="rehab",
            query_language="fr",
        ),
        MapsGridCell(
            region_name="Auvergne-Rhone-Alpes",
            city_name="Lyon",
            query_text="rehab lyon",
            query_family="rehab",
            query_language="fr",
        ),
    ]
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner(seed_cells)
    )

    # Paris hits its pagination cap (25 raw results across 2 pages, 3 dupes)
    # -- must trigger subdivision. Lyon is a small, uncapped cell.
    paris_places = [_place(f"paris-{i}", name=f"Centre Paris {i}") for i in range(22)]
    lyon_places = [_place(f"lyon-{i}", name=f"Centre Lyon {i}") for i in range(5)]
    outcomes = {
        "rehab paris": PlacesSearchOutcome(
            places=paris_places,
            pages_fetched=2,
            raw_results_found=25,
            unique_results_found=22,
            duplicates_found=3,
            next_page_available=True,
            result_cap_reached=True,
            resume_page_token=None,
        ),
        "rehab lyon": PlacesSearchOutcome(
            places=lyon_places,
            pages_fetched=1,
            raw_results_found=5,
            unique_results_found=5,
            duplicates_found=0,
            next_page_available=False,
            result_cap_reached=False,
        ),
    }
    # Every subdivided child cell (alternate query family / locality
    # qualifiers) returns nothing further, so the queue drains quickly —
    # ``_FrancePlacesClient`` already returns an empty outcome for any query
    # not in ``outcomes``.
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _FrancePlacesClient(outcomes),
    )

    async def fake_classify_batch(self, *, provider, model_slug, country_code, country_name, payloads):
        decisions = []
        for item in payloads:
            # Even-indexed Paris facilities and all Lyon facilities are
            # plausible; odd-indexed Paris facilities are unrelated noise.
            name = item["name"]
            if "Lyon" in name or (int(name.rsplit(" ", 1)[-1]) % 2 == 0):
                decisions.append(
                    MapsRelevanceDecision(
                        place_id=item["place_id"],
                        is_relevant=True,
                        reason="addiction rehab facility",
                        confidence=0.9,
                    )
                )
            else:
                decisions.append(
                    MapsRelevanceDecision(
                        place_id=item["place_id"],
                        is_relevant=False,
                        reason="unrelated listing, not a facility",
                        confidence=0.1,
                    )
                )
        return decisions

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._classify_batch",
        fake_classify_batch,
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="openai/gpt-4.1"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_provider_registry",
        lambda: SimpleNamespace(
            get_provider=lambda _name: SimpleNamespace(
                complete=lambda **_kwargs: _empty_decisions_response()
            )
        ),
    )

    census_summary = await maps_census_service.run_census(db, run_id=run.id)
    assert census_summary.get("error") is None

    await db.refresh(run)
    assert run.status == MapsCensusStatus.COMPLETED

    cells = (
        await db.execute(select(MapsCensusCell).where(MapsCensusCell.run_id == run.id))
    ).scalars().all()
    regions = (
        await db.execute(select(MapsCensusRegion).where(MapsCensusRegion.run_id == run.id))
    ).scalars().all()

    # --- Regions / cities covered ---
    region_names = {r.region_name for r in regions}
    city_names = {c.city_name for c in cells if c.city_name}
    assert region_names == {"Ile-de-France", "Auvergne-Rhone-Alpes"}
    assert city_names == {"Paris", "Lyon"}

    # --- Cells executed / capped / subdivided ---
    assert len(cells) >= 3  # 2 seed cells + at least one Paris subdivision child
    capped_cells = [c for c in cells if c.status == MapsCensusCellStatus.CAPPED]
    subdivision_cells = [c for c in cells if c.parent_cell_id is not None]
    assert len(capped_cells) == 1
    assert capped_cells[0].query_text == "rehab paris"
    assert len(subdivision_cells) >= 1
    for child in subdivision_cells:
        assert child.parent_cell_id == capped_cells[0].id
        assert child.expansion_depth == capped_cells[0].expansion_depth + 1
        assert child.expansion_reason is not None

    funnel = run.funnel_metrics
    assert funnel is not None
    assert funnel["cells_completed"] == len(cells) - 0  # capped + completed cells both count
    assert funnel["capped_cells"] == 1
    assert funnel["subdivision_cells"] == len(subdivision_cells)
    assert funnel["pages_fetched"] >= 3  # 2 (Paris) + 1 (Lyon), children contribute 0
    assert funnel["raw_results_found"] >= 30  # 25 (Paris) + 5 (Lyon)
    assert funnel["unique_results_found"] >= 27
    assert funnel["duplicates_found"] >= 3
    assert 0.0 <= funnel["duplicate_rate"] <= 1.0

    # --- Eligible / review / public / individuals / unrelated ---
    assert funnel["eligible_candidates_found"] > 0
    assert funnel["unrelated_found"] > 0
    assert funnel["review_candidates_found"] >= 0
    assert funnel["confirmed_public_found"] >= 0
    assert funnel["individuals_found"] >= 0

    # --- Provider request counts / quota metrics ---
    quota = funnel["quota_metrics"]
    assert quota["google_places_requests"] >= 2
    assert quota["google_places_pages"] >= 3
    assert quota["classifier_calls"] >= 1

    # --- Saturation by region ---
    saturation = run.saturation_summary
    assert saturation is not None
    saturation_by_region = {r["region_name"]: r for r in saturation["regions"]}
    assert set(saturation_by_region) == {"Ile-de-France", "Auvergne-Rhone-Alpes"}
    for region_entry in saturation_by_region.values():
        assert region_entry["saturation_status"] in {"pending", "expanding", "saturated", "capped"}

    # --- Resumable website search + enrichment on top of the census run ---
    relevant_places = (
        await db.execute(
            select(MapsPlace).where(MapsPlace.run_id == run.id, MapsPlace.is_relevant.is_(True))
        )
    ).scalars().all()
    assert relevant_places  # the mixed classification above must have produced some

    async def fake_find_websites_llm_batch(self, *, provider, model_slug, country_code, country_name, batch):
        return [
            MapsWebsiteDecision(
                place_id=item["id"], url="https://example.fr/", reason="known facility", confidence=0.95
            )
            for item in batch
        ]

    async def verify_always(_url: str) -> bool:
        return True

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._find_websites_llm_batch",
        fake_find_websites_llm_batch,
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service._verify_website_reachable", verify_always
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="anthropic/claude-sonnet-4"),
    )

    website_summary = await maps_census_service.run_website_refresh(db, run_id=run.id)
    assert website_summary["places_with_website"] == len(relevant_places)

    await db.refresh(run)
    assert run.processing_state is not None
    assert run.processing_state.get("website_search_paused") is False
    assert run.quota_metrics.get("website_lookup_calls", 0) > 0

    async def fake_enrichment_provider(*, system, user, model, max_tokens=4096):
        import re

        place_ids = re.findall(r'"place_id":\s*"([^"]+)"', user)
        results = [
            {
                "place_id": pid,
                "operator_type": "nonprofit",
                "ownership_status": "confirmed_non_government",
                "funding_type": "donation_based",
                "facility_type": "residential_addiction_rehab",
                "care_setting": "residential",
                "organization_scope": "facility",
                "addiction_focus_confirmed": True,
                "medical_detox": False,
                "residential_accommodation": True,
                "operating_status": "open",
                "classification_evidence": {},
                "confidence": 0.9,
                "addictions_treated": [
                    {"value": "Alcohol", "evidence_quote": "Traitement alcool.", "source_url": "https://example.fr/programs"}
                ],
                "languages_spoken": [
                    {"value": "French", "evidence_quote": "Entretiens en francais.", "source_url": "https://example.fr/contact"}
                ],
            }
            for pid in place_ids
        ]
        return SimpleNamespace(text=json.dumps({"results": results}))

    monkeypatch.setattr(get_settings(), "maps_census_enrichment_enabled", True)
    monkeypatch.setattr(
        "app.services.scraping.maps_place_enrichment_service.get_model",
        lambda _slug: SimpleNamespace(provider="mock", provider_model="mock-model"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_place_enrichment_service.get_provider_registry",
        lambda: SimpleNamespace(
            get_provider=lambda _name: SimpleNamespace(complete=fake_enrichment_provider)
        ),
    )

    enrichment_summary = await maps_place_enrichment_service.enrich_run(db, run_id=run.id)
    assert enrichment_summary["enriched"] > 0

    await db.refresh(run)
    assert run.processing_state.get("enrichment_paused") is False
    assert run.quota_metrics.get("enrichment_calls", 0) > 0

    # --- Processing limits reached (both phases completed under budget) ---
    assert run.processing_state.get("limits_reached") == {
        "website_search": False,
        "enrichment": False,
    }


async def _empty_decisions_response():
    return SimpleNamespace(text=json.dumps({"decisions": []}))
