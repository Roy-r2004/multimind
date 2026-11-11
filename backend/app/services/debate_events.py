"""Debate Mode SSE event fan-out over Redis Pub/Sub.

Mirrors ``app.services.chat_title_events``/the scraping execution service's
``_publish_event`` pattern: a session-scoped channel, best-effort publish
(a dead/unreachable Redis must never fail debate persistence or execution —
only live streaming degrades), and a generator that supports any number of
simultaneous subscribers since each SSE connection opens its own independent
``pubsub()`` subscription. Nothing here persists anything; it only notifies
already-connected clients of state this module's callers have already
committed to the database. A reconnecting client re-fetches the durable
snapshot via the debate GET endpoint and then resubscribes here — a missed
event while disconnected is never the only place that information lives.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from redis.asyncio import Redis
from starlette.requests import Request

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_HEARTBEAT_SECONDS = 15.0

# Stable set of lifecycle events a subscriber can expect. ``debate_started``
# fires once; terminal events (``debate_completed``/``debate_partial``/
# ``debate_failed``/``debate_cancelled``) fire exactly once and end the
# stream for well-behaved clients (the generator itself keeps listening until
# the request disconnects, since late reconnects should still get a
# heartbeat rather than a closed connection).
DEBATE_EVENT_NAMES = frozenset(
    {
        "debate_started",
        "debate_challenge_started",
        "debate_challenge_completed",
        "debate_challenge_failed",
        "debate_response_started",
        "debate_response_completed",
        "debate_response_failed",
        "debate_findings_ready",
        "debate_completed",
        "debate_partial",
        "debate_failed",
        "debate_cancelled",
    }
)

TERMINAL_DEBATE_EVENT_NAMES = frozenset(
    {"debate_completed", "debate_partial", "debate_failed", "debate_cancelled"}
)


class DebateSSEEvent(BaseModel):
    """Stable wire contract for one Debate Mode SSE frame."""

    model_config = ConfigDict(extra="forbid")

    event: str
    session_id: str
    ts: str
    data: dict[str, Any]


def debate_channel(session_id: str) -> str:
    return f"debate:{session_id}:events"


async def publish_debate_event(session_id: str, event: str, data: dict[str, Any]) -> None:
    """Best-effort publish. Never raises — a Redis outage must not abort
    debate execution or persistence, only live streaming for that moment."""
    if event not in DEBATE_EVENT_NAMES:
        raise ValueError(f"unknown debate SSE event name: {event!r}")
    payload = DebateSSEEvent(
        event=event, session_id=session_id, ts=datetime.now(UTC).isoformat(), data=data
    )
    try:
        client = Redis.from_url(
            get_settings().redis_url, decode_responses=True, socket_connect_timeout=1.0
        )
        try:
            await client.publish(
                debate_channel(session_id), payload.model_dump_json()
            )
        finally:
            await client.aclose()
    except Exception:
        logger.warning(
            "debate_event_publish_failed", session_id=session_id, sse_event=event, exc_info=True
        )


async def iter_debate_sse(
    session_id: str,
    request: Request,
    *,
    redis_client: Redis | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames for one debate session. Multiple independent callers
    may subscribe concurrently (each gets its own ``pubsub()``), so two
    browser tabs never steal each other's events."""
    owns_client = redis_client is None
    client = redis_client or Redis.from_url(
        get_settings().redis_url, decode_responses=True, socket_connect_timeout=1.0
    )
    pubsub = client.pubsub()
    await pubsub.subscribe(debate_channel(session_id))
    try:
        yield ": connected\n\n"
        while True:
            if await request.is_disconnected():
                return
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_HEARTBEAT_SECONDS
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
            try:
                event_name = json.loads(data).get("event", "message")
            except json.JSONDecodeError:
                event_name = "message"
            yield f"event: {event_name}\ndata: {data}\n\n"
    finally:
        try:
            await pubsub.unsubscribe(debate_channel(session_id))
        finally:
            await pubsub.aclose()
            if owns_client:
                await client.aclose()
