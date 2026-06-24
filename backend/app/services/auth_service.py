"""Authentication service."""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, UnauthorizedError, ValidationError
from app.core.security import create_access_token, hash_password, verify_password
from app.db.models import OrgMembership, OrgRole, Organization, User, UserPreferences
from app.schemas.api import OrgResponse, SessionResponse, SignInRequest, SignUpRequest, UserResponse


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "org"


class AuthService:
    async def sign_up(self, db: AsyncSession, data: SignUpRequest) -> SessionResponse:
        existing = await db.execute(select(User).where(User.email == data.email))
        if existing.scalar_one_or_none():
            raise ConflictError("Email already registered")

        user = User(
            email=data.email,
            hashed_password=hash_password(data.password),
            full_name=data.full_name,
        )
        db.add(user)
        await db.flush()

        base_slug = _slugify(data.org_name)
        slug = base_slug
        counter = 1
        while True:
            check = await db.execute(select(Organization).where(Organization.slug == slug))
            if check.scalar_one_or_none() is None:
                break
            slug = f"{base_slug}-{counter}"
            counter += 1

        org = Organization(name=data.org_name, slug=slug)
        db.add(org)
        await db.flush()

        membership = OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.OWNER)
        db.add(membership)

        prefs = UserPreferences(user_id=user.id, default_model_set_id="balanced")
        db.add(prefs)
        await db.flush()

        return self._session(user, org, OrgRole.OWNER)

    async def sign_in(self, db: AsyncSession, data: SignInRequest) -> SessionResponse:
        result = await db.execute(select(User).where(User.email == data.email))
        user = result.scalar_one_or_none()
        if user is None or not verify_password(data.password, user.hashed_password):
            raise UnauthorizedError("Invalid email or password")
        if not user.is_active:
            raise UnauthorizedError("Account disabled")

        result = await db.execute(
            select(OrgMembership, Organization)
            .join(Organization, Organization.id == OrgMembership.org_id)
            .where(OrgMembership.user_id == user.id)
            .limit(1)
        )
        row = result.first()
        if row is None:
            raise ValidationError("User has no organization")
        membership, org = row
        return self._session(user, org, membership.role)

    async def get_session(self, db: AsyncSession, user_id: str) -> SessionResponse:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None:
            raise UnauthorizedError()

        result = await db.execute(
            select(OrgMembership, Organization)
            .join(Organization, Organization.id == OrgMembership.org_id)
            .where(OrgMembership.user_id == user.id)
            .limit(1)
        )
        row = result.first()
        if row is None:
            raise ValidationError("User has no organization")
        membership, org = row
        return self._session(user, org, membership.role)

    def create_token(self, user: User, org_id: str) -> str:
        return create_access_token(str(user.id), extra={"org_id": str(org_id)})

    def _session(self, user: User, org: Organization, role: OrgRole) -> SessionResponse:
        return SessionResponse(
            user=UserResponse.model_validate(user),
            organization=OrgResponse(
                id=org.id,
                name=org.name,
                slug=org.slug,
                plan=org.plan,
                role=role.value,
            ),
        )


auth_service = AuthService()
