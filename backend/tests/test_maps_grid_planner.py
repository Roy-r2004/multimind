"""Unit tests for Maps census grid payload normalization/dedupe (no live LLM)."""

from __future__ import annotations

from app.services.scraping.maps_grid_planner import (
    MapsGridCell,
    _dedupe_cells,
    _normalize_grid_payload,
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
