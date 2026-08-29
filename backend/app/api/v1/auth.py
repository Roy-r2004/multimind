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
    try:
        session = await auth_service.sign_in(db, data)
    except UnauthorizedError:
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
