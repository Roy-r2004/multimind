"""Standalone Saved Prompt creation (no chat turn yet) — used by Prompt Builder."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.exceptions import ValidationError
from app.services.saved_prompt_service import saved_prompt_service
from tests.conftest import create_other_auth


@pytest.mark.asyncio
async def test_create_standalone_saves_without_turn_or_verdict(
    db: AsyncSession, auth: AuthContext
):
    result = await saved_prompt_service.create_standalone(
        db,
        auth,
        prompt_text="A fully generated prompt from the Builder",
        title="My prompt",
        label_ids=[],
        label_names=[],
    )

    assert result.prompt_text == "A fully generated prompt from the Builder"
    assert result.title == "My prompt"
    assert result.turn_id is None
    assert result.chat_id is None
    assert result.verdict_text is None


@pytest.mark.asyncio
async def test_create_standalone_requires_prompt_text(db: AsyncSession, auth: AuthContext):
    with pytest.raises(ValidationError, match="Prompt text is required"):
        await saved_prompt_service.create_standalone(
            db,
            auth,
            prompt_text="   ",
            title=None,
            label_ids=[],
            label_names=[],
        )


@pytest.mark.asyncio
async def test_create_standalone_is_isolated_by_org_and_user(
    db: AsyncSession, auth: AuthContext
):
    other = await create_other_auth(db)
    mine = await saved_prompt_service.create_standalone(
        db, auth, prompt_text="mine", title=None, label_ids=[], label_names=[]
    )
    theirs = await saved_prompt_service.create_standalone(
        db, other, prompt_text="theirs", title=None, label_ids=[], label_names=[]
    )

    mine_results = await saved_prompt_service.search(db, auth)
    other_results = await saved_prompt_service.search(db, other)

    assert [row.id for row in mine_results] == [mine.id]
    assert [row.id for row in other_results] == [theirs.id]


@pytest.mark.asyncio
async def test_create_standalone_supports_labels(db: AsyncSession, auth: AuthContext):
    result = await saved_prompt_service.create_standalone(
        db,
        auth,
        prompt_text="labeled prompt",
        title=None,
        label_ids=[],
        label_names=["marketing"],
    )

    assert [label.name for label in result.labels] == ["marketing"]
