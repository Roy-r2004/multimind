"""FastAPI dependency injection — auth context and tenant scoping."""

from dataclasses import dataclass

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.db.models import OrgMembership, OrgRole, User
from app.db.session import get_db

security = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AuthContext:
    user: User
    org_id: str
    role: OrgRole


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> User:
    if credentials is None:
        raise UnauthorizedError()
    try:
        payload = decode_access_token(credentials.credentials)
    except ValueError as exc:
        raise UnauthorizedError("Invalid or expired token") from exc

    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError()

    result = await db.execute(select(User).where(User.id == str(user_id), User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    if user is None:
        raise UnauthorizedError("User not found")
    return user


async def get_auth_context(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
) -> AuthContext:
    result = await db.execute(
        select(OrgMembership)
        .where(OrgMembership.user_id == user.id)
        .options(selectinload(OrgMembership.organization))
    )
    memberships = list(result.scalars().all())
    if not memberships:
        raise ForbiddenError("No organization membership")

    membership = memberships[0]
    if x_org_id:
        match = next((m for m in memberships if str(m.org_id) == x_org_id), None)
        if match is None:
            raise ForbiddenError("Not a member of requested organization")
        membership = match

    return AuthContext(user=user, org_id=membership.org_id, role=membership.role)


async def require_org_admin(
    auth: AuthContext = Depends(get_auth_context),
) -> AuthContext:
    if auth.role not in (OrgRole.OWNER, OrgRole.ADMIN):
        raise ForbiddenError("Organization admin access required")
    return auth


async def get_optional_user(
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> User | None:
    if credentials is None:
        return None
    try:
        return await get_current_user(db, credentials)
    except UnauthorizedError:
        return None
