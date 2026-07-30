"""The live log must be seeded from the newest events, not the oldest.

A viewer derives its stream cursor from the events it loaded. Seeding from the oldest
page pins that cursor near the start of the run, so the server keeps replaying the
backlog, and each replayed event triggers another full refresh — the page never settles.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.db.models import ScrapingEvent, ScrapingExecution, ScrapingExecutionStatus
from app.services.scraping.execution_service import execution_service

TOTAL_EVENTS = 250


async def _execution_with_events(db: AsyncSession, auth: AuthContext) -> ScrapingExecution:
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
    )
    db.add(execution)
    await db.flush()
    db.add_all(
        ScrapingEvent(
            execution_id=execution.id,
            sequence_number=sequence,
            event_type="progress",
            message=f"event {sequence}",
            metadata_json={},
        )
        for sequence in range(1, TOTAL_EVENTS + 1)
    )
    await db.flush()
    return execution


@pytest.mark.asyncio
async def test_tail_returns_the_newest_page_in_chronological_order(
    db: AsyncSession, auth: AuthContext
):
    execution = await _execution_with_events(db, auth)

    events = await execution_service.list_events(
        db, auth, execution.id, limit=100, tail=True
    )

    sequences = [event.sequence_number for event in events]
    assert sequences == list(range(151, 251)), "tail must be the newest page, still ascending"
    # This is the number a viewer adopts as its stream cursor.
    assert max(sequences) == TOTAL_EVENTS


@pytest.mark.asyncio
async def test_default_listing_still_starts_from_the_oldest_event(
    db: AsyncSession, auth: AuthContext
):
    execution = await _execution_with_events(db, auth)

    events = await execution_service.list_events(db, auth, execution.id, limit=100)

    sequences = [event.sequence_number for event in events]
    assert sequences == list(range(1, 101))


@pytest.mark.asyncio
async def test_tail_respects_the_after_sequence_cursor(db: AsyncSession, auth: AuthContext):
    execution = await _execution_with_events(db, auth)

    events = await execution_service.list_events(
        db, auth, execution.id, after_sequence=240, limit=100, tail=True
    )

    assert [event.sequence_number for event in events] == list(range(241, 251))


@pytest.mark.asyncio
async def test_tail_returns_everything_when_the_run_is_shorter_than_the_limit(
    db: AsyncSession, auth: AuthContext
):
    execution = await _execution_with_events(db, auth)

    events = await execution_service.list_events(
        db, auth, execution.id, limit=1000, tail=True
    )

    assert len(events) == TOTAL_EVENTS
    assert events[0].sequence_number == 1
    assert events[-1].sequence_number == TOTAL_EVENTS
