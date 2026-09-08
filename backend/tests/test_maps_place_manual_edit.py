"""Manual Phase 2 export-field edits and enrichment override protection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.dependencies import AuthContext, get_auth_context
from app.db.models import MapsCensusRun, MapsCensusStatus, MapsPlace, MapsPlaceEnrichmentStatus
from app.db.session import get_db
from app.main import create_app
from app.services.scraping.maps_detail_enrichment_service import MapsDetailEnrichmentService
from app.services.scraping.maps_enrichment_response_parser import EnrichmentParseStats
from app.services.scraping.maps_quota_tracker import MapsQuotaTracker


def _client_app(db: AsyncSession, auth: AuthContext):
    app = create_app()

    async def override_db():
        yield db

    async def override_auth():
        return auth

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = override_auth
    return app


async def _create_run(db: AsyncSession, auth: AuthContext) -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FI",
        country_name="Finland",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    return run


def _place(run_id: str, key: str = "facility-1", **kwargs) -> MapsPlace:
    payload = dict(
        run_id=run_id,
        google_place_id=key,
        raw_name="Example Rehab",
        canonical_name="Example Rehab",
        formatted_address="1 Main St",
        keep_drop_decision="keep",
        international_phone_number="+358 1",
        official_website="https://example.fi",
    )
    payload.update(kwargs)
    return MapsPlace(**payload)


@pytest.mark.asyncio
async def test_patch_bed_count_null_to_eight(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    place = _place(run.id, bed_count=None)
    db.add(place)
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/api/v1/maps/runs/{run.id}/places/{place.id}",
            json={"bed_count": 8},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["bed_count"] == 8
    refreshed = await db.get(MapsPlace, place.id)
    assert refreshed is not None
    assert refreshed.bed_count == 8
    assert refreshed.manual_field_overrides == ["bed_count"]
    assert refreshed.keep_drop_decision == "keep"


@pytest.mark.asyncio
async def test_patch_bed_count_to_null_keeps_override(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    place = _place(run.id, bed_count=8, manual_field_overrides=["bed_count"])
    db.add(place)
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/api/v1/maps/runs/{run.id}/places/{place.id}",
            json={"bed_count": None},
        )

    assert response.status_code == 200
    assert response.json()["bed_count"] is None
    refreshed = await db.get(MapsPlace, place.id)
    assert refreshed is not None
    assert refreshed.bed_count is None
    assert refreshed.manual_field_overrides == ["bed_count"]


@pytest.mark.asyncio
async def test_patch_multiple_fields_accumulates_overrides(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    place = _place(run.id, bed_count=None, contact_email=None, treatment_price=None)
    db.add(place)
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.patch(
            f"/api/v1/maps/runs/{run.id}/places/{place.id}",
            json={"bed_count": 8},
        )
        second = await client.patch(
            f"/api/v1/maps/runs/{run.id}/places/{place.id}",
            json={
                "contact_email": "desk@example.fi",
                "treatment_price": "€200/day",
                "addictions_treated": ["Alcohol", "Opioids"],
            },
        )

    assert first.status_code == 200
    assert second.status_code == 200
    body = second.json()
    assert body["bed_count"] == 8
    assert body["contact_email"] == "desk@example.fi"
    assert body["treatment_price"] == "€200/day"
    assert body["addictions_treated"] == ["Alcohol", "Opioids"]
    refreshed = await db.get(MapsPlace, place.id)
    assert refreshed is not None
    assert set(refreshed.manual_field_overrides) == {
        "bed_count",
        "contact_email",
        "treatment_price",
        "addictions_treated",
    }


@pytest.mark.asyncio
async def test_patch_rejects_negative_bed_count(db: AsyncSession, auth: AuthContext):
    run = await _create_run(db, auth)
    place = _place(run.id)
    db.add(place)
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/api/v1/maps/runs/{run.id}/places/{place.id}",
            json={"bed_count": -1},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_patch_rejects_place_from_different_run(db: AsyncSession, auth: AuthContext):
    run_a = await _create_run(db, auth)
    run_b = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="SE",
        country_name="Sweden",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run_b)
    await db.flush()
    place = _place(run_a.id)
    db.add(place)
    await db.commit()

    app = _client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            f"/api/v1/maps/runs/{run_b.id}/places/{place.id}",
            json={"bed_count": 8},
        )

    assert response.status_code == 404
    refreshed = await db.get(MapsPlace, place.id)
    assert refreshed is not None
    assert refreshed.bed_count is None


@pytest.mark.asyncio
async def test_detail_enrichment_skips_overridden_bed_count(db, auth, monkeypatch):
    run = await _create_run(db, auth)
    place = _place(
        run.id,
        "locked-beds",
        bed_count=8,
        treatment_price=None,
        official_website=None,
        raw_website=None,
        manual_field_overrides=["bed_count"],
        enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
        is_relevant=True,
    )
    db.add(place)
    await db.commit()

    factory = async_sessionmaker(bind=db.bind, expire_on_commit=False)
    fake_result = SimpleNamespace(
        place_id=place.id,
        addictions_treated=[],
        languages_spoken=[],
        treatment_price="$5000",
        contact_email=None,
        contact_phone=None,
        bed_count=12,
    )

    async def fake_fetch_detail_batch(self, payloads, **kwargs):
        return [fake_result]

    monkeypatch.setattr(
        MapsDetailEnrichmentService, "_fetch_detail_batch", fake_fetch_detail_batch
    )

    completed = await MapsDetailEnrichmentService()._enrich_batch(
        factory,
        places=[place],
        country_code="FI",
        country_name="Finland",
        parse_stats=EnrichmentParseStats(),
        tracker=MapsQuotaTracker(),
    )
    assert completed == 1

    async with factory() as session:
        refreshed = await session.get(MapsPlace, place.id)
        assert refreshed is not None
        assert refreshed.bed_count == 8
        assert refreshed.treatment_price == "$5000"
