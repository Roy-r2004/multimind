"""Quality-first comparison helpers for same-country scrape runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class QualityFacilityRecord:
    country_code: str | None
    has_phone: bool
    has_address: bool
    verified_with_both: bool


@dataclass(frozen=True)
class QualityThresholds:
    max_wrong_country_rate: float = 5.0
    min_phone_rate: float = 60.0
    min_address_rate: float = 60.0
    min_verified_with_both_rate: float = 35.0


@dataclass(frozen=True)
class QualitySummary:
    row_count: int
    wrong_country_rate: float
    phone_rate: float
    address_rate: float
    verified_with_both_rate: float
    composite_score: float
    passes_thresholds: bool


@dataclass(frozen=True)
class QualityComparison:
    left_name: str
    right_name: str
    left: QualitySummary
    right: QualitySummary
    winner: str


def summarize_same_country_quality(
    records: Iterable[QualityFacilityRecord],
    *,
    target_country_code: str,
    thresholds: QualityThresholds | None = None,
) -> QualitySummary:
    rows = list(records)
    row_count = len(rows)
    normalized_target = (target_country_code or "").strip().upper()
    wrong_country_count = len(
        [
            row
            for row in rows
            if (row.country_code or "").strip().upper()
            and (row.country_code or "").strip().upper() != normalized_target
        ]
    )
    phone_count = len([row for row in rows if row.has_phone])
    address_count = len([row for row in rows if row.has_address])
    verified_with_both_count = len([row for row in rows if row.verified_with_both])

    wrong_country_rate = _rate(wrong_country_count, row_count)
    phone_rate = _rate(phone_count, row_count)
    address_rate = _rate(address_count, row_count)
    verified_with_both_rate = _rate(verified_with_both_count, row_count)
    composite_score = round(
        (100.0 - wrong_country_rate) * 0.35
        + phone_rate * 0.20
        + address_rate * 0.20
        + verified_with_both_rate * 0.25,
        2,
    )
    effective_thresholds = thresholds or QualityThresholds()
    passes_thresholds = (
        wrong_country_rate <= effective_thresholds.max_wrong_country_rate
        and phone_rate >= effective_thresholds.min_phone_rate
        and address_rate >= effective_thresholds.min_address_rate
        and verified_with_both_rate >= effective_thresholds.min_verified_with_both_rate
    )
    return QualitySummary(
        row_count=row_count,
        wrong_country_rate=wrong_country_rate,
        phone_rate=phone_rate,
        address_rate=address_rate,
        verified_with_both_rate=verified_with_both_rate,
        composite_score=composite_score,
        passes_thresholds=passes_thresholds,
    )


def compare_same_country_quality(
    *,
    left_name: str,
    left_records: Iterable[QualityFacilityRecord],
    right_name: str,
    right_records: Iterable[QualityFacilityRecord],
    target_country_code: str,
    thresholds: QualityThresholds | None = None,
) -> QualityComparison:
    left = summarize_same_country_quality(
        left_records,
        target_country_code=target_country_code,
        thresholds=thresholds,
    )
    right = summarize_same_country_quality(
        right_records,
        target_country_code=target_country_code,
        thresholds=thresholds,
    )
    winner = left_name if _comparison_key(left) >= _comparison_key(right) else right_name
    return QualityComparison(
        left_name=left_name,
        right_name=right_name,
        left=left,
        right=right,
        winner=winner,
    )


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 2)


def _comparison_key(summary: QualitySummary) -> tuple[bool, float, float, float, float, float]:
    return (
        summary.passes_thresholds,
        summary.composite_score,
        summary.verified_with_both_rate,
        summary.phone_rate,
        summary.address_rate,
        -summary.wrong_country_rate,
    )
