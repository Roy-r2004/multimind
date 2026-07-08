"""Seed system model sets, templates, and demo user."""

import asyncio

from sqlalchemy import select

from app.core.security import hash_password
from app.db.models import (
    ModelSet,
    OrgMembership,
    OrgRole,
    Organization,
    Strategy,
    Template,
    User,
    UserPreferences,
)
from app.db.session import AsyncSessionLocal, engine
from app.db.base import Base


SYSTEM_MODEL_SETS = [
    {
        "slug": "balanced",
        "name": "Balanced Set",
        "description": "Great default for everyday questions.",
        "models": ["gpt-4.1", "claude", "gemini"],
        "verdict_model": "gpt-4.1",
        "strategy": Strategy.SYNTHESIZE,
        "best_for": "General questions, everyday use",
    },
    {
        "slug": "coding",
        "name": "Coding Set",
        "description": "Tuned for code review and debugging.",
        "models": ["gpt-4.1", "deepseek", "claude"],
        "verdict_model": "claude",
        "strategy": Strategy.PICK_BEST,
        "best_for": "Coding, debugging, architecture",
    },
    {
        "slug": "business",
        "name": "Business Set",
        "description": "Strategic, concise, action-oriented.",
        "models": ["gpt-4.1", "gemini", "mistral"],
        "verdict_model": "gpt-4.1",
        "strategy": Strategy.RECONCILE,
        "best_for": "Strategy, startups, business decisions",
    },
    {
        "slug": "research",
        "name": "Research Set",
        "description": "Deep, cited, careful reasoning.",
        "models": ["claude", "qwen", "gpt-4.1"],
        "verdict_model": "claude",
        "strategy": Strategy.DEBATE,
        "best_for": "Research, analysis, fact-checking",
    },
]

SYSTEM_TEMPLATES = [
    {
        "title": "Explain simply",
        "description": "Beginner-friendly explanations.",
        "category": "Learning",
        "instructions": "Explain like I'm new to the topic. Use simple words and short examples.",
    },
    {
        "title": "Short business answer",
        "description": "Concise, decision-oriented.",
        "category": "Business",
        "instructions": "Give a short, business-focused answer. Lead with the recommendation.",
    },
    {
        "title": "Compare & recommend",
        "description": "Weigh options and pick one.",
        "category": "Decision",
        "instructions": "Compare options in a small table, then recommend the most practical one.",
    },
    {
        "title": "Step-by-step",
        "description": "Walk through with examples.",
        "category": "Learning",
        "instructions": "Explain step by step with examples for each step.",
    },
]


DEMO_EMAIL = "chafic@gmail.com"
ADMIN_EMAIL = "admin@gmail.com"
DEMO_PASSWORD = "password123"
ADMIN_PASSWORD = "password123"
DEMO_ORG_SLUG = "acme"
LEGACY_DEMO_EMAILS = ("chafic@acme.co", "sara@acme.co")
LEGACY_ADMIN_EMAILS = ("admin@multi.ai",)


async def ensure_user(
    db,
    *,
    email: str,
    full_name: str,
    password: str,
) -> User:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(email=email, hashed_password=hash_password(password), full_name=full_name)
        db.add(user)
        await db.flush()
    else:
        # Don't re-hash on every boot — bcrypt is intentionally slow and delays cold starts.
        user.full_name = full_name
        user.is_active = True
    return user


async def ensure_membership(db, *, org: Organization, user: User, role: OrgRole) -> OrgMembership:
    result = await db.execute(
        select(OrgMembership).where(
            OrgMembership.org_id == org.id,
            OrgMembership.user_id == user.id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership is None:
        membership = OrgMembership(org_id=org.id, user_id=user.id, role=role)
        db.add(membership)
    else:
        membership.role = role
    return membership


async def ensure_preferences(db, *, user: User) -> None:
    result = await db.execute(select(UserPreferences).where(UserPreferences.user_id == user.id))
    if result.scalar_one_or_none() is None:
        db.add(UserPreferences(user_id=user.id, default_model_set_id="balanced"))


async def find_legacy_user(db, emails: tuple[str, ...]) -> User | None:
    for email in emails:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is not None:
            return user
    return None


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        for data in SYSTEM_MODEL_SETS:
            exists = await db.execute(select(ModelSet).where(ModelSet.slug == data["slug"]))
            if exists.scalar_one_or_none():
                continue
            db.add(ModelSet(**data, is_system=True))

        for data in SYSTEM_TEMPLATES:
            exists = await db.execute(
                select(Template).where(
                    Template.title == data["title"], Template.is_system.is_(True)
                )
            )
            if exists.scalar_one_or_none():
                continue
            db.add(Template(**data, is_system=True))

        org_result = await db.execute(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
        org = org_result.scalar_one_or_none()
        if org is None:
            org = Organization(name="Acme Corp", slug=DEMO_ORG_SLUG)
            db.add(org)
            await db.flush()

        demo_result = await db.execute(select(User).where(User.email == DEMO_EMAIL))
        demo_user = demo_result.scalar_one_or_none()
        if demo_user is None:
            demo_user = await find_legacy_user(db, LEGACY_DEMO_EMAILS)
            if demo_user:
                demo_user.email = DEMO_EMAIL

        demo_user = await ensure_user(
            db,
            email=DEMO_EMAIL,
            full_name="Chafic",
            password=DEMO_PASSWORD,
        )

        admin_result = await db.execute(select(User).where(User.email == ADMIN_EMAIL))
        admin_user = admin_result.scalar_one_or_none()
        if admin_user is None:
            admin_user = await find_legacy_user(db, LEGACY_ADMIN_EMAILS)
            if admin_user:
                admin_user.email = ADMIN_EMAIL

        admin_user = await ensure_user(
            db,
            email=ADMIN_EMAIL,
            full_name="Admin",
            password=ADMIN_PASSWORD,
        )

        await ensure_membership(db, org=org, user=demo_user, role=OrgRole.MEMBER)
        await ensure_membership(db, org=org, user=admin_user, role=OrgRole.OWNER)
        await ensure_preferences(db, user=demo_user)
        await ensure_preferences(db, user=admin_user)

        await db.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
