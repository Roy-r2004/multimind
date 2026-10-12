"""Org-scoped chat title fan-out over Redis Pub/Sub.

Events are queued on the SQLAlchemy session and published only from
``after_commit``, so a rollback never notifies other sessions.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from redis.asyncio import Redis
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_EVENTS_KEY = "chat_title_events"
_HOOK_KEY = "chat_title_events_hook"
_HEARTBEAT_SECONDS = 15.0


def title_channel(org_id: str) -> str:
    return f"chat:titles:{org_id}"


def _queue_event(db: AsyncSession, payload: dict) -> None:
    sync = db.sync_session
    sync.info.setdefault(_EVENTS_KEY, []).append(payload)
    # Captured here, on the request task. after_commit can run in a greenlet
    # where get_running_loop() fails, which previously dropped the publish.
    try:
        sync.info["chat_events_loop"] = asyncio.get_running_loop()
    except RuntimeError:
        pass
    if sync.info.get(_HOOK_KEY):
        return
    sync.info[_HOOK_KEY] = True
    event.listen(sync, "after_commit", _publish_after_commit)
    event.listen(sync, "after_rollback", _discard_after_rollback)


def schedule_chat_title_event(
    db: AsyncSession,
    *,
    org_id: str,
    chat_id: str,
    title: str,
    updated_at: str,
) -> None:
    _queue_event(
        db,
        {
            "kind": "title",
            "org_id": org_id,
            "chat_id": chat_id,
            "title": title,
            "updated_at": updated_at,
        },
    )


def schedule_chat_deleted_event(db: AsyncSession, *, org_id: str, chat_id: str) -> None:
    _queue_event(db, {"kind": "deleted", "org_id": org_id, "chat_id": chat_id})


def schedule_chat_created_event(
    db: AsyncSession,
    *,
    org_id: str,
    chat_id: str,
    title: str,
    project_id: str | None,
    model_set_id: str | None,
    updated_at: str,
) -> None:
    _queue_event(
        db,
        {
            "kind": "created",
            "org_id": org_id,
            "chat_id": chat_id,
            "title": title,
            "project_id": project_id,
            "model_set_id": model_set_id,
            "updated_at": updated_at,
        },
    )


def _discard_after_rollback(session) -> None:
    session.info.pop(_EVENTS_KEY, None)


def _publish_after_commit(session) -> None:
    events = list(session.info.pop(_EVENTS_KEY, []))
    if not events:
        return
    loop = session.info.get("chat_events_loop")
    if loop is None or getattr(loop, "is_closed", lambda: False)():
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("chat_title_publish_skipped_no_loop")
            return
    for queued in events:
        kind = queued.get("kind", "title")
        if kind == "created":
            coro = publish_chat_created(
                org_id=queued["org_id"],
                chat_id=queued["chat_id"],
                title=queued["title"],
                project_id=queued.get("project_id"),
                model_set_id=queued.get("model_set_id"),
                updated_at=queued.get("updated_at") or "",
            )
        elif kind == "deleted":
            coro = publish_chat_deleted(org_id=queued["org_id"], chat_id=queued["chat_id"])
        else:
            coro = publish_chat_title(
                org_id=queued["org_id"],
                chat_id=queued["chat_id"],
                title=queued["title"],
                updated_at=queued.get("updated_at") or "",
            )
        task = loop.create_task(coro)
        task.add_done_callback(_log_task_failure)


def _log_task_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("chat_title_publish_failed", exc_info=exc)


def _sse_event_name(data: str) -> str:
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return "chat_title"
    if isinstance(parsed, dict) and parsed.get("type") == "chat.created":
        return "chat.created"
    if isinstance(parsed, dict) and parsed.get("type") == "chat.deleted":
        return "chat.deleted"
    return "chat_title"


async def publish_chat_created(
    *,
    org_id: str,
    chat_id: str,
    title: str,
    project_id: str | None,
    model_set_id: str | None,
    updated_at: str,
) -> None:
    payload = json.dumps(
        {
            "type": "chat.created",
            "chat_id": chat_id,
            "title": title,
            "project_id": project_id,
            "model_set_id": model_set_id,
            "updated_at": updated_at,
        },
        separators=(",", ":"),
    )
    client = Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=1.0,
    )
    try:
        await client.publish(title_channel(org_id), payload)
    finally:
        await client.aclose()


async def publish_chat_deleted(*, org_id: str, chat_id: str) -> None:
    payload = json.dumps(
        {"type": "chat.deleted", "chat_id": chat_id},
        separators=(",", ":"),
    )
    client = Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=1.0,
    )
    try:
        await client.publish(title_channel(org_id), payload)
    finally:
        await client.aclose()


async def publish_chat_title(*, org_id: str, chat_id: str, title: str, updated_at: str) -> None:
    payload = json.dumps(
        {"chat_id": chat_id, "title": title, "updated_at": updated_at},
        separators=(",", ":"),
    )
    client = Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=1.0,
    )
    try:
        await client.publish(title_channel(org_id), payload)
    finally:
        await client.aclose()


async def iter_chat_title_sse(
    org_id: str,
    request: Request,
    *,
    redis_client: Redis | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames for one org. Closes the Redis subscription on exit."""
    owns_client = redis_client is None
    client = redis_client or Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        socket_connect_timeout=1.0,
    )
    pubsub = client.pubsub()
    await pubsub.subscribe(title_channel(org_id))
    try:
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                return
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=_HEARTBEAT_SECONDS,
            )
            if message is None:
                yield ": heartbeat\n\n"
                continue
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode()
            if not isinstance(data, str) or not data:
                continue
            yield f"event: {_sse_event_name(data)}\ndata: {data}\n\n"
    finally:
        try:
            await pubsub.unsubscribe(title_channel(org_id))
        finally:
            await pubsub.aclose()
            if owns_client:
                await client.aclose()
