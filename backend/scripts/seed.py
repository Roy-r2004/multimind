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

        demo = await db.execute(select(User).where(User.email == "chafic@acme.co"))
        if demo.scalar_one_or_none() is None:
            # Migrate legacy demo user if present
            legacy = await db.execute(select(User).where(User.email == "sara@acme.co"))
            legacy_user = legacy.scalar_one_or_none()
            if legacy_user:
                legacy_user.email = "chafic@acme.co"
                legacy_user.full_name = "Chafic"
                legacy_user.hashed_password = hash_password("password123")
            else:
                user = User(
                    email="chafic@acme.co",
                    hashed_password=hash_password("password123"),
                    full_name="Chafic",
                )
                db.add(user)
                await db.flush()

                org_result = await db.execute(select(Organization).where(Organization.slug == "acme"))
                org = org_result.scalar_one_or_none()
                if org is None:
                    org = Organization(name="Acme Corp", slug="acme")
                    db.add(org)
                    await db.flush()

                db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.OWNER))
                db.add(UserPreferences(user_id=user.id, default_model_set_id="balanced"))

        await db.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
