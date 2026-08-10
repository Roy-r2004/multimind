"""Chat-level model set selection for the next message."""

import pytest
from sqlalchemy import select

from app.core.exceptions import NotFoundError
from app.db.models import Chat, Turn
from app.schemas.api import ChatCreateRequest, ChatUpdateRequest, TurnCreateRequest
from app.services.chat_service import chat_service
from tests.conftest import AuthContext, create_model_set


@pytest.mark.asyncio
async def test_update_chat_persists_model_set_id(db, auth: AuthContext):
    set_a = await create_model_set(db, auth, slug="council-a")
    set_b = await create_model_set(db, auth, slug="council-b")
    chat = await chat_service.create_chat(
        db, auth, ChatCreateRequest(title="Switchable", model_set_id=set_a.slug)
    )
    assert chat.model_set_id == "council-a"

    updated = await chat_service.update_chat(
        db, auth, chat.id, ChatUpdateRequest(model_set_id=set_b.slug)
    )
    assert updated.model_set_id == "council-b"

    loaded = (
        await db.execute(select(Chat).where(Chat.id == chat.id))
    ).scalar_one()
    assert loaded.model_set_id == "council-b"


@pytest.mark.asyncio
async def test_update_chat_rejects_unknown_model_set(db, auth: AuthContext):
    chat = await chat_service.create_chat(db, auth, ChatCreateRequest(title="No set"))
    with pytest.raises(NotFoundError):
        await chat_service.update_chat(
            db, auth, chat.id, ChatUpdateRequest(model_set_id="missing-set")
        )


@pytest.mark.asyncio
async def test_start_turn_uses_request_set_and_syncs_chat(db, auth: AuthContext):
    set_a = await create_model_set(db, auth, slug="first-set")
    set_b = await create_model_set(db, auth, slug="second-set")
    chat = await chat_service.create_chat(
        db, auth, ChatCreateRequest(title="Mixed", model_set_id=set_a.slug)
    )

    turn_a = await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(user_message="With A", model_set_id=set_a.slug),
    )
    turn_b = await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(user_message="With B", model_set_id=set_b.slug),
    )

    assert turn_a.model_set_id == "first-set"
    assert turn_b.model_set_id == "second-set"

    loaded = (
        await db.execute(select(Chat).where(Chat.id == chat.id))
    ).scalar_one()
    assert loaded.model_set_id == "second-set"

    turns = (
        await db.execute(select(Turn).where(Turn.chat_id == chat.id).order_by(Turn.created_at))
    ).scalars().all()
    assert [t.model_set_id for t in turns] == ["first-set", "second-set"]


@pytest.mark.asyncio
async def test_switching_chat_model_set_does_not_rewrite_old_turns(db, auth: AuthContext):
    set_a = await create_model_set(db, auth, slug="keep-a")
    set_b = await create_model_set(db, auth, slug="next-b")
    chat = await chat_service.create_chat(db, auth, ChatCreateRequest(title="History"))
    turn = await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(user_message="Original", model_set_id=set_a.slug),
    )

    await chat_service.update_chat(
        db, auth, chat.id, ChatUpdateRequest(model_set_id=set_b.slug)
    )

    stored = (
        await db.execute(select(Turn).where(Turn.id == turn.id))
    ).scalar_one()
    assert stored.model_set_id == "keep-a"
    listed = await chat_service.list_chats(db, auth)
    assert next(c for c in listed if c.id == chat.id).model_set_id == "next-b"
