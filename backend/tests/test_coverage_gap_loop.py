from __future__ import annotations

from app.db.models import ScrapingCoverageStatus
from app.services.scraping.coverage_gap_loop_service import (
    CoverageGapCell,
    CoverageGapFacility,
    coverage_gap_loop_service,
)


def test_measurement_groups_coverage_and_publication_gaps_by_bucket():
    measurement = coverage_gap_loop_service.measure(
        coverage_cells=[
            CoverageGapCell(
                id="cell-1",
                region_name="Vienna",
                language_name="German",
                source_category="official registry",
                status=ScrapingCoverageStatus.FAILED.value,
            ),
            CoverageGapCell(
                id="cell-2",
                region_name="Vienna",
                language_name="German",
                source_category="official registry",
                status=ScrapingCoverageStatus.BLOCKED.value,
            ),
        ],
        facilities=[
            CoverageGapFacility(
                facility_id="fac-1",
                region_name="Vienna",
                language_name="German",
                source_category="official registry",
                location_gap_reason="location_missing",
                country_uncertain=False,
            ),
            CoverageGapFacility(
                facility_id="fac-2",
                region_name="Vienna",
                language_name="German",
                source_category="official registry",
                location_gap_reason="phone_missing",
                country_uncertain=True,
            ),
        ],
    )

    assert measurement.total_coverage_gaps == 2
    assert measurement.location_missing == 1
    assert measurement.phone_missing == 1
    assert measurement.country_uncertain == 1
    assert len(measurement.buckets) == 1
    assert measurement.buckets[0].coverage_gap_count == 2


def test_plan_round_prefers_coverage_then_contact_retries_within_budget():
    measurement = coverage_gap_loop_service.measure(
        coverage_cells=[
            CoverageGapCell(
                id="cell-1",
                region_name="Vienna",
                language_name="German",
                source_category="official registry",
                status=ScrapingCoverageStatus.FAILED.value,
            ),
            CoverageGapCell(
                id="cell-2",
                region_name="Linz",
                language_name="German",
                source_category="directory",
                status=ScrapingCoverageStatus.BLOCKED.value,
            ),
        ],
        facilities=[
            CoverageGapFacility(
                facility_id="fac-1",
                region_name="Vienna",
                language_name="German",
                source_category="official registry",
                location_gap_reason="phone_missing",
                country_uncertain=False,
            ),
            CoverageGapFacility(
                facility_id="fac-2",
                region_name="Linz",
                language_name="German",
                source_category="directory",
                location_gap_reason="location_missing",
                country_uncertain=True,
            ),
        ],
    )

    plan = coverage_gap_loop_service.plan_round(
        measurement,
        mission_profile="full_national_census",
        round_number=1,
        max_rounds=2,
        remaining_budget=3,
        prior_gap_total=None,
    )

    assert plan.stop_reason is None
    assert plan.coverage_attempted is True
    assert plan.contact_attempted is True
    assert len(plan.coverage_retry_cell_ids) == 2
    assert len(plan.contact_retry_facility_ids) == 1


def test_plan_round_stops_when_budget_is_exhausted():
    measurement = coverage_gap_loop_service.measure(
        coverage_cells=[
            CoverageGapCell(
                id="cell-1",
                region_name="Vienna",
                language_name="German",
                source_category="official registry",
                status=ScrapingCoverageStatus.FAILED.value,
            )
        ],
        facilities=[],
    )

    plan = coverage_gap_loop_service.plan_round(
        measurement,
        mission_profile="private_residential",
        round_number=1,
        max_rounds=2,
        remaining_budget=0,
        prior_gap_total=None,
    )

    assert plan.stop_reason == "budget_exhausted"
    assert plan.coverage_retry_cell_ids == []
    assert plan.contact_retry_facility_ids == []


def test_plan_round_stops_for_low_yield_when_no_actionable_follow_ups():
    measurement = coverage_gap_loop_service.measure(
        coverage_cells=[],
        facilities=[],
    )

    plan = coverage_gap_loop_service.plan_round(
        measurement,
        mission_profile="full_national_census",
        round_number=1,
        max_rounds=2,
        remaining_budget=4,
        prior_gap_total=3,
    )

    assert plan.stop_reason == "low_yield"
    assert plan.coverage_attempted is False
    assert plan.contact_attempted is False
