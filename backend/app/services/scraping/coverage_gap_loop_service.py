"""Phase D coverage-gap measurement and bounded follow-up planning."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.scraping.execution_outcome import GAP_COVERAGE_STATUSES


@dataclass(frozen=True)
class CoverageGapCell:
    id: str
    region_name: str
    language_name: str
    source_category: str
    status: str


@dataclass(frozen=True)
class CoverageGapFacility:
    facility_id: str
    region_name: str
    language_name: str
    source_category: str
    location_gap_reason: str | None
    country_uncertain: bool


@dataclass(frozen=True)
class CoverageGapBucket:
    region_name: str
    language_name: str
    source_category: str
    coverage_gap_count: int
    location_missing_count: int
    phone_missing_count: int
    country_uncertain_count: int
    coverage_cell_ids: list[str]
    contact_retry_facility_ids: list[str]


@dataclass(frozen=True)
class CoverageGapMeasurement:
    total_coverage_gaps: int
    location_missing: int
    phone_missing: int
    country_uncertain: int
    buckets: list[CoverageGapBucket]

    @property
    def total_gap_items(self) -> int:
        return self.total_coverage_gaps + self.location_missing + self.phone_missing + self.country_uncertain


@dataclass(frozen=True)
class CoverageGapRoundPlan:
    stop_reason: str | None
    coverage_retry_cell_ids: list[str]
    contact_retry_facility_ids: list[str]
    coverage_attempted: bool
    contact_attempted: bool


class CoverageGapLoopService:
    def measure(
        self,
        *,
        coverage_cells: list[CoverageGapCell],
        facilities: list[CoverageGapFacility],
    ) -> CoverageGapMeasurement:
        grouped: dict[tuple[str, str, str], dict[str, object]] = {}
        total_coverage_gaps = 0
        location_missing = 0
        phone_missing = 0
        country_uncertain = 0

        for cell in coverage_cells:
            if cell.status not in GAP_COVERAGE_STATUSES:
                continue
            total_coverage_gaps += 1
            bucket = grouped.setdefault(
                (cell.region_name, cell.language_name, cell.source_category),
                {
                    "coverage_gap_count": 0,
                    "location_missing_count": 0,
                    "phone_missing_count": 0,
                    "country_uncertain_count": 0,
                    "coverage_cell_ids": [],
                    "contact_retry_facility_ids": [],
                },
            )
            bucket["coverage_gap_count"] = int(bucket["coverage_gap_count"]) + 1
            bucket["coverage_cell_ids"].append(cell.id)

        for facility in facilities:
            bucket = grouped.setdefault(
                (facility.region_name, facility.language_name, facility.source_category),
                {
                    "coverage_gap_count": 0,
                    "location_missing_count": 0,
                    "phone_missing_count": 0,
                    "country_uncertain_count": 0,
                    "coverage_cell_ids": [],
                    "contact_retry_facility_ids": [],
                },
            )
            needs_contact_retry = False
            if facility.location_gap_reason == "location_missing":
                location_missing += 1
                bucket["location_missing_count"] = int(bucket["location_missing_count"]) + 1
                needs_contact_retry = True
            elif facility.location_gap_reason == "phone_missing":
                phone_missing += 1
                bucket["phone_missing_count"] = int(bucket["phone_missing_count"]) + 1
                needs_contact_retry = True
            if facility.country_uncertain:
                country_uncertain += 1
                bucket["country_uncertain_count"] = int(bucket["country_uncertain_count"]) + 1
                needs_contact_retry = True
            if needs_contact_retry:
                bucket["contact_retry_facility_ids"].append(facility.facility_id)

        buckets = [
            CoverageGapBucket(
                region_name=region_name,
                language_name=language_name,
                source_category=source_category,
                coverage_gap_count=int(values["coverage_gap_count"]),
                location_missing_count=int(values["location_missing_count"]),
                phone_missing_count=int(values["phone_missing_count"]),
                country_uncertain_count=int(values["country_uncertain_count"]),
                coverage_cell_ids=list(values["coverage_cell_ids"]),
                contact_retry_facility_ids=list(values["contact_retry_facility_ids"]),
            )
            for (region_name, language_name, source_category), values in grouped.items()
        ]
        buckets.sort(
            key=lambda bucket: (
                -(bucket.coverage_gap_count + bucket.location_missing_count + bucket.phone_missing_count + bucket.country_uncertain_count),
                bucket.region_name,
                bucket.language_name,
                bucket.source_category,
            )
        )
        return CoverageGapMeasurement(
            total_coverage_gaps=total_coverage_gaps,
            location_missing=location_missing,
            phone_missing=phone_missing,
            country_uncertain=country_uncertain,
            buckets=buckets,
        )

    def plan_round(
        self,
        measurement: CoverageGapMeasurement,
        *,
        mission_profile: str,
        round_number: int,
        max_rounds: int,
        remaining_budget: int,
        prior_gap_total: int | None,
    ) -> CoverageGapRoundPlan:
        del mission_profile
        if round_number > max_rounds:
            return CoverageGapRoundPlan("max_rounds_reached", [], [], False, False)
        if remaining_budget <= 0:
            return CoverageGapRoundPlan("budget_exhausted", [], [], False, False)
        if measurement.total_gap_items <= 0:
            return CoverageGapRoundPlan("low_yield", [], [], False, False)
        if prior_gap_total is not None and round_number > 1 and measurement.total_gap_items >= prior_gap_total:
            return CoverageGapRoundPlan("low_yield", [], [], False, False)

        coverage_retry_cell_ids: list[str] = []
        contact_retry_facility_ids: list[str] = []
        budget = remaining_budget

        for bucket in measurement.buckets:
            for cell_id in bucket.coverage_cell_ids:
                if budget <= 0:
                    break
                coverage_retry_cell_ids.append(cell_id)
                budget -= 1
            if budget <= 0:
                break

        for bucket in measurement.buckets:
            for facility_id in bucket.contact_retry_facility_ids:
                if budget <= 0:
                    break
                if facility_id in contact_retry_facility_ids:
                    continue
                contact_retry_facility_ids.append(facility_id)
                budget -= 1
            if budget <= 0:
                break

        coverage_attempted = bool(coverage_retry_cell_ids)
        contact_attempted = bool(contact_retry_facility_ids)
        stop_reason = None if (coverage_attempted or contact_attempted) else "low_yield"
        return CoverageGapRoundPlan(
            stop_reason=stop_reason,
            coverage_retry_cell_ids=coverage_retry_cell_ids,
            contact_retry_facility_ids=contact_retry_facility_ids,
            coverage_attempted=coverage_attempted,
            contact_attempted=contact_attempted,
        )


coverage_gap_loop_service = CoverageGapLoopService()
