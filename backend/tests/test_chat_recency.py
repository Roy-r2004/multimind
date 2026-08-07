"""Chat sidebar recency: turn activity bumps Chat.updated_at; viewing does not."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import Chat, Turn
from app.schemas.api import TurnCreateRequest
from app.services.chat_service import chat_service
from tests.conftest import AuthContext, create_model_set


async def _create_chat(db, auth: AuthContext, *, title: str = "New chat") -> Chat:
    chat = Chat(
        org_id=auth.org_id,
        created_by=auth.user.id,
        title=title,
    )
    db.add(chat)
    await db.flush()
    return chat


@pytest.mark.asyncio
async def test_first_turn_sets_title_and_bumps_updated_at(db, auth):
    model_set = await create_model_set(db, auth)
    chat = await _create_chat(db, auth)
    before = chat.updated_at

    turn = await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(user_message="  Hello council  ", model_set_id=model_set.slug),
    )

    await db.refresh(chat)
    assert chat.title == "Hello council"
    assert chat.updated_at >= before
    assert turn.chat_title == "Hello council"
    assert turn.chat_updated_at is not None


@pytest.mark.asyncio
async def test_later_turn_bumps_updated_at_and_list_order(db, auth):
    model_set = await create_model_set(db, auth)
    older = await _create_chat(db, auth, title="Older chat")
    newer = await _create_chat(db, auth, title="Newer chat")

    # Make older appear more recent initially.
    older.updated_at = datetime.now(UTC)
    newer.updated_at = datetime.now(UTC) - timedelta(hours=1)
    await db.flush()

    await chat_service.start_turn(
        db,
        auth,
        newer.id,
        TurnCreateRequest(user_message="Follow-up question", model_set_id=model_set.slug),
    )

    listed = await chat_service.list_chats(db, auth)
    assert listed[0].id == newer.id
    assert listed[0].title == "Newer chat"


@pytest.mark.asyncio
async def test_list_turns_does_not_bump_updated_at(db, auth):
    model_set = await create_model_set(db, auth)
    chat = await _create_chat(db, auth, title="Stable")
    await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(user_message="First", model_set_id=model_set.slug),
    )
    await db.refresh(chat)
    stamped = chat.updated_at

    await chat_service.list_turns(db, auth, chat.id)
    await db.refresh(chat)
    assert chat.updated_at == stamped


@pytest.mark.asyncio
async def test_delete_only_if_unused_rejects_chat_with_turns(db, auth):
    model_set = await create_model_set(db, auth)
    chat = await _create_chat(db, auth)
    await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(user_message="Keep me", model_set_id=model_set.slug),
    )

    from app.core.exceptions import ConflictError

    with pytest.raises(ConflictError):
        await chat_service.delete_chat(db, auth, chat.id, only_if_unused=True)

    still = (
        await db.execute(select(Chat).where(Chat.id == chat.id))
    ).scalar_one_or_none()
    assert still is not None
    turns = (
        await db.execute(select(Turn).where(Turn.chat_id == chat.id))
    ).scalars().all()
    assert len(turns) == 1


@pytest.mark.asyncio
async def test_delete_only_if_unused_allows_empty_new_chat(db, auth):
    chat = await _create_chat(db, auth)
    chat_id = chat.id
    await chat_service.delete_chat(db, auth, chat_id, only_if_unused=True)
    gone = (
        await db.execute(select(Chat).where(Chat.id == chat_id))
    ).scalar_one_or_none()
    assert gone is None
