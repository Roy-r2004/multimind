"""Unit tests for the optional AI geo-planning layer.

No live model calls — the provider's ``complete`` is monkeypatched. These
tests exercise: disabled-by-default skip, strict schema stripping of any
unexpected (e.g. coordinate-like) field, invalid/non-JSON output handled
as a skip rather than a crash, and disk-cache reuse avoiding a second
call for the same geography+model.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.services.scraping import maps_geo_planner_service as planner


@pytest.fixture
def planner_cache_dir(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_geo_boundary_cache_dir", str(tmp_path / "geo_boundaries"))
    monkeypatch.setattr(settings, "maps_geo_ai_planner_enabled", True)
    monkeypatch.setattr(settings, "maps_geo_ai_planner_model", "test-model")
    monkeypatch.setattr(settings, "maps_geo_ai_planner_timeout_seconds", 5.0)
    yield tmp_path


def _fake_model():
    return SimpleNamespace(provider="test-provider", provider_model="test-model-v1")


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


def _patch_provider(monkeypatch, response_text: str, call_counter: list[int]):
    async def fake_complete(**kwargs):
        call_counter.append(1)
        return _FakeResponse(response_text)

    fake_provider = SimpleNamespace(complete=fake_complete)
    monkeypatch.setattr(planner, "get_model", lambda name: _fake_model())
    monkeypatch.setattr(
        planner, "get_provider_registry", lambda: SimpleNamespace(get_provider=lambda p: fake_provider)
    )


async def test_planner_disabled_by_default_is_skipped(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_geo_ai_planner_enabled", False)

    result = await planner.plan_geo_strategy(
        country_code="US",
        country_name="United States",
        state_code="AL",
        state_name="Alabama",
        resolved_region_label="Alabama, United States",
    )
    assert result.used_ai is False
    assert result.skipped_reason == "ai_planner_disabled"
    assert result.suggestion is None


async def test_planner_strips_unexpected_fields_like_coordinates(planner_cache_dir, monkeypatch):
    calls: list[int] = []
    _patch_provider(
        monkeypatch,
        '{"priority_areas": ["Metro area"], "latitude": 32.5, "longitude": -86.5,'
        ' "additional_search_terms": ["detox"], "strategy_notes": "focus urban first"}',
        calls,
    )

    result = await planner.plan_geo_strategy(
        country_code="US",
        country_name="United States",
        state_code="AL",
        state_name="Alabama",
        resolved_region_label="Alabama, United States",
    )
    assert result.used_ai is True
    assert result.cached is False
    assert result.suggestion is not None
    assert result.suggestion.priority_areas == ["Metro area"]
    # extra="ignore" means the model never gained a latitude/longitude
    # attribute at all, not merely that we failed to read it back.
    assert not hasattr(result.suggestion, "latitude")
    assert not hasattr(result.suggestion, "longitude")
    assert len(calls) == 1


async def test_planner_invalid_json_response_is_skipped_not_raised(planner_cache_dir, monkeypatch):
    calls: list[int] = []
    _patch_provider(monkeypatch, "not json at all", calls)

    result = await planner.plan_geo_strategy(
        country_code="US",
        country_name="United States",
        state_code="MI",
        state_name="Michigan",
        resolved_region_label="Michigan, United States",
    )
    assert result.used_ai is False
    assert result.suggestion is None
    assert result.skipped_reason is not None
    assert "ai_planner_error" in result.skipped_reason


async def test_planner_reuses_cache_without_a_second_call(planner_cache_dir, monkeypatch):
    calls: list[int] = []
    _patch_provider(
        monkeypatch,
        '{"priority_areas": ["Metro area"]}',
        calls,
    )

    first = await planner.plan_geo_strategy(
        country_code="US",
        country_name="United States",
        state_code="CO",
        state_name="Colorado",
        resolved_region_label="Colorado, United States",
    )
    second = await planner.plan_geo_strategy(
        country_code="US",
        country_name="United States",
        state_code="CO",
        state_name="Colorado",
        resolved_region_label="Colorado, United States",
    )
    assert first.cached is False
    assert second.cached is True
    assert second.suggestion == first.suggestion
    assert len(calls) == 1
