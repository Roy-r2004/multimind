from __future__ import annotations

from io import BytesIO

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.models import (
    MapsCensusRun,
    MapsCensusStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
    MapsPlaceReviewAction,
)
from app.db.session import get_db
from app.main import create_app
from app.services.scraping.maps_export_service import maps_export_service
from app.services.scraping.maps_census_service import maps_census_service
from tests.conftest import create_other_auth


def _app(db: AsyncSession, auth: AuthContext):
    app = create_app()

    async def override_db():
        yield db

    async def override_auth():
        return auth

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = override_auth
    return app


async def _run(db: AsyncSession, auth: AuthContext, country: str = "Finland"):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FI",
        country_name=country,
        status=MapsCensusStatus.COMPLETED,
        places_found=2,
        places_classified_relevant=2,
        places_with_website=2,
        places_enriched=2,
    )
    db.add(run)
    await db.flush()
    return run


def _place(run_id: str, key: str) -> MapsPlace:
    return MapsPlace(
        run_id=run_id,
        google_place_id=key,
        raw_name=key,
        canonical_name=key,
        is_relevant=True,
        keep_drop_decision="keep",
        client_eligibility="eligible",
        international_phone_number="+358 1 234 567",
        official_website=f"https://{key}.example",
        enrichment_status=MapsPlaceEnrichmentStatus.COMPLETED.value,
    )


@pytest.mark.asyncio
async def test_delete_place_removes_row_dependents_counts_and_exports(
    db: AsyncSession, auth: AuthContext
):
    run = await _run(db, auth)
    deleted = _place(run.id, "Delete Me")
    kept = _place(run.id, "Keep Me")
    db.add_all([deleted, kept])
    await db.flush()
    db.add(
        MapsPlaceReviewAction(
            place_id=deleted.id,
            run_id=run.id,
            reviewer_user_id=auth.user.id,
            action="mark_eligible",
            reason="test",
        )
    )
    await db.commit()

    async with AsyncClient(transport=ASGITransport(app=_app(db, auth)), base_url="http://test") as client:
        response = await client.delete(f"/api/v1/maps/runs/{run.id}/places/{deleted.id}")

    assert response.status_code == 204
    assert await db.get(MapsPlace, deleted.id) is None
    assert await db.get(MapsPlace, kept.id) is not None
    assert (await db.execute(select(MapsPlaceReviewAction))).scalars().all() == []
    refreshed_run = await db.get(MapsCensusRun, run.id)
    assert refreshed_run is not None
    assert (
        refreshed_run.places_found,
        refreshed_run.places_classified_relevant,
        refreshed_run.places_with_website,
        refreshed_run.places_enriched,
    ) == (2, 1, 1, 1)

    for scope in ("phase1", "phase2"):
        content, _ = await maps_export_service.build_workbook(db, auth, run.id, scope=scope)
        sheet = load_workbook(BytesIO(content)).active
        assert sheet.max_row == 2
        assert sheet.cell(row=2, column=1).value == "Keep Me"


@pytest.mark.asyncio
async def test_delete_place_rejects_other_run_and_other_organization(
    db: AsyncSession, auth: AuthContext
):
    run = await _run(db, auth)
    other_run = await _run(db, auth, "Sweden")
    place = _place(run.id, "Protected")
    other_place = _place(other_run.id, "Other Run")
    db.add_all([place, other_place])
    await db.commit()

    async with AsyncClient(transport=ASGITransport(app=_app(db, auth)), base_url="http://test") as client:
        wrong_run = await client.delete(
            f"/api/v1/maps/runs/{other_run.id}/places/{place.id}"
        )
    assert wrong_run.status_code == 404

    other_auth = await create_other_auth(db)
    await db.commit()
    async with AsyncClient(
        transport=ASGITransport(app=_app(db, other_auth)), base_url="http://test"
    ) as client:
        wrong_org = await client.delete(f"/api/v1/maps/runs/{run.id}/places/{place.id}")
    assert wrong_org.status_code == 404
    assert await db.get(MapsPlace, place.id) is not None
    assert await db.get(MapsPlace, other_place.id) is not None


@pytest.mark.asyncio
async def test_delete_place_rolls_back_all_changes_when_commit_fails(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    run = await _run(db, auth)
    place = _place(run.id, "Rollback")
    db.add(place)
    await db.flush()
    review = MapsPlaceReviewAction(
        place_id=place.id,
        run_id=run.id,
        reviewer_user_id=auth.user.id,
        action="mark_eligible",
        reason="test",
    )
    db.add(review)
    await db.commit()
    place_id = place.id
    review_id = review.id
    run_id = run.id

    async def fail_commit():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await maps_census_service.delete_place(db, auth, run_id, place_id)
    await db.rollback()

    assert await db.get(MapsPlace, place_id) is not None
    assert await db.get(MapsPlaceReviewAction, review_id) is not None
    refreshed_run = await db.get(MapsCensusRun, run_id)
    assert refreshed_run is not None
    assert refreshed_run.places_classified_relevant == 2
