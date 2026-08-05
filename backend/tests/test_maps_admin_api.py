"""HTTP-level tests for Maps census admin endpoints (Phase 4)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import AuthContext, get_auth_context, require_org_admin
from app.db.models import (
    MapsCensusCell,
    MapsCensusCellStatus,
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsLifecycleStatus,
    MapsPlace,
    OrgMembership,
    OrgRole,
    User,
)
from app.db.session import get_db
from app.main import create_app


def _admin_client_app(db: AsyncSession, auth: AuthContext):
    app = create_app()

    async def override_db():
        yield db

    async def override_auth():
        return auth

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = override_auth
    app.dependency_overrides[require_org_admin] = override_auth
    return app


async def _create_member_auth(db: AsyncSession, auth: AuthContext) -> AuthContext:
    user = User(email="member@example.com", hashed_password="x", full_name="Member")
    db.add(user)
    await db.flush()
    db.add(OrgMembership(org_id=auth.org_id, user_id=user.id, role=OrgRole.MEMBER))
    await db.flush()
    return AuthContext(user=user, org_id=auth.org_id, role=OrgRole.MEMBER)


@pytest.mark.asyncio
async def test_admin_dashboard_returns_derived_counts(db: AsyncSession, auth: AuthContext):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FR",
        country_name="France",
        status=MapsCensusStatus.RUNNING,
        cells_total=3,
        cells_completed=1,
        places_found=2,
        funnel_metrics={"places_found": 2, "eligible_candidates_found": 1},
        processing_state={"campaign_paused": True},
    )
    db.add(run)
    await db.flush()
    db.add_all(
        [
            MapsCensusCell(
                run_id=run.id,
                region_name="Île-de-France",
                query_text="rehab paris",
                status=MapsCensusCellStatus.PENDING,
            ),
            MapsCensusCell(
                run_id=run.id,
                region_name="Île-de-France",
                query_text="detox paris",
                status=MapsCensusCellStatus.FAILED,
            ),
            MapsCensusCell(
                run_id=run.id,
                region_name="Provence",
                query_text="centre addiction",
                status=MapsCensusCellStatus.CAPPED,
            ),
            MapsPlace(
                run_id=run.id,
                google_place_id="p-eligible",
                raw_name="Eligible Rehab",
                canonical_name="Eligible Rehab",
                lifecycle_status=MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value,
                client_eligibility=MapsClientEligibility.ELIGIBLE.value,
            ),
            MapsPlace(
                run_id=run.id,
                google_place_id="p-review",
                raw_name="Review Rehab",
                canonical_name="Review Rehab",
                lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
                client_eligibility=MapsClientEligibility.REVIEW.value,
            ),
        ]
    )
    await db.commit()

    app = _admin_client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/maps/runs/{run.id}/dashboard")

    assert response.status_code == 200
    body = response.json()
    assert body["current_stage"] == "paused"
    assert body["campaign_paused"] is True
    assert body["cells_pending"] == 1
    assert body["cells_failed"] == 1
    assert body["cells_capped"] == 1
    assert body["places_eligible"] == 1
    assert body["places_review"] == 1
    assert body["funnel_metrics"]["eligible_candidates_found"] == 1


@pytest.mark.asyncio
async def test_admin_places_paged_search_and_filters(db: AsyncSession, auth: AuthContext):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FR",
        country_name="France",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    db.add_all(
        [
            MapsPlace(
                run_id=run.id,
                google_place_id="alpha",
                raw_name="Alpha Center",
                canonical_name="Alpha Center",
                lifecycle_status=MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value,
                client_eligibility=MapsClientEligibility.ELIGIBLE.value,
            ),
            MapsPlace(
                run_id=run.id,
                google_place_id="beta",
                raw_name="Beta Clinic",
                canonical_name="Beta Clinic",
                lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
                client_eligibility=MapsClientEligibility.REVIEW.value,
            ),
        ]
    )
    await db.commit()

    app = _admin_client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/maps/runs/{run.id}/places/paged",
            params={"search": "Alpha", "client_eligibility": "eligible", "limit": 10},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["canonical_name"] == "Alpha Center"


@pytest.mark.asyncio
async def test_admin_review_action_persists_override(db: AsyncSession, auth: AuthContext):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FR",
        country_name="France",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="review-me",
        raw_name="Needs Review Center",
        canonical_name="Needs Review Center",
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        classification_evidence={"ownership_status": {"quote": "x" * 300, "source_url": "https://example.com"}},
    )
    db.add(place)
    await db.commit()

    app = _admin_client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/maps/runs/{run.id}/places/{place.id}/review",
            json={
                "action": "mark_eligible",
                "reason": "Manual verification confirmed private rehab",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["lifecycle_status"] == MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value
    assert body["client_eligibility"] == MapsClientEligibility.ELIGIBLE.value
    assert len(body["review_actions"]) == 1
    assert body["review_actions"][0]["action"] == "mark_eligible"
    assert len(body["classification_evidence"]["ownership_status"]["quote"]) == 240


@pytest.mark.asyncio
async def test_admin_reopen_for_keep_drop_reopens_regardless_of_current_reason(
    db: AsyncSession, auth: AuthContext
):
    """A place dropped for any reason — including one whose keep_drop_reason
    text has since been overwritten by a later bulk re-stamp — can be forced
    back into the keep/drop candidate pool by place_id."""
    from app.services.scraping.maps_keep_drop_service import (
        build_keep_drop_query,
    )

    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="BA",
        country_name="Bosnia and Herzegovina",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    place = MapsPlace(
        run_id=run.id,
        google_place_id="cenacolo",
        raw_name='Comunita Il Cenacolo "Campo Della Vita"',
        canonical_name='Comunita Il Cenacolo "Campo Della Vita"',
        lifecycle_status=MapsLifecycleStatus.UNRELATED.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        is_relevant=False,
        keep_drop_decision="drop",
        keep_drop_reason="preexisting_exclusion: already out of relevant set",
        keep_drop_confidence=1.0,
    )
    db.add(place)
    await db.commit()

    app = _admin_client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/maps/runs/{run.id}/places/{place.id}/review",
            json={
                "action": "reopen_for_keep_drop",
                "reason": "Likely-legitimate NGO therapeutic community, force re-judgment",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["keep_drop_decision"] is None
    assert body["lifecycle_status"] == MapsLifecycleStatus.NEEDS_REVIEW.value
    assert body["client_eligibility"] == MapsClientEligibility.REVIEW.value
    assert body["review_actions"][-1]["action"] == "reopen_for_keep_drop"

    await db.refresh(place)
    assert place.is_relevant is True
    candidates = (await db.execute(build_keep_drop_query(run.id))).scalars().all()
    assert place.id in {p.id for p in candidates}


@pytest.mark.asyncio
async def test_admin_pause_and_cancel_campaign(db: AsyncSession, auth: AuthContext):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FR",
        country_name="France",
        status=MapsCensusStatus.RUNNING,
    )
    db.add(run)
    await db.commit()

    app = _admin_client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        pause_response = await client.post(f"/api/v1/maps/runs/{run.id}/pause")
        assert pause_response.status_code == 200
        assert pause_response.json()["campaign_paused"] is True

        resume_response = await client.post(f"/api/v1/maps/runs/{run.id}/resume")
        assert resume_response.status_code == 200
        assert resume_response.json()["campaign_paused"] is False

        cancel_response = await client.post(f"/api/v1/maps/runs/{run.id}/cancel")
        assert cancel_response.status_code == 200
        assert cancel_response.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_admin_recover_reenqueues_failed_run(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    from app.services.scraping.maps_census_service import maps_census_service

    enqueued: list[str] = []

    async def _fake_enqueue(run_id: str):
        enqueued.append(run_id)

    monkeypatch.setattr(maps_census_service, "_enqueue", _fake_enqueue)

    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FR",
        country_name="France",
        status=MapsCensusStatus.FAILED,
        error_message="RuntimeError: boom",
        completed_at=datetime.now(UTC),
    )
    db.add(run)
    await db.commit()

    app = _admin_client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/v1/maps/runs/{run.id}/recover")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert enqueued == [run.id]
    await db.refresh(run)
    assert run.status == MapsCensusStatus.RUNNING
    assert run.error_message is None
    assert run.completed_at is None


@pytest.mark.asyncio
async def test_admin_recover_refuses_terminal_run(
    db: AsyncSession, auth: AuthContext, monkeypatch
):
    from app.services.scraping.maps_census_service import maps_census_service

    enqueued: list[str] = []

    async def _fake_enqueue(run_id: str):
        enqueued.append(run_id)

    monkeypatch.setattr(maps_census_service, "_enqueue", _fake_enqueue)

    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FR",
        country_name="France",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.commit()

    app = _admin_client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/v1/maps/runs/{run.id}/recover")

    assert response.status_code == 200
    assert "nothing to recover" in response.json()["message"]
    assert enqueued == []
    await db.refresh(run)
    assert run.status == MapsCensusStatus.COMPLETED


@pytest.mark.asyncio
async def test_admin_dashboard_forbidden_for_non_admin(db: AsyncSession, auth: AuthContext):
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FR",
        country_name="France",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.commit()

    member_auth = await _create_member_auth(db, auth)
    app = create_app()

    async def override_db():
        yield db

    async def override_member_auth():
        return member_auth

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = override_member_auth

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/maps/runs/{run.id}/dashboard")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_disabled_returns_forbidden(db: AsyncSession, auth: AuthContext, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_census_admin_ui_enabled", False)

    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="FR",
        country_name="France",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.commit()

    app = _admin_client_app(db, auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/maps/runs/{run.id}/dashboard")

    assert response.status_code == 403
