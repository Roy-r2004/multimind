"""Tests for the Phase 2 country discovery profile stage.

Mirrors ``maps_place_enrichment_service``'s Sonar call pattern: an LLM with live
web search discovers local addiction-treatment provider vocabulary for a
country (region names, languages, query-family search terms) before the Maps
grid planner runs. Country-agnostic by design — no France/CSAPA special
casing anywhere in the service or prompt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.dependencies import AuthContext
from app.db.models import MapsCensusRun, MapsCensusStatus, MapsCountryProfileStatus
from app.services.scraping.maps_census_service import maps_census_service
from app.services.scraping.maps_country_profile_service import (
    MapsCountryDiscoveryProfile,
    _normalize_profile_payload,
    maps_country_profile_service,
)
from app.services.scraping.maps_grid_planner import MapsGridCell


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeProvider:
    def __init__(self, payload_json: str) -> None:
        self._payload_json = payload_json

    async def complete(self, **_kwargs) -> _FakeResponse:
        return _FakeResponse(self._payload_json)


class _ExplodingProvider:
    async def complete(self, **_kwargs):
        raise RuntimeError("sonar offline")


class _FakeProviderRegistry:
    def __init__(self, provider) -> None:
        self._provider = provider

    def get_provider(self, _name: str):
        return self._provider


async def _create_run(db, auth: AuthContext, *, status=MapsCensusStatus.RUNNING) -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="GE",
        country_name="Georgia",
        status=status,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run


def _fixture_profile_payload() -> dict:
    return {
        "administrative_regions": [
            {"name": "Tbilisi", "importance": "capital"},
            "Adjara",
        ],
        "languages": ["Georgian", "English"],
        "provider_terms": {
            "generic": ["rehabilitation center", "sarealibitatsio tsentri"],
            "residential": ["inpatient rehab"],
            "outpatient": ["outpatient program"],
            "detox": ["detoxification unit"],
            "association": ["addiction support NGO"],
            "acronym": [],
        },
        "query_families": ["generic", "residential", "outpatient", "detox", "association"],
        "common_acronyms": [],
        "directory_hints": [],
        "notes": "Local NGOs often use rehabilitation terminology directly.",
    }


# ---------------------------------------------------------------------------
# 1. Payload normalization / parsing
# ---------------------------------------------------------------------------


def test_normalize_profile_payload_produces_expected_shape():
    profile = MapsCountryDiscoveryProfile.model_validate(
        _normalize_profile_payload(_fixture_profile_payload())
    )

    assert [region.name for region in profile.administrative_regions] == ["Tbilisi", "Adjara"]
    assert profile.administrative_regions[0].importance == "capital"
    assert profile.administrative_regions[1].importance is None
    assert profile.languages == ["Georgian", "English"]
    assert profile.provider_terms["outpatient"] == ["outpatient program"]
    assert profile.provider_terms["detox"] == ["detoxification unit"]
    assert set(profile.query_families) == {
        "generic",
        "residential",
        "outpatient",
        "detox",
        "association",
    }
    assert profile.notes.startswith("Local NGOs")


def test_normalize_profile_payload_handles_missing_and_malformed_fields():
    profile = MapsCountryDiscoveryProfile.model_validate(_normalize_profile_payload({}))
    assert profile.administrative_regions == []
    assert profile.languages == []
    assert profile.provider_terms == {}
    assert profile.query_families == []

    profile2 = MapsCountryDiscoveryProfile.model_validate(_normalize_profile_payload("not a dict"))
    assert profile2.administrative_regions == []


# ---------------------------------------------------------------------------
# 2. Successful build_profile_for_run stores completed profile on the run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_profile_for_run_stores_completed_profile(db, auth, monkeypatch):
    run = await _create_run(db, auth)
    payload = _fixture_profile_payload()

    monkeypatch.setattr(
        "app.services.scraping.maps_country_profile_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="perplexity/sonar-pro"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_country_profile_service.get_provider_registry",
        lambda: _FakeProviderRegistry(_FakeProvider(json.dumps(payload))),
    )

    result = await maps_country_profile_service.build_profile_for_run(db, run_id=run.id)

    assert result is not None
    assert set(result["query_families"]) == set(payload["query_families"])

    await db.refresh(run)
    assert run.country_profile_status == MapsCountryProfileStatus.COMPLETED.value
    assert run.country_profile is not None
    assert run.country_profile["languages"] == ["Georgian", "English"]
    assert run.country_profile_error is None


# ---------------------------------------------------------------------------
# 3. Provider failure sets failed status + error without raising
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_profile_for_run_sets_failed_status_without_raising(db, auth, monkeypatch):
    run = await _create_run(db, auth)

    monkeypatch.setattr(
        "app.services.scraping.maps_country_profile_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="perplexity/sonar-pro"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_country_profile_service.get_provider_registry",
        lambda: _FakeProviderRegistry(_ExplodingProvider()),
    )

    result = await maps_country_profile_service.build_profile_for_run(db, run_id=run.id)

    assert result is None
    await db.refresh(run)
    assert run.country_profile_status == MapsCountryProfileStatus.FAILED.value
    assert run.country_profile_error
    assert run.country_profile is None


@pytest.mark.asyncio
async def test_build_profile_for_run_sets_failed_status_on_malformed_json(db, auth, monkeypatch):
    run = await _create_run(db, auth)

    monkeypatch.setattr(
        "app.services.scraping.maps_country_profile_service.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="perplexity/sonar-pro"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_country_profile_service.get_provider_registry",
        lambda: _FakeProviderRegistry(_FakeProvider("not json at all")),
    )

    result = await maps_country_profile_service.build_profile_for_run(db, run_id=run.id)

    assert result is None
    await db.refresh(run)
    assert run.country_profile_status == MapsCountryProfileStatus.FAILED.value
    assert run.country_profile_error


# ---------------------------------------------------------------------------
# 4. No France-specific (or other) hardcoded literals anywhere in the service/prompt
# ---------------------------------------------------------------------------


def test_no_hardcoded_country_literals_in_profile_service_or_prompt():
    backend_dir = Path(__file__).resolve().parents[1]
    service_path = (
        backend_dir / "app" / "services" / "scraping" / "maps_country_profile_service.py"
    )
    prompt_path = backend_dir / "app" / "prompts" / "scraping" / "maps_country_profile.j2"

    assert service_path.is_file()
    assert prompt_path.is_file()

    forbidden_patterns = [r"\bFrance\b", r"\bFrench\b", r"\bCSAPA\b", r"\b500\b"]
    for path in (service_path, prompt_path):
        content = path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            assert not re.search(pattern, content, flags=re.IGNORECASE), (
                f"forbidden literal matching {pattern!r} found in {path}"
            )
    # Country name may only appear as the Jinja template variable, never inlined.
    prompt_content = prompt_path.read_text(encoding="utf-8")
    assert "{{ country_name }}" in prompt_content


# ---------------------------------------------------------------------------
# 5. run_census builds the profile and persists it before planning grid cells
# ---------------------------------------------------------------------------


class _OrderTrackingGridPlanner:
    def __init__(self, cells: list[MapsGridCell], order: list[str]) -> None:
        self._cells = cells
        self._order = order

    async def plan(self, **_kwargs) -> list[MapsGridCell]:
        self._order.append("grid")
        return self._cells


class _NoOpPlacesClient:
    def is_configured(self) -> bool:
        return True

    async def search_text(self, *, query: str, region_code: str, max_results: int) -> list:
        return []


class _FakeCountryProfileService:
    def __init__(self, order: list[str]) -> None:
        self._order = order

    async def build_profile_for_run(self, db, *, run_id: str):
        self._order.append("profile")
        run = await db.get(MapsCensusRun, run_id)
        if run is None:
            return None
        run.country_profile = {"query_families": ["generic"]}
        run.country_profile_status = MapsCountryProfileStatus.COMPLETED.value
        run.country_profile_error = None
        await db.commit()
        return run.country_profile


@pytest.mark.asyncio
async def test_run_census_builds_country_profile_before_planning_grid(db, auth, monkeypatch):
    run = await _create_run(db, auth, status=MapsCensusStatus.QUEUED)
    order: list[str] = []

    cells = [MapsGridCell(region_name="Tbilisi Region", city_name="Tbilisi", query_text="rehab Tbilisi")]

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_country_profile_service",
        _FakeCountryProfileService(order),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _OrderTrackingGridPlanner(cells, order),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _NoOpPlacesClient(),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    # Profile stage runs before the seed grid-planning call; the fake keeps
    # returning the same single cell, so the adaptive loop's one expansion
    # attempt (before dedup discards the repeat) adds a second "grid" call.
    assert order[0] == "profile"
    assert order.count("grid") >= 1

    await db.refresh(run)
    assert run.status == MapsCensusStatus.COMPLETED
    assert run.country_profile_status == MapsCountryProfileStatus.COMPLETED.value
    assert run.country_profile == {"query_families": ["generic"]}


@pytest.mark.asyncio
async def test_run_census_continues_when_profile_stage_raises_unexpectedly(db, auth, monkeypatch):
    """The wiring in ``run_census`` must never let a profiling bug fail the whole census."""
    run = await _create_run(db, auth, status=MapsCensusStatus.QUEUED)

    cells = [MapsGridCell(region_name="Tbilisi Region", city_name="Tbilisi", query_text="rehab Tbilisi")]

    class _BrokenProfileService:
        async def build_profile_for_run(self, _db, *, run_id: str):
            raise RuntimeError("boom")

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_country_profile_service",
        _BrokenProfileService(),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner",
        _OrderTrackingGridPlanner(cells, []),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client",
        lambda: _NoOpPlacesClient(),
    )

    summary = await maps_census_service.run_census(db, run_id=run.id)
    assert summary.get("error") is None

    await db.refresh(run)
    assert run.status == MapsCensusStatus.COMPLETED
    # The fake swaps out the whole profile service, so build_profile_for_run's
    # own status-setting code (PENDING -> COMPLETED/FAILED) never runs here —
    # country_profile_status is left at its untouched default and the profile
    # itself stays unset. That's expected for this fake; real failure-status
    # behavior is covered by test_build_profile_for_run_sets_failed_status_*
    # above against the real service.
    assert run.country_profile_status == MapsCountryProfileStatus.PENDING.value
    assert run.country_profile is None
    # Grid planning still ran and cells were created despite the profile
    # stage exploding — the guard in run_census must never block planning.
    assert run.cells_total == len(cells)
