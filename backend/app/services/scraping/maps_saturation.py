"""Pure helpers for Maps census region saturation and campaign stop/expand rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.db.models import MapsRegionSaturationStatus


@dataclass(frozen=True)
class CellWindowResult:
    new_unique_places: int
    new_plausible_places: int


class _CellWindowLike(Protocol):
    new_unique_places: int
    new_plausible_places: int


@dataclass(frozen=True)
class RegionWindowMetrics:
    new_unique_places: int
    new_plausible_providers: int
    cells_in_window: int
    duplicate_rate: float | None = None


@dataclass(frozen=True)
class SaturationDecision:
    status: str
    should_expand: bool
    reason: str


def _coerce_cell_result(item: CellWindowResult | Mapping[str, Any] | _CellWindowLike) -> CellWindowResult:
    if isinstance(item, CellWindowResult):
        return item
    if isinstance(item, Mapping):
        return CellWindowResult(
            new_unique_places=int(item.get("new_unique_places", 0)),
            new_plausible_places=int(item.get("new_plausible_places", 0)),
        )
    return CellWindowResult(
        new_unique_places=int(item.new_unique_places),
        new_plausible_places=int(item.new_plausible_places),
    )


def compute_window_metrics(
    cell_results: Sequence[CellWindowResult | Mapping[str, Any] | _CellWindowLike],
    *,
    saturation_window: int,
) -> RegionWindowMetrics:
    """Aggregate metrics from the most recent completed cells in a region."""
    if saturation_window <= 0:
        window = list(cell_results)
    else:
        window = list(cell_results[-saturation_window:])

    new_unique = 0
    new_plausible = 0
    for item in window:
        cell = _coerce_cell_result(item)
        new_unique += max(0, cell.new_unique_places)
        new_plausible += max(0, cell.new_plausible_places)

    return RegionWindowMetrics(
        new_unique_places=new_unique,
        new_plausible_providers=new_plausible,
        cells_in_window=len(window),
    )


def decide_region_saturation(
    *,
    metrics: RegionWindowMetrics,
    cells_completed_total: int,
    cells_planned_total: int,
    campaign_cells_used: int,
    max_cells_per_campaign: int,
    min_new_unique_for_expansion: int,
    min_new_plausible_for_expansion: int,
    saturation_window: int,
) -> SaturationDecision:
    """Decide whether a region should keep expanding or stop."""
    _ = cells_completed_total, cells_planned_total  # reserved for Task 4 reporting

    if campaign_cells_used >= max_cells_per_campaign:
        return SaturationDecision(
            status=MapsRegionSaturationStatus.CAPPED.value,
            should_expand=False,
            reason="campaign cell ceiling reached",
        )

    window_full = metrics.cells_in_window >= saturation_window
    productive = (
        metrics.new_unique_places >= min_new_unique_for_expansion
        or metrics.new_plausible_providers >= min_new_plausible_for_expansion
    )

    if window_full and not productive:
        return SaturationDecision(
            status=MapsRegionSaturationStatus.SATURATED.value,
            should_expand=False,
            reason="window full with insufficient new unique or plausible places",
        )

    if productive:
        return SaturationDecision(
            status=MapsRegionSaturationStatus.EXPANDING.value,
            should_expand=True,
            reason="recent cells show productive discovery",
        )

    # Partial window: keep expanding until the sliding window is full before
    # declaring the region unproductive (saturation requires a full window).
    return SaturationDecision(
        status=MapsRegionSaturationStatus.PENDING.value,
        should_expand=True,
        reason="insufficient window data; continue expanding",
    )


def should_stop_campaign(
    *,
    campaign_cells_used: int,
    max_cells: int,
    all_regions_terminal: bool,
) -> bool:
    """Return True when the campaign hits its cell ceiling or every region is terminal."""
    if campaign_cells_used >= max_cells:
        return True
    return all_regions_terminal
