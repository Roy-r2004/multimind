"""An operator's explicit recover gives a failed run a fresh automatic-recovery
budget, so one more Gosom stall does not fail it again straight away."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.db.models import MapsCensusRun, MapsCensusStatus
from app.services.scraping.maps_admin_service import maps_admin_service
from app.services.scraping.maps_census_service import maps_census_service


@pytest.mark.asyncio
async def test_recover_resets_the_auto_resume_budget(db, auth, monkeypatch):
    monkeypatch.setattr(get_settings(), "maps_census_admin_ui_enabled", True)
    enqueued: list[str] = []

    async def fake_enqueue(run_id: str) -> None:
        enqueued.append(run_id)

    monkeypatch.setattr(maps_census_service, "_enqueue", fake_enqueue)
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="NO",
        country_name="Norway",
        state_name="Oslo",
        discovery_provider="gosom",
        status=MapsCensusStatus.FAILED,
        error_message="Discovery could not make progress after 5 automatic recovery attempts",
        processing_state={"discovery_auto_resume_count": 5, "keep_drop_status": "pending"},
    )
    db.add(run)
    await db.commit()

    response = await maps_admin_service.recover_run(db, auth, run.id)

    await db.refresh(run)
    assert response.status == MapsCensusStatus.RUNNING.value
    assert run.status == MapsCensusStatus.RUNNING
    assert run.error_message is None
    assert run.processing_state["discovery_auto_resume_count"] == 0
    assert run.processing_state["keep_drop_status"] == "pending"
    assert enqueued == [run.id]
