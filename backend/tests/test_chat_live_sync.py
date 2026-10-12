"""Two subscribers on one org channel see create, generated title, and delete.

Publish and the SSE iterator share an in-memory Redis stand-in, so this
exercises the same path two browser tabs use. A rolled-back create and a
second organization receive nothing.
"""

import asyncio
from collections import defaultdict

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.schemas.api import ChatCreateRequest, ChatUpdateRequest, TurnCreateRequest
from app.services import chat_title_events
from app.services.chat_service import chat_service
from tests.conftest import create_model_set, create_other_auth


class _Request:
    def __init__(self) -> None:
        self.stop = False

    async def is_disconnected(self) -> bool:
        return self.stop


class _PubSub:
    def __init__(self, queues: dict[str, list[asyncio.Queue]]) -> None:
        self.queues = queues
        self.queue: asyncio.Queue | None = None

    async def subscribe(self, channel: str) -> None:
        self.queue = asyncio.Queue()
        self.queues[channel].append(self.queue)

    async def unsubscribe(self, channel: str) -> None:
        return None

    async def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 0):
        assert self.queue is not None
        deadline = timeout or 0.05
        waited = 0.0
        while waited < deadline:
            try:
                data = await asyncio.wait_for(self.queue.get(), timeout=min(0.05, deadline - waited))
            except TimeoutError:
                waited += 0.05
                continue
            return {"type": "message", "data": data}
        return None

    async def aclose(self) -> None:
        return None


class _Client:
    def __init__(self, queues: dict[str, list[asyncio.Queue]]) -> None:
        self.queues = queues

    def pubsub(self) -> _PubSub:
        return _PubSub(self.queues)

    async def publish(self, channel: str, payload: str) -> None:
        for queue in list(self.queues.get(channel, [])):
            await queue.put(payload)

    async def aclose(self) -> None:
        return None


class _Redis:
    queues: dict[str, list[asyncio.Queue]] = {}

    @classmethod
    def from_url(cls, *args, **kwargs) -> _Client:
        return _Client(cls.queues)


async def _collect(org_id: str, request: _Request, frames: list[str], stop_at: int) -> None:
    async for frame in chat_title_events.iter_chat_title_sse(org_id, request):
        if frame.startswith("event:"):
            frames.append(frame)
            if len(frames) >= stop_at:
                request.stop = True


@pytest.mark.asyncio
async def test_two_tabs_receive_create_generated_title_and_delete(
    db: AsyncSession, auth: AuthContext, monkeypatch: pytest.MonkeyPatch
):
    queues: dict[str, list[asyncio.Queue]] = defaultdict(list)
    _Redis.queues = queues
    monkeypatch.setattr(chat_title_events, "Redis", _Redis)

    other = await create_other_auth(db)
    model_set = await create_model_set(db, auth)
    await db.commit()
    model_set_slug = model_set.slug
    tab_a: list[str] = []
    tab_b: list[str] = []
    outsider: list[str] = []
    request_a, request_b, request_other = _Request(), _Request(), _Request()
    tasks = [
        asyncio.create_task(_collect(auth.org_id, request_a, tab_a, 4)),
        asyncio.create_task(_collect(auth.org_id, request_b, tab_b, 4)),
        asyncio.create_task(_collect(other.org_id, request_other, outsider, 1)),
    ]
    for _ in range(50):
        if len(queues[chat_title_events.title_channel(auth.org_id)]) >= 2:
            break
        await asyncio.sleep(0.01)
    assert len(queues[chat_title_events.title_channel(auth.org_id)]) == 2

    chat = await chat_service.create_chat(db, auth, ChatCreateRequest(title="New chat"))
    await db.rollback()
    await db.refresh(auth.user)
    await asyncio.sleep(0.05)
    assert tab_a == [] and tab_b == []

    chat = await chat_service.create_chat(db, auth, ChatCreateRequest(title="New chat"))
    await db.commit()
    await chat_service.start_turn(
        db,
        auth,
        chat.id,
        TurnCreateRequest(user_message="Hello council", model_set_id=model_set_slug),
    )
    await db.commit()
    await chat_service.update_chat(db, auth, chat.id, ChatUpdateRequest(title="Renamed live"))
    await db.commit()
    await chat_service.delete_chat(db, auth, chat.id)

    await asyncio.wait_for(asyncio.gather(tasks[0], tasks[1]), timeout=5)
    tasks[2].cancel()
    with pytest.raises(asyncio.CancelledError):
        await tasks[2]

    assert outsider == []
    assert tab_a == tab_b
    joined = "\n".join(tab_a)
    assert "event: chat.created" in joined
    assert "Hello council" in joined
    assert "Renamed live" in joined
    assert "event: chat.deleted" in joined
    assert joined.index("event: chat.created") < joined.index("Hello council")
    assert joined.index("Hello council") < joined.index("Renamed live")
    assert joined.index("Renamed live") < joined.index("event: chat.deleted")
