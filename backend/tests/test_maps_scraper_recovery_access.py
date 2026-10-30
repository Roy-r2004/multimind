"""HTTP-level authorization tests for the four scraper-recovery endpoints.

These were previously gated by require_org_admin (OWNER/ADMIN only). They
now use ordinary authenticated-organization-member authorization
(get_auth_context): any OWNER, ADMIN, or MEMBER of the run's own
organization can use them, while organization isolation (a user from a
DIFFERENT org can never see or touch this run) is preserved unchanged via
_get_run_for_org.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.db.models import (
    MapsCensusRun,
    MapsCensusStatus,
    Organization,
    OrgMembership,
    OrgRole,
    User,
)
from app.db.session import get_db
from app.main import create_app

RECOVERY_ENDPOINTS = [
    ("GET", "keep-drop-retry-projection"),
    ("POST", "retry-failed-keep-drop"),
    ("GET", "eligibility-retry-projection"),
    ("POST", "retry-unresolved-eligibility-evidence"),
]


def _run_for(auth: AuthContext) -> MapsCensusRun:
    return MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="BY",
        country_name="Belarus",
        status=MapsCensusStatus.COMPLETED_WITH_WARNINGS,
        cells_total=1,
        cells_completed=1,
        places_found=1,
    )


async def _member_with_role(db: AsyncSession, auth: AuthContext, role: OrgRole, email: str) -> AuthContext:
    user = User(email=email, hashed_password="x", full_name=role.value)
    db.add(user)
    await db.flush()
    db.add(OrgMembership(org_id=auth.org_id, user_id=user.id, role=role))
    await db.flush()
    return AuthContext(user=user, org_id=auth.org_id, role=role)


async def _app_with_auth_override(db: AsyncSession, resolved_auth: AuthContext):
    app = create_app()

    async def override_db():
        yield db

    async def override_auth():
        return resolved_auth

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = override_auth
    return app


async def _call(app, method: str, path: str) -> int:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.request(method, f"/api/v1/maps/runs/{path}")
    return response.status_code


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [OrgRole.MEMBER, OrgRole.ADMIN, OrgRole.OWNER])
async def test_any_org_role_can_use_all_four_recovery_endpoints(
    db: AsyncSession, auth: AuthContext, role: OrgRole
):
    """The actual requirement this change exists for: a plain MEMBER (not
    just ADMIN/OWNER) must be able to use every recovery endpoint."""
    run = _run_for(auth)
    db.add(run)
    await db.commit()

    member_auth = await _member_with_role(db, auth, role, f"{role.value.lower()}@example.com")
    app = await _app_with_auth_override(db, member_auth)

    for method, suffix in RECOVERY_ENDPOINTS:
        status = await _call(app, method, f"{run.id}/{suffix}")
        assert status == 200, f"{role.value} got {status} on {method} {suffix}"


@pytest.mark.asyncio
async def test_unauthenticated_request_is_rejected(db: AsyncSession, auth: AuthContext):
    """No Authorization header at all — must still be rejected; recovery
    is for authenticated org members, never anonymous callers."""
    run = _run_for(auth)
    db.add(run)
    await db.commit()

    app = create_app()

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    # Deliberately NOT overriding get_auth_context/get_current_user — the
    # real, unauthenticated dependency chain must reject this on its own.

    for method, suffix in RECOVERY_ENDPOINTS:
        status = await _call(app, method, f"{run.id}/{suffix}")
        assert status == 401, f"got {status} on {method} {suffix}"


@pytest.mark.asyncio
async def test_cross_organization_member_cannot_access_the_run(db: AsyncSession, auth: AuthContext):
    """Organization isolation must survive this change unchanged: a MEMBER
    (or ADMIN/OWNER) of a DIFFERENT organization must never be able to see
    or act on this run, regardless of their own role."""
    run = _run_for(auth)
    db.add(run)
    await db.flush()

    other_org = Organization(name="Other Org", slug="other-org-recovery-test")
    db.add(other_org)
    await db.flush()
    other_user = User(email="outsider@example.com", hashed_password="x", full_name="Outsider")
    db.add(other_user)
    await db.flush()
    db.add(OrgMembership(org_id=other_org.id, user_id=other_user.id, role=OrgRole.OWNER))
    await db.commit()

    outsider_auth = AuthContext(user=other_user, org_id=other_org.id, role=OrgRole.OWNER)
    app = await _app_with_auth_override(db, outsider_auth)

    for method, suffix in RECOVERY_ENDPOINTS:
        status = await _call(app, method, f"{run.id}/{suffix}")
        assert status == 404, f"got {status} on {method} {suffix}"


@pytest.mark.asyncio
async def test_same_org_member_sees_accurate_projection_counts(db: AsyncSession, auth: AuthContext):
    """Not just a status-code check: a MEMBER actually gets the real,
    correct projection payload (same data an OWNER would see)."""
    from app.db.models import MapsClientEligibility, MapsLifecycleStatus, MapsPlace

    run = _run_for(auth)
    db.add(run)
    await db.flush()
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="g1",
            raw_name="Unresolved Rehab",
            canonical_name="Unresolved Rehab",
            is_relevant=True,
            keep_drop_decision="review",
            lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
            client_eligibility=MapsClientEligibility.REVIEW.value,
            inpatient_treatment=None,
            residential_accommodation=None,
        )
    )
    await db.commit()

    member_auth = await _member_with_role(db, auth, OrgRole.MEMBER, "member2@example.com")
    app = await _app_with_auth_override(db, member_auth)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/maps/runs/{run.id}/eligibility-retry-projection")

    assert response.status_code == 200
    assert response.json()["candidate_count"] == 1
