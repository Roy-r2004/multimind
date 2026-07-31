"""HTTP-level tests for the standalone Maps census endpoints."""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
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


class _FakePhotoAsyncClient:
    """Minimal httpx.AsyncClient stand-in for the Google Places Photo media call."""

    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.request_count = 0

    def __call__(self, *, timeout: float) -> "_FakePhotoAsyncClient":
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, *, params):
        self.request_count += 1
        return self._response


@pytest.mark.asyncio
async def test_place_photo_404s_without_photo_reference(db: AsyncSession, auth: AuthContext):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="AT",
        country_name="Austria",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="p-no-photo",
        raw_name="No Photo Clinic",
        canonical_name="No Photo Clinic",
        is_relevant=True,
    )
    db.add(place)
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/maps/runs/{run.id}/places/{place.id}/photo")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_place_photo_fetches_and_caches_on_first_request(
    db: AsyncSession, auth: AuthContext, monkeypatch, tmp_path
):
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "test-places-key")
    monkeypatch.setenv("MAPS_CENSUS_PHOTO_CACHE_DIR", str(tmp_path / "maps_photos"))
    get_settings.cache_clear()

    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="AT",
        country_name="Austria",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="p-with-photo",
        raw_name="Photo Clinic",
        canonical_name="Photo Clinic",
        is_relevant=True,
        photo_reference="places/p-with-photo/photos/photo-1",
    )
    db.add(place)
    await db.commit()

    fake_client = _FakePhotoAsyncClient(httpx.Response(200, content=b"\xff\xd8\xff\xd9fakejpeg"))
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.get(f"/api/v1/maps/runs/{run.id}/places/{place.id}/photo")
            assert first.status_code == 200
            assert first.content == b"\xff\xd8\xff\xd9fakejpeg"
            assert fake_client.request_count == 1

            # Second request must be served from disk cache — no second Google call.
            second = await client.get(f"/api/v1/maps/runs/{run.id}/places/{place.id}/photo")
            assert second.status_code == 200
            assert fake_client.request_count == 1
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_place_photo_scoped_to_org(db: AsyncSession, auth: AuthContext):
    from tests.conftest import create_other_auth

    other_auth = await create_other_auth(db)
    run = MapsCensusRun(
        organization_id=other_auth.org_id,
        created_by=other_auth.user.id,
        country_code="AT",
        country_name="Austria",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="p-other-org",
        raw_name="Other Org Clinic",
        canonical_name="Other Org Clinic",
        is_relevant=True,
        photo_reference="places/p-other-org/photos/photo-1",
    )
    db.add(place)
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/maps/runs/{run.id}/places/{place.id}/photo")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_maps_census_cells(db: AsyncSession, auth: AuthContext):
    from app.db.models import MapsCensusCell, MapsCensusStatus

    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="BY",
        country_name="Belarus",
        status=MapsCensusStatus.COMPLETED,
        cells_total=2,
        cells_completed=2,
    )
    db.add(run)
    await db.flush()
    db.add_all(
        [
            MapsCensusCell(
                run_id=run.id,
                region_name="Minsk Region",
                city_name="Minsk",
                query_text="inpatient addiction rehab Minsk Belarus",
                status="completed",
                places_found=12,
            ),
            MapsCensusCell(
                run_id=run.id,
                region_name="Minsk Region",
                city_name="Minsk",
                query_text="наркологическая клиника Минск",
                status="completed",
                places_found=8,
            ),
        ]
    )
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/maps/runs/{run.id}/cells")
    assert response.status_code == 200
    cells = response.json()
    assert len(cells) == 2
    assert cells[0]["query_text"] == "inpatient addiction rehab Minsk Belarus"
    assert cells[1]["places_found"] == 8


@pytest.mark.asyncio
async def test_export_maps_census_run_csv(db: AsyncSession, auth: AuthContext):
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
            google_place_id="export-1",
            raw_name="Export Rehab",
            canonical_name="Export Rehab",
            is_relevant=True,
            confidence_score=0.91,
            formatted_address="10 Export St, Minsk",
            official_website="export.example",
            addictions_treated=["Alcohol"],
            enrichment_status="completed",
        )
    )
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/maps/runs/{run.id}/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert 'filename="by-maps-census-export.csv"' in response.headers["content-disposition"]
    body = response.content.decode("utf-8-sig")
    assert "Facility Name,Addictions Treated,Location" in body.splitlines()[0]
    assert "Export Rehab" in body
    assert "Alcohol" in body
    assert "https://export.example" in body


@pytest.mark.asyncio
async def test_export_maps_census_run_csv_rejects_invalid_tier(db: AsyncSession, auth: AuthContext):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="BY",
        country_name="Belarus",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/maps/runs/{run.id}/export.csv", params={"tier": "maybe"}
        )
    assert response.status_code == 422
