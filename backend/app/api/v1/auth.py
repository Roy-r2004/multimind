from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext, get_auth_context
from app.core.exceptions import UnauthorizedError
from app.db.models import AuditSeverity, User
from app.db.session import get_db
from app.schemas.api import (
    SessionResponse,
    SignInRequest,
    SignUpRequest,
    TokenResponse,
)
from app.services.audit_service import audit_service
from app.services.auth_service import auth_service

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


@router.post("/signup", response_model=TokenResponse)
async def sign_up(data: SignUpRequest, db: AsyncSession = Depends(get_db)):
    session = await auth_service.sign_up(db, data)
    from sqlalchemy import select
    from app.db.models import User

    result = await db.execute(select(User).where(User.id == session.user.id))
    user = result.scalar_one()
    token = auth_service.create_token(user, session.organization.id)
    return TokenResponse(
        access_token=token,
        user=session.user,
        organization=session.organization,
    )


@router.post("/signin", response_model=TokenResponse)
async def sign_in(
    data: SignInRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    import sys
    print(f"[SIGNIN DEBUG] Email: {data.email}, Password length: {len(data.password)}", file=sys.stderr)
    try:
        session = await auth_service.sign_in(db, data)
    except UnauthorizedError as e:
        print(f"[SIGNIN DEBUG] Auth failed: {e}", file=sys.stderr)
        await audit_service.record(
            db,
            org_id=None,
            action="auth.sign_in_failed",
            category="auth",
            summary=f"Failed sign-in attempt for {data.email.lower()}",
            actor_email=data.email.lower(),
            severity=AuditSeverity.WARNING,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        raise

    result = await db.execute(select(User).where(User.id == session.user.id))
    user = result.scalar_one()
    await audit_service.record_auth_sign_in(
        db,
        org_id=session.organization.id,
        user=user,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    token = auth_service.create_token(user, session.organization.id)
    return TokenResponse(
        access_token=token,
        user=session.user,
        organization=session.organization,
    )


@router.get("/session", response_model=SessionResponse)
async def get_session(
    auth: AuthContext = Depends(get_auth_context),
    db: AsyncSession = Depends(get_db),
):
    return await auth_service.get_session(db, auth.user.id)


@router.post("/debug-signin", response_model=TokenResponse)
async def debug_signin(password: str = "", db: AsyncSession = Depends(get_db)):
    """Debug login endpoint - accepts password to login to datacenter account"""
    if password != "password123":
        raise UnauthorizedError("Invalid password")

    # Get datacenter account
    result = await db.execute(select(User).where(User.email == "datacenter.client@gmail.com"))
    user = result.scalar_one_or_none()

    if not user:
        raise UnauthorizedError("Account not found")

    # Get session
    result = await db.execute(
        select(OrgMembership, Organization)
        .join(Organization, Organization.id == OrgMembership.org_id)
        .where(OrgMembership.user_id == user.id)
        .limit(1)
    )
    row = result.first()
    if row is None:
        raise UnauthorizedError("Organization not found")

    membership, org = row
    token = auth_service.create_token(user, org.id)

    return TokenResponse(
        access_token=token,
        user=UserResponse.model_validate(user),
        organization=OrgResponse(
            id=org.id,
            name=org.name,
            slug=org.slug,
            plan=org.plan,
            role=membership.role.value,
        ),
    )
