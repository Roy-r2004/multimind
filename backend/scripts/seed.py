"""Seed system model sets, templates, and demo user."""

import asyncio

from sqlalchemy import select

from app.core.security import hash_password
from app.db.base import Base
from app.db.models import (
    ModelSet,
    Organization,
    OrgMembership,
    OrgRole,
    Strategy,
    Template,
    User,
    UserBrain,
    UserPreferences,
)
from app.db.session import AsyncSessionLocal, engine

REFEREE_CUSTOM_INSTRUCTIONS = """\
You are an expert Referee/Synthesizer AI within a multi-model system. Your primary role is to merge multiple AI-generated responses into a single, authoritative answer that is more accurate, complete, and useful than any individual input. Follow these core principles and workflow:

Core Priorities (in order)
Safety & Policy Compliance
Refuse or redirect any unsafe, illegal, or unethical content, regardless of model input.
Factual Accuracy & Evidence
Prioritize claims with strong evidence and clear reasoning. Never fabricate or overstate certainty. Explicitly flag unresolved uncertainty or disagreement.
Completeness & Nuance
Ensure all important perspectives, caveats, and edge cases are represented, including valuable minority viewpoints.
Clarity & Coherence
Write as a unified, logically structured narrative in a consistent voice. Prioritize readability and logical flow.
Conciseness & Practical Usefulness
Eliminate redundancy and filler. Preserve high-impact insights without oversimplification.
Workflow
Decompose: Break each input into atomic claims, facts, arguments, examples, and caveats.
Cluster & Map: Group similar claims; identify consensus, unique insights, and contradictions.
Evaluate & Score: Assess each claim for accuracy, evidence strength, and relevance. Use consensus as a backbone but prioritize truth over majority.
Resolve Conflicts: Prefer claims with stronger support. When uncertainty or disagreement remains, state it plainly with evidence context.
Synthesize: Draft an original response integrating the strongest, most relevant content; supplement consensus with unique, well-supported insights.
Quality Check: Review for coverage, accuracy, clarity, and proper handling of nuance and uncertainty.
Output Standards
Use Markdown formatting with headings and bullets as appropriate.
Attribute claims or confidence only if requested.
Do not mention model identities unless specifically asked.
Never fabricate sources, data, or certainty.
State unresolved uncertainties or disagreements explicitly.

## Assessment criteria
Calibrate every CONFIDENCE score (0–100) against these priorities:
no criteria.\
"""

SYSTEM_MODEL_SETS = [
    {
        "slug": "referee",
        "name": "Chafiq Referee",
        "description": "Expert referee synthesizer — merges council answers into a single authoritative verdict.",
        "models": ["gpt-4.1", "claude", "gemini", "grok", "deepseek"],
        "verdict_model": "gpt-4.1",
        "strategy": Strategy.REFEREE,
        "best_for": "General questions, everyday use, authoritative synthesis",
        "custom_instructions": REFEREE_CUSTOM_INSTRUCTIONS,
    },
    {
        # Stable slug preserved from the locally created UI set (not a row UUID).
        "slug": "set-7edaefc8",
        "name": "Chafic ultimate model set",
        "description": "Custom model set.",
        "models": [
            "gemini",
            "or:openai--gpt-5.5-pro",
            "or:anthropic--claude-opus-4",
            "or:~moonshotai--kimi-latest",
        ],
        "verdict_model": "or:openai--gpt-5.5",
        "strategy": Strategy.REFEREE,
        "best_for": "Custom model set.",
        "template_name": "Chafiq Referee",
        "custom_instructions": REFEREE_CUSTOM_INSTRUCTIONS,
    },
    {
        "slug": "balanced",
        "name": "Balanced Set",
        "description": "Great default five-model council for everyday questions.",
        "models": ["gpt-4.1", "claude", "gemini", "grok", "deepseek"],
        "verdict_model": "gpt-4.1",
        "strategy": Strategy.SYNTHESIZE,
        "best_for": "General questions, everyday use",
    },
    {
        "slug": "coding",
        "name": "Coding Set",
        "description": "Tuned for code review and debugging.",
        "models": ["gpt-4.1", "claude", "gemini", "grok", "deepseek"],
        "verdict_model": "claude",
        "strategy": Strategy.PICK_BEST,
        "best_for": "Coding, debugging, architecture",
    },
    {
        "slug": "business",
        "name": "Business Set",
        "description": "Strategic, concise, action-oriented.",
        "models": ["gpt-4.1", "claude", "gemini", "grok", "deepseek"],
        "verdict_model": "gpt-4.1",
        "strategy": Strategy.RECONCILE,
        "best_for": "Strategy, startups, business decisions",
    },
    {
        "slug": "research",
        "name": "Research Set",
        "description": "Deep, cited, careful reasoning.",
        "models": ["gpt-4.1", "claude", "gemini", "grok", "deepseek"],
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

DEFAULT_MODEL_SET_ID = "referee"


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
    prefs = result.scalar_one_or_none()
    if prefs is None:
        db.add(UserPreferences(user_id=user.id, default_model_set_id=DEFAULT_MODEL_SET_ID))
    elif prefs.default_model_set_id == "balanced":
        prefs.default_model_set_id = DEFAULT_MODEL_SET_ID


async def ensure_chafic_brain(db, *, user: User, org: Organization) -> None:
    result = await db.execute(select(UserBrain).where(UserBrain.user_id == user.id))
    brain = result.scalar_one_or_none()

    summary = (
        "Chafic El Khazen is a high-agency systems thinker with a long time horizon. "
        "He treats language as architecture — words are load-bearing, not decorative. "
        "Ambiguity is a threat to be resolved, not a space to inhabit. "
        "He is Lebanese, works primarily in English and French, and is embedded in the Apple/iOS ecosystem. "
        "He values precision, control over epistemic bandwidth, and structured thinking over intuition."
    )
    thinking_style = (
        "Builds from first principles. Decompose before synthesizing. "
        "Prefers frameworks with clear invariants. Long-horizon: optimizes for what matters in 5–10 years, "
        "not the next quarter. Treats every communication decision as an architecture decision. "
        "Dislikes open loops — always seeks resolution or an explicit 'parking' decision."
    )
    likes = [
        "Precision and unambiguous language",
        "Systems thinking and structural clarity",
        "Long-horizon planning and second-order consequences",
        "Tight feedback loops with measurable outcomes",
        "Direct, confident answers without hedging",
        "Apple/iOS ecosystem and product craftsmanship",
        "French and English code-switching where natural",
    ]
    dislikes = [
        "Ambiguity left unresolved",
        "Validation-seeking or wishy-washy responses",
        "Unnecessary caveats that dilute the signal",
        "Verbosity without information density",
        "Overconfident answers lacking evidence",
        "Bureaucratic thinking that substitutes process for judgment",
    ]
    memories = [
        {
            "title": "Language as architecture",
            "content": "Chafic believes every word choice is a structural decision. Sloppy language = sloppy thinking.",
        },
        {
            "title": "Lebanese roots, global operator",
            "content": "Based in Lebanon, operating globally. Bilingual EN/FR. Cultural context matters in interpretation.",
        },
        {
            "title": "Ambiguity as threat",
            "content": "Chafic explicitly resolves ambiguity before proceeding. He expects AI to do the same or flag it explicitly.",
        },
        {
            "title": "Apple/iOS native",
            "content": "Primary tech ecosystem: Apple. Prefers iOS-first thinking for consumer product questions.",
        },
        {
            "title": "Epistemic bandwidth",
            "content": "Chafic optimizes for signal-to-noise in information. He controls what he pays attention to deliberately.",
        },
    ]

    if brain is None:
        db.add(
            UserBrain(
                user_id=user.id,
                org_id=org.id,
                user_name=user.full_name,
                summary=summary,
                thinking_style=thinking_style,
                likes=likes,
                dislikes=dislikes,
                memories=memories,
                lesson_count=0,
            )
        )
    else:
        brain.org_id = org.id
        brain.user_name = user.full_name
        brain.summary = summary
        brain.thinking_style = thinking_style
        brain.likes = likes
        brain.dislikes = dislikes
        brain.memories = memories


async def find_legacy_user(db, emails: tuple[str, ...]) -> User | None:
    for email in emails:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user is not None:
            return user
    return None


async def ensure_system_model_sets(db) -> None:
    """Idempotently upsert system model sets by stable slug (not row UUID)."""
    for data in SYSTEM_MODEL_SETS:
        exists = await db.execute(select(ModelSet).where(ModelSet.slug == data["slug"]))
        model_set = exists.scalar_one_or_none()
        if model_set:
            model_set.name = data["name"]
            model_set.description = data["description"]
            model_set.models = list(data["models"])
            model_set.verdict_model = data["verdict_model"]
            model_set.strategy = data["strategy"]
            model_set.best_for = data["best_for"]
            model_set.is_system = True
            model_set.org_id = None
            model_set.template_name = data.get("template_name")
            if "custom_instructions" in data:
                model_set.custom_instructions = data["custom_instructions"]
            continue
        db.add(
            ModelSet(
                **data,
                is_system=True,
                org_id=None,
            )
        )


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        await ensure_system_model_sets(db)

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
            full_name="Chafic El Khazen",
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
        await ensure_chafic_brain(db, user=demo_user, org=org)

        await db.commit()
        print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
