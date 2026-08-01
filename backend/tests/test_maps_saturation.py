"""Unit tests for Maps census region saturation rules (Phase 2 Task 3)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.db.models import MapsRegionSaturationStatus
from app.services.scraping.maps_saturation import (
    CellWindowResult,
    RegionWindowMetrics,
    compute_window_metrics,
    decide_region_saturation,
    should_stop_campaign,
)


DEFAULTS = {
    "max_cells_per_campaign": 1500,
    "min_new_unique_for_expansion": 3,
    "min_new_plausible_for_expansion": 1,
    "saturation_window": 10,
}


def _decide(
    *,
    metrics: RegionWindowMetrics,
    campaign_cells_used: int = 0,
    cells_completed_total: int = 0,
    cells_planned_total: int = 0,
    **overrides,
):
    params = {**DEFAULTS, **overrides}
    return decide_region_saturation(
        metrics=metrics,
        cells_completed_total=cells_completed_total,
        cells_planned_total=cells_planned_total,
        campaign_cells_used=campaign_cells_used,
        max_cells_per_campaign=params["max_cells_per_campaign"],
        min_new_unique_for_expansion=params["min_new_unique_for_expansion"],
        min_new_plausible_for_expansion=params["min_new_plausible_for_expansion"],
        saturation_window=params["saturation_window"],
    )


def test_unproductive_full_window_marks_region_saturated():
    metrics = RegionWindowMetrics(
        new_unique_places=0,
        new_plausible_providers=0,
        cells_in_window=10,
    )
    decision = _decide(metrics=metrics, campaign_cells_used=100)

    assert decision.status == MapsRegionSaturationStatus.SATURATED.value
    assert decision.should_expand is False
    assert "insufficient" in decision.reason.casefold() or "unproductive" in decision.reason.casefold()


def test_productive_region_by_unique_places_expands():
    metrics = RegionWindowMetrics(
        new_unique_places=3,
        new_plausible_providers=0,
        cells_in_window=10,
    )
    decision = _decide(metrics=metrics)

    assert decision.status == MapsRegionSaturationStatus.EXPANDING.value
    assert decision.should_expand is True


def test_productive_region_by_plausible_providers_expands():
    metrics = RegionWindowMetrics(
        new_unique_places=0,
        new_plausible_providers=1,
        cells_in_window=10,
    )
    decision = _decide(metrics=metrics)

    assert decision.status == MapsRegionSaturationStatus.EXPANDING.value
    assert decision.should_expand is True


def test_campaign_at_1500_cells_is_capped():
    metrics = RegionWindowMetrics(
        new_unique_places=10,
        new_plausible_providers=5,
        cells_in_window=10,
    )
    decision = _decide(metrics=metrics, campaign_cells_used=1500)

    assert decision.status == MapsRegionSaturationStatus.CAPPED.value
    assert decision.should_expand is False


def test_partial_window_still_expands():
    metrics = RegionWindowMetrics(
        new_unique_places=0,
        new_plausible_providers=0,
        cells_in_window=4,
    )
    decision = _decide(metrics=metrics)

    assert decision.should_expand is True
    assert decision.status in {
        MapsRegionSaturationStatus.PENDING.value,
        MapsRegionSaturationStatus.EXPANDING.value,
    }


def test_compute_window_metrics_sums_last_n_cells():
    cells = [
        CellWindowResult(new_unique_places=1, new_plausible_places=0),
        CellWindowResult(new_unique_places=2, new_plausible_places=1),
        CellWindowResult(new_unique_places=0, new_plausible_places=0),
    ]
    metrics = compute_window_metrics(cells, saturation_window=2)

    assert metrics.cells_in_window == 2
    assert metrics.new_unique_places == 2
    assert metrics.new_plausible_providers == 1


def test_should_stop_campaign_at_ceiling_or_all_regions_terminal():
    assert should_stop_campaign(
        campaign_cells_used=1500,
        max_cells=1500,
        all_regions_terminal=False,
    )
    assert should_stop_campaign(
        campaign_cells_used=100,
        max_cells=1500,
        all_regions_terminal=True,
    )
    assert not should_stop_campaign(
        campaign_cells_used=100,
        max_cells=1500,
        all_regions_terminal=False,
    )


def test_no_hardcoded_country_literals_in_saturation_module():
    module_path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "scraping"
        / "maps_saturation.py"
    )
    content = module_path.read_text(encoding="utf-8")
    for pattern in (r"\bFrance\b", r"\bFrench\b", r"\bCSAPA\b"):
        assert not re.search(pattern, content, flags=re.IGNORECASE), (
            f"forbidden literal matching {pattern!r} found in {module_path}"
        )
