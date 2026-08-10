"""Focused tests for seeding Chafic ultimate model set."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import ModelSet, Organization, OrgMembership, OrgRole, Strategy, User
from app.llm.catalog import model_id_to_slug
from scripts.seed import (
    REFEREE_CUSTOM_INSTRUCTIONS,
    SYSTEM_MODEL_SETS,
    ensure_system_model_sets,
)

ULTIMATE_SLUG = "set-7edaefc8"
ULTIMATE_NAME = "Chafic ultimate model set"
ULTIMATE_MODELS = [
    "gemini",
    "or:openai--gpt-5",
    "or:anthropic--claude-opus-4",
]
ULTIMATE_VERDICT = "or:openai--gpt-5.5"


@pytest.fixture
async def db() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessionmaker() as session:
        yield session
    await engine.dispose()


def ultimate_spec() -> dict:
    match = next(item for item in SYSTEM_MODEL_SETS if item["slug"] == ULTIMATE_SLUG)
    return match


@pytest.mark.asyncio
async def test_fresh_seed_creates_ultimate_model_set(db: AsyncSession) -> None:
    await ensure_system_model_sets(db)
    await db.commit()

    row = (
        await db.execute(select(ModelSet).where(ModelSet.slug == ULTIMATE_SLUG))
    ).scalar_one()
    assert row.name == ULTIMATE_NAME
    assert row.is_system is True
    assert row.org_id is None


@pytest.mark.asyncio
async def test_seed_creates_exact_models_order_and_verdict(db: AsyncSession) -> None:
    await ensure_system_model_sets(db)
    await db.commit()

    row = (
        await db.execute(select(ModelSet).where(ModelSet.slug == ULTIMATE_SLUG))
    ).scalar_one()
    assert row.models == ULTIMATE_MODELS
    assert row.verdict_model == ULTIMATE_VERDICT
    assert row.strategy == Strategy.REFEREE
    assert row.template_name == "Chafiq Referee"
    assert row.custom_instructions == REFEREE_CUSTOM_INSTRUCTIONS
    assert row.description == "Custom model set."
    assert row.best_for == "Custom model set."

    # Stable OpenRouter-derived ids (not environment UUIDs).
    assert model_id_to_slug("gemini") == "google/gemini-2.5-pro"
    assert model_id_to_slug("or:openai--gpt-5") == "openai/gpt-5"
    assert model_id_to_slug("or:anthropic--claude-opus-4") == "anthropic/claude-opus-4"
    assert model_id_to_slug("or:openai--gpt-5.5") == "openai/gpt-5.5"


@pytest.mark.asyncio
async def test_second_seed_run_creates_no_duplicates(db: AsyncSession) -> None:
    await ensure_system_model_sets(db)
    await db.commit()
    await ensure_system_model_sets(db)
    await db.commit()

    count = (
        await db.execute(
            select(func.count()).select_from(ModelSet).where(ModelSet.slug == ULTIMATE_SLUG)
        )
    ).scalar_one()
    assert count == 1

    name_count = (
        await db.execute(
            select(func.count())
            .select_from(ModelSet)
            .where(ModelSet.name == ULTIMATE_NAME)
        )
    ).scalar_one()
    assert name_count == 1


@pytest.mark.asyncio
async def test_seed_updates_existing_row_without_depending_on_uuid(db: AsyncSession) -> None:
    # Pre-existing row with a different DB id and stale config, same stable slug.
    stale = ModelSet(
        org_id=None,
        slug=ULTIMATE_SLUG,
        name="stale name",
        description="stale",
        models=["gpt-4.1"],
        verdict_model="gpt-4.1",
        strategy=Strategy.SYNTHESIZE,
        best_for="stale",
        is_system=False,
    )
    db.add(stale)
    await db.flush()
    original_id = stale.id

    await ensure_system_model_sets(db)
    await db.commit()

    row = (
        await db.execute(select(ModelSet).where(ModelSet.slug == ULTIMATE_SLUG))
    ).scalar_one()
    assert row.id == original_id
    assert row.name == ULTIMATE_NAME
    assert row.models == ULTIMATE_MODELS
    assert row.verdict_model == ULTIMATE_VERDICT
    assert row.is_system is True
    assert row.org_id is None


@pytest.mark.asyncio
async def test_unrelated_user_created_sets_remain_untouched(db: AsyncSession) -> None:
    org = Organization(name="Acme", slug="acme-test")
    user = User(email="owner@example.com", hashed_password="x", full_name="Owner")
    db.add_all([org, user])
    await db.flush()
    db.add(OrgMembership(org_id=org.id, user_id=user.id, role=OrgRole.OWNER))

    user_set = ModelSet(
        org_id=org.id,
        slug="set-user-custom",
        name="My private set",
        description="keep me",
        models=["claude"],
        verdict_model="claude",
        strategy=Strategy.PICK_BEST,
        best_for="private",
        is_system=False,
    )
    db.add(user_set)
    await db.flush()
    user_set_id = user_set.id

    await ensure_system_model_sets(db)
    await db.commit()

    remaining = (
        await db.execute(select(ModelSet).where(ModelSet.id == user_set_id))
    ).scalar_one()
    assert remaining.name == "My private set"
    assert remaining.org_id == org.id
    assert remaining.is_system is False
    assert remaining.models == ["claude"]


@pytest.mark.asyncio
async def test_system_scoping_and_referee_still_present(db: AsyncSession) -> None:
    await ensure_system_model_sets(db)
    await db.commit()

    ultimate = (
        await db.execute(select(ModelSet).where(ModelSet.slug == ULTIMATE_SLUG))
    ).scalar_one()
    referee = (
        await db.execute(select(ModelSet).where(ModelSet.slug == "referee"))
    ).scalar_one()

    assert ultimate.is_system is True
    assert ultimate.org_id is None
    assert referee.is_system is True
    assert referee.name == "Chafiq Referee"


def test_ultimate_spec_is_present_in_system_model_sets() -> None:
    spec = ultimate_spec()
    assert spec["models"] == ULTIMATE_MODELS
    assert spec["verdict_model"] == ULTIMATE_VERDICT
    assert spec["name"] == ULTIMATE_NAME
    assert ULTIMATE_MODELS[0] == "gemini"
    assert ULTIMATE_MODELS == [
        "gemini",
        "or:openai--gpt-5",
        "or:anthropic--claude-opus-4",
    ]
    assert len(ULTIMATE_MODELS) == 3
    # Missing OpenRouter models are not validated at seed time (same as other system sets);
    # identifiers remain stable slug-derived ids for cross-environment use.
    for model_id in ULTIMATE_MODELS + [ULTIMATE_VERDICT]:
        if model_id.startswith("or:"):
            assert "/" not in model_id
            assert "--" in model_id
