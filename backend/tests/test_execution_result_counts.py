"""Mission-level facility counts must reflect the roster after AI cleanup.

The execution row carries publish-time counters that are frozen before cleanup runs, so
headline counts have to be derived from the facilities themselves. Otherwise a mission
advertises facilities that the roster deliberately hides.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.db.models import RehabilitationFacility, ScrapingExecution, ScrapingExecutionStatus
from app.services.scraping.execution_service import execution_service
from app.services.scraping.result_metrics import result_counts


class _Facility:
    def __init__(self, publication_class: str) -> None:
        self.publication_class = publication_class


def test_result_counts_reports_kept_as_verified_plus_review():
    counts = result_counts(
        [
            _Facility("verified"),
            _Facility("review_required"),
            _Facility("review_required"),
            _Facility("excluded"),
        ]
    )

    assert counts == {"verified": 1, "review": 2, "excluded": 1, "kept": 3}


def test_result_counts_of_an_empty_roster_is_all_zero():
    assert result_counts([]) == {"verified": 0, "review": 0, "excluded": 0, "kept": 0}


async def _execution_with_facilities(
    db: AsyncSession,
    auth: AuthContext,
    *,
    published: int,
    excluded: int,
) -> ScrapingExecution:
    execution = ScrapingExecution(
        organization_id=auth.org_id,
        mission_id="mission-1",
        blueprint_id="blueprint-1",
        team_plan_id="team-plan-1",
        execution_type="initial_full_country",
        mode="real",
        status=ScrapingExecutionStatus.COMPLETED,
        country_code="FI",
        country_name="Finland",
        # Frozen at publish time, before cleanup excluded anything.
        records_verified=published + excluded,
    )
    db.add(execution)
    await db.flush()
    for index in range(published + excluded):
        db.add(
            RehabilitationFacility(
                organization_id=auth.org_id,
                execution_id=execution.id,
                stable_key=f"facility-{index}",
                canonical_name=f"Facility {index}",
                country_code="FI",
                country_name="Finland",
                facility_type="clinic",
                organization_type="private",
                operational_status="operational",
                verification_status="review_required",
                confidence_score=0.9,
                duplicate_status="unique",
                human_review_status="required",
                publication_class="excluded" if index < excluded else "review_required",
            )
        )
    await db.flush()
    return execution


@pytest.mark.asyncio
async def test_counts_by_execution_ignores_the_stale_publish_counter(
    db: AsyncSession, auth: AuthContext
):
    execution = await _execution_with_facilities(db, auth, published=7, excluded=3)

    counts = await execution_service._result_counts_by_execution(db, [execution.id])

    assert counts[execution.id]["kept"] == 7
    assert counts[execution.id]["excluded"] == 3
    # The row still advertises the pre-cleanup total, which is exactly why we recount.
    assert execution.records_verified == 10


@pytest.mark.asyncio
async def test_counts_by_execution_returns_nothing_for_unknown_ids(
    db: AsyncSession, auth: AuthContext
):
    assert await execution_service._result_counts_by_execution(db, []) == {}
    assert await execution_service._result_counts_by_execution(db, ["missing"]) == {}
