"""Unit tests for Maps census grid payload normalization/dedupe (no live LLM)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.scraping.maps_grid_planner import (
    MapsGridCell,
    _dedupe_cells,
    _known_family_keys,
    _normalize_grid_payload,
    maps_grid_planner,
)


def test_normalize_grid_payload_accepts_cells_or_grid_key():
    payload = _normalize_grid_payload(
        {
            "cells": [
                {"region_name": "Minsk Region", "city_name": "Minsk", "query_text": "rehab Minsk"},
                {"region": "Brest Region", "city": "Brest", "query": "наркология Брест"},
            ]
        }
    )
    assert len(payload["cells"]) == 2
    assert payload["cells"][0]["region_name"] == "Minsk Region"
    assert payload["cells"][1]["region_name"] == "Brest Region"
    assert payload["cells"][1]["query_text"] == "наркология Брест"


def test_normalize_grid_payload_ignores_non_dict_items():
    payload = _normalize_grid_payload({"cells": ["not-a-dict", {"region_name": "X", "query_text": "y"}]})
    assert len(payload["cells"]) == 1


def test_dedupe_cells_drops_blank_and_duplicate_queries():
    cells = [
        MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="rehab Minsk"),
        MapsGridCell(region_name="Minsk Region", city_name="Minsk", query_text="Rehab Minsk"),
        MapsGridCell(region_name="", city_name=None, query_text="orphan query"),
        MapsGridCell(region_name="Brest Region", city_name="Brest", query_text="narcology Brest"),
    ]
    result = _dedupe_cells(cells, max_cells=10)
    assert len(result) == 2
    assert {cell.query_text for cell in result} == {"rehab Minsk", "narcology Brest"}


def test_dedupe_cells_respects_max_cells_cap():
    cells = [
        MapsGridCell(region_name="R", city_name="C", query_text=f"query {i}") for i in range(5)
    ]
    result = _dedupe_cells(cells, max_cells=2)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Phase 2 Task 2: planner consumes country_profile / broadens discovery
# ---------------------------------------------------------------------------


def test_known_family_keys_normalizes_casing_from_query_families_and_provider_terms():
    """query_families may retain the LLM's original casing while provider_terms
    keys are already normalized (casefold, underscored) by the profile service —
    the planner must casefold/strip both sides before cross-referencing them.
    """
    profile = {
        "provider_terms": {"outpatient": ["zxq-outpatient-clinic"], "detox": ["detox unit"]},
        "query_families": ["Outpatient", " Detox ", "Residential"],
    }
    keys = _known_family_keys(profile)
    assert keys == {"outpatient", "detox", "residential"}


def test_known_family_keys_returns_none_for_missing_or_empty_profile():
    assert _known_family_keys(None) is None
    assert _known_family_keys({}) is None
    assert _known_family_keys({"provider_terms": {}, "query_families": []}) is None


def test_normalize_grid_payload_populates_query_family_and_language_when_known():
    payload = _normalize_grid_payload(
        {
            "cells": [
                {
                    "region_name": "Minsk Region",
                    "city_name": "Minsk",
                    "query_text": "zxq-outpatient-clinic Minsk",
                    "query_family": "Outpatient",
                    "query_language": "en",
                }
            ]
        },
        known_families={"outpatient", "detox"},
    )
    cell = payload["cells"][0]
    assert cell["query_family"] == "outpatient"
    assert cell["query_language"] == "en"


def test_normalize_grid_payload_drops_query_family_not_in_known_families():
    payload = _normalize_grid_payload(
        {
            "cells": [
                {
                    "region_name": "Minsk Region",
                    "city_name": "Minsk",
                    "query_text": "rehab Minsk",
                    "query_family": "made_up_family",
                }
            ]
        },
        known_families={"outpatient", "detox"},
    )
    assert payload["cells"][0]["query_family"] is None


def test_normalize_grid_payload_keeps_query_family_when_no_known_families_given():
    payload = _normalize_grid_payload(
        {
            "cells": [
                {
                    "region_name": "Minsk Region",
                    "city_name": "Minsk",
                    "query_text": "rehab Minsk",
                    "query_family": "Outpatient",
                }
            ]
        }
    )
    assert payload["cells"][0]["query_family"] == "outpatient"


@pytest.mark.asyncio
async def test_plan_includes_distinctive_profile_provider_term_in_rendered_prompt(monkeypatch):
    """Assertion #1: a distinctive fake provider term from the profile must reach
    the actual prompt/user text sent to the LLM, proving the planner renders the
    profile into the prompt rather than ignoring it.
    """
    captured: dict = {}

    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeProvider:
        async def complete(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse(json.dumps({"cells": []}))

    class _FakeRegistry:
        def get_provider(self, _name: str):
            return _FakeProvider()

    monkeypatch.setattr(
        "app.services.scraping.maps_grid_planner.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="openai/gpt-4.1"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_grid_planner.get_provider_registry",
        lambda: _FakeRegistry(),
    )

    profile = {
        "provider_terms": {"outpatient": ["zxq-outpatient-clinic"]},
        "query_families": ["Outpatient"],
        "languages": ["English"],
        "administrative_regions": [{"name": "Minsk Region"}],
    }

    await maps_grid_planner.plan(
        country_code="BY",
        country_name="Belarus",
        max_cells=10,
        country_profile=profile,
    )

    assert "zxq-outpatient-clinic" in captured["user"]


@pytest.mark.asyncio
async def test_plan_falls_back_to_broad_prompt_when_profile_missing(monkeypatch):
    """When ``country_profile`` is None/empty, plan() must still render (no crash)
    and must not silently omit the fallback framing — covered indirectly by the
    prompt-content test below; this just proves ``plan`` accepts the default.
    """
    captured: dict = {}

    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeProvider:
        async def complete(self, **kwargs):
            captured.update(kwargs)
            return _FakeResponse(json.dumps({"cells": []}))

    class _FakeRegistry:
        def get_provider(self, _name: str):
            return _FakeProvider()

    monkeypatch.setattr(
        "app.services.scraping.maps_grid_planner.get_model",
        lambda _name: SimpleNamespace(provider="openrouter", provider_model="openai/gpt-4.1"),
    )
    monkeypatch.setattr(
        "app.services.scraping.maps_grid_planner.get_provider_registry",
        lambda: _FakeRegistry(),
    )

    cells = await maps_grid_planner.plan(
        country_code="BY", country_name="Belarus", max_cells=10
    )
    assert cells == []
    assert "user" in captured


def test_planner_system_message_and_prompt_drop_private_inpatient_only_framing():
    """Assertion #2: the old non-government/private-inpatient-only exclusion
    language (which forbade outpatient) must be gone, replaced by broad
    discovery language that explicitly covers outpatient/association terms.
    """
    service_path = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "scraping" / "maps_grid_planner.py"
    )
    prompt_path = (
        Path(__file__).resolve().parents[1] / "app" / "prompts" / "scraping" / "maps_grid_planner.j2"
    )
    service_content = service_path.read_text(encoding="utf-8")
    prompt_content = prompt_path.read_text(encoding="utf-8")

    assert "non-government" not in service_content.casefold()
    assert "outpatient-only programs" not in prompt_content.casefold()
    assert "do not use keywords aimed at government" not in prompt_content.casefold()
    assert "do not use keywords for: general psychiatric" not in prompt_content.casefold()

    assert "outpatient" in prompt_content.casefold()
    assert "association" in prompt_content.casefold()
    assert "outpatient" in service_content.casefold() or "outpatient" in prompt_content.casefold()


def test_no_hardcoded_country_literals_in_grid_planner_service_or_prompt():
    """Assertion #3: source scan mirroring Task 1 — no country-specific hardcoding."""
    backend_dir = Path(__file__).resolve().parents[1]
    service_path = backend_dir / "app" / "services" / "scraping" / "maps_grid_planner.py"
    prompt_path = backend_dir / "app" / "prompts" / "scraping" / "maps_grid_planner.j2"

    assert service_path.is_file()
    assert prompt_path.is_file()

    forbidden_patterns = [r"\bFrance\b", r"\bFrench\b", r"\bCSAPA\b"]
    for path in (service_path, prompt_path):
        content = path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            assert not re.search(pattern, content, flags=re.IGNORECASE), (
                f"forbidden literal matching {pattern!r} found in {path}"
            )

    prompt_content = prompt_path.read_text(encoding="utf-8")
    assert "{{ country_name }}" in prompt_content
    assert "{{ country_profile_json }}" in prompt_content
