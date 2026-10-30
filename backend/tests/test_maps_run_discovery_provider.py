"""Each Maps census run chooses its own discovery provider."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.core.exceptions import ValidationError
from app.db.models import MapsCensusRun
from app.services.scraping.maps_census_service import maps_census_service
from app.services.scraping.maps_country_profile_service import maps_country_profile_service
from app.services.scraping.maps_grid_planner import MapsGridCell
from app.services.scraping.maps_places_client import resolve_discovery_provider


@pytest.fixture(autouse=True)
def _no_background_work(monkeypatch):
    async def no_enqueue(*_args, **_kwargs):
        return None

    async def no_hero(*_args, **_kwargs):
        return None

    monkeypatch.setattr(maps_census_service, "_enqueue", no_enqueue)
    monkeypatch.setattr(maps_census_service, "_fetch_hero_image", no_hero)
    monkeypatch.setattr(get_settings(), "maps_discovery_provider", "google_places")
    monkeypatch.setattr(get_settings(), "google_places_enabled", True)
    monkeypatch.setattr(get_settings(), "gosom_base_url", "http://gosom:8080")


@pytest.mark.asyncio
async def test_run_stores_the_chosen_provider(db, auth):
    detail = await maps_census_service.create_run(
        db, auth, country_code="NO", state_name="Oslo", discovery_provider="gosom"
    )
    run = await db.get(MapsCensusRun, detail.id)
    assert run.discovery_provider == "gosom"
    assert detail.discovery_provider == "gosom"


@pytest.mark.asyncio
async def test_run_without_a_choice_uses_the_setting(db, auth):
    detail = await maps_census_service.create_run(db, auth, country_code="NO")
    run = await db.get(MapsCensusRun, detail.id)
    assert run.discovery_provider == "google_places"
    assert detail.discovery_provider == "google_places"


@pytest.mark.asyncio
async def test_unknown_provider_is_rejected(db, auth):
    with pytest.raises(ValidationError):
        await maps_census_service.create_run(
            db, auth, country_code="NO", discovery_provider="bing"
        )


@pytest.mark.asyncio
async def test_gosom_run_requires_a_gosom_url(db, auth, monkeypatch):
    monkeypatch.setattr(get_settings(), "gosom_base_url", "")
    with pytest.raises(ValidationError):
        await maps_census_service.create_run(
            db, auth, country_code="NO", discovery_provider="gosom"
        )


def test_runs_created_before_the_column_fall_back_to_the_setting(monkeypatch):
    monkeypatch.setattr(get_settings(), "maps_discovery_provider", "gosom")
    assert resolve_discovery_provider(SimpleNamespace(discovery_provider=None)) == "gosom"
    assert (
        resolve_discovery_provider(SimpleNamespace(discovery_provider="google_places"))
        == "google_places"
    )


class _FakeGridPlanner:
    async def plan(self, **_kwargs) -> list[MapsGridCell]:
        return [MapsGridCell(region_name="Oslo", city_name="Oslo", query_text="rehab Oslo")]


@pytest.mark.asyncio
async def test_run_census_builds_the_client_for_the_runs_provider(db, auth, monkeypatch):
    # Never reach a live model: no country profile, no AI geo planner, and a
    # fixed grid.
    async def no_profile(_db, *, run_id: str):
        return None

    monkeypatch.setattr(maps_country_profile_service, "build_profile_for_run", no_profile)
    monkeypatch.setattr(get_settings(), "maps_geo_ai_planner_enabled", False)
    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.maps_grid_planner", _FakeGridPlanner()
    )
    detail = await maps_census_service.create_run(
        db, auth, country_code="NO", discovery_provider="gosom"
    )
    requested: list[str | None] = []

    class _Unconfigured:
        name = "gosom"

        def is_configured(self) -> bool:
            return False

    def fake_factory(provider=None):
        requested.append(provider)
        return _Unconfigured()

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.create_places_client", fake_factory
    )
    await maps_census_service.run_census(db, run_id=detail.id)

    assert requested == ["gosom"]
    run = await db.get(MapsCensusRun, detail.id)
    await db.refresh(run)
    assert run.error_message == "Gosom scraper is not configured (GOSOM_BASE_URL)."
