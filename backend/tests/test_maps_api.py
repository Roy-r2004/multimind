"""HTTP-level tests for the standalone Maps census endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.models import MapsCensusRun, MapsCensusStatus, MapsPlace
from app.db.session import get_db
from app.main import create_app


def _client_app(db: AsyncSession, auth: AuthContext):
    app = create_app()

    async def override_db():
        yield db

    async def override_auth():
        return auth

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = override_auth
    return app


@pytest.mark.asyncio
async def test_unauthenticated_maps_run_creation_returns_401(db: AsyncSession):
    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/maps/runs", json={"country_code": "BY"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_and_list_maps_census_runs(db: AsyncSession, auth: AuthContext, monkeypatch):
    async def fake_enqueue(self, run_id: str) -> None:
        return None

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._enqueue", fake_enqueue
    )

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        create_response = await client.post("/api/v1/maps/runs", json={"country_code": "by"})
        assert create_response.status_code == 201
        body = create_response.json()
        assert body["country_code"] == "BY"
        assert body["country_name"] == "Belarus"
        assert body["status"] == "queued"

        list_response = await client.get("/api/v1/maps/runs")
        assert list_response.status_code == 200
        runs = list_response.json()
        assert any(r["id"] == body["id"] for r in runs)

        detail_response = await client.get(f"/api/v1/maps/runs/{body['id']}")
        assert detail_response.status_code == 200
        assert detail_response.json()["id"] == body["id"]


@pytest.mark.asyncio
async def test_create_maps_census_run_rejects_invalid_country(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    async def fake_enqueue(self, run_id: str) -> None:
        return None

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._enqueue", fake_enqueue
    )

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/v1/maps/runs", json={"country_code": "ZZ"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_maps_census_run_not_found_across_orgs(
    db: AsyncSession, auth: AuthContext
):
    from tests.conftest import create_other_auth

    other_auth = await create_other_auth(db)
    run = MapsCensusRun(
        organization_id=other_auth.org_id,
        created_by=other_auth.user.id,
        country_code="BY",
        country_name="Belarus",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/maps/runs/{run.id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_maps_census_places_filters(db: AsyncSession, auth: AuthContext):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="BY",
        country_name="Belarus",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="p1",
            raw_name="Relevant Clinic",
            canonical_name="Relevant Clinic",
            is_relevant=True,
            official_website="https://relevant.example/",
        )
    )
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="p2",
            raw_name="Irrelevant Hotel",
            canonical_name="Irrelevant Hotel",
            is_relevant=False,
        )
    )
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        all_response = await client.get(f"/api/v1/maps/runs/{run.id}/places")
        assert len(all_response.json()) == 2

        relevant_response = await client.get(
            f"/api/v1/maps/runs/{run.id}/places", params={"relevant_only": True}
        )
        places = relevant_response.json()
        assert len(places) == 1
        assert places[0]["google_place_id"] == "p1"


@pytest.mark.asyncio
async def test_delete_maps_census_run(db: AsyncSession, auth: AuthContext):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="BY",
        country_name="Belarus",
        status=MapsCensusStatus.FAILED,
        error_message="Google Places API key is not configured.",
    )
    db.add(run)
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        delete_response = await client.delete(f"/api/v1/maps/runs/{run.id}")
        assert delete_response.status_code == 204

        get_response = await client.get(f"/api/v1/maps/runs/{run.id}")
        assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_maps_census_run_scoped_to_org(db: AsyncSession, auth: AuthContext):
    from tests.conftest import create_other_auth

    other_auth = await create_other_auth(db)
    run = MapsCensusRun(
        organization_id=other_auth.org_id,
        created_by=other_auth.user.id,
        country_code="BY",
        country_name="Belarus",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.delete(f"/api/v1/maps/runs/{run.id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_refresh_websites_requires_completed_run(db: AsyncSession, auth: AuthContext):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="BY",
        country_name="Belarus",
        status=MapsCensusStatus.QUEUED,
    )
    db.add(run)
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/v1/maps/runs/{run.id}/refresh-websites")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_refresh_websites_marks_completed_run_running(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    async def fake_enqueue_refresh(self, run_id: str) -> None:
        return None

    monkeypatch.setattr(
        "app.services.scraping.maps_census_service.MapsCensusService._enqueue_refresh",
        fake_enqueue_refresh,
    )

    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="BY",
        country_name="Belarus",
        status=MapsCensusStatus.COMPLETED,
        places_classified_relevant=5,
        places_with_website=2,
    )
    db.add(run)
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/v1/maps/runs/{run.id}/refresh-websites")
    assert response.status_code == 200
    assert response.json()["status"] == "running"
