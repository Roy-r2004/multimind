"""Chat title changes fan out only after commit, on the owning org channel."""

import asyncio
import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.schemas.api import ChatCreateRequest, ChatUpdateRequest
from app.services.chat_service import chat_service
from app.services.chat_title_events import iter_chat_title_sse, title_channel
from tests.conftest import create_other_auth


class _Request:
    def __init__(self, disconnect_after: int) -> None:
        self._checks = 0
        self.disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        self._checks += 1
        return self._checks > self.disconnect_after


class _PubSub:
    def __init__(self, message: dict | None) -> None:
        self.message = message
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 0):
        message = self.message
        self.message = None
        return message

    async def aclose(self) -> None:
        self.closed = True


class _Redis:
    def __init__(self, pubsub: _PubSub) -> None:
        self.pubsub_client = pubsub
        self.closed = False

    def pubsub(self) -> _PubSub:
        return self.pubsub_client

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_title_publish_waits_for_commit_and_stays_in_org(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    published: list[dict] = []

    async def capture(**kwargs):
        published.append(kwargs)

    monkeypatch.setattr("app.services.chat_title_events.publish_chat_title", capture)
    other = await create_other_auth(db)
    chat = await chat_service.create_chat(db, auth, ChatCreateRequest(title="Original"))
    await db.commit()

    await chat_service.update_chat(db, auth, chat.id, ChatUpdateRequest(title="Renamed"))
    await db.rollback()
    await asyncio.sleep(0)
    assert published == []

    await chat_service.update_chat(db, auth, chat.id, ChatUpdateRequest(title="Renamed"))
    await db.commit()
    await asyncio.sleep(0)
    assert published == [{"org_id": auth.org_id, "chat_id": chat.id, "title": "Renamed"}]
    assert published[0]["org_id"] != other.org_id

    await chat_service.update_chat(db, auth, chat.id, ChatUpdateRequest(title="Renamed"))
    await db.commit()
    await asyncio.sleep(0)
    assert len(published) == 1

    await chat_service.update_chat(
        db, auth, chat.id, ChatUpdateRequest(project_id=None)
    )
    await db.commit()
    await asyncio.sleep(0)
    assert len(published) == 1


@pytest.mark.asyncio
async def test_title_stream_delivers_one_org_and_closes_subscription():
    payload = json.dumps({"chat_id": "chat-1", "title": "From the other window"})
    pubsub = _PubSub({"type": "message", "data": payload})
    redis = _Redis(pubsub)
    request = _Request(disconnect_after=1)

    frames = [
        frame
        async for frame in iter_chat_title_sse("org-a", request, redis_client=redis)
    ]

    assert pubsub.subscribed == [title_channel("org-a")]
    assert frames == [
        ": connected\n\n",
        f"event: chat_title\ndata: {payload}\n\n",
    ]
    assert pubsub.unsubscribed == [title_channel("org-a")]
    assert pubsub.closed is True
    assert redis.closed is False
