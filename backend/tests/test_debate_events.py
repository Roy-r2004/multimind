"""Phase 4: Debate Mode SSE event fan-out (app.services.debate_events).

Mirrors test_chat_title_events.py's fake Redis/pubsub pattern rather than
needing a live Redis — iter_debate_sse accepts an injected redis_client for
exactly this reason.
"""

import json

import pytest

from app.services.debate_events import (
    DEBATE_EVENT_NAMES,
    TERMINAL_DEBATE_EVENT_NAMES,
    debate_channel,
    iter_debate_sse,
    publish_debate_event,
)


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


async def test_sse_stream_subscribes_to_the_session_scoped_channel_and_yields_frames():
    message = {
        "type": "message",
        "data": json.dumps({"event": "debate_challenge_completed", "session_id": "s1", "ts": "t", "data": {}}),
    }
    pubsub = _PubSub(message)
    redis = _Redis(pubsub)
    request = _Request(disconnect_after=1)

    frames = [frame async for frame in iter_debate_sse("s1", request, redis_client=redis)]

    assert pubsub.subscribed == [debate_channel("s1")]
    assert frames[0] == ": connected\n\n"
    assert any(f.startswith("event: debate_challenge_completed\n") for f in frames)
    assert pubsub.unsubscribed == [debate_channel("s1")]
    assert pubsub.closed is True
    # An injected redis_client is caller-owned (mirrors chat_title_events.py's
    # iter_chat_title_sse) — the generator must not close it.
    assert redis.closed is False


async def test_sse_stream_emits_heartbeats_when_no_message_is_pending():
    pubsub = _PubSub(message=None)
    redis = _Redis(pubsub)
    request = _Request(disconnect_after=2)

    frames = [frame async for frame in iter_debate_sse("s2", request, redis_client=redis)]

    assert frames.count(": heartbeat\n\n") == 2


async def test_sse_stream_stops_on_disconnect_without_leaking_the_subscription():
    pubsub = _PubSub(message=None)
    redis = _Redis(pubsub)
    request = _Request(disconnect_after=0)

    frames = [frame async for frame in iter_debate_sse("s3", request, redis_client=redis)]

    assert frames == [": connected\n\n"]
    assert pubsub.closed is True


async def test_publish_rejects_an_unknown_event_name():
    with pytest.raises(ValueError):
        await publish_debate_event("s1", "not_a_real_debate_event", {})


async def test_publish_swallows_a_redis_outage_instead_of_raising(monkeypatch: pytest.MonkeyPatch):
    import app.services.debate_events as module

    class _BrokenRedis:
        @staticmethod
        def from_url(*args, **kwargs):
            raise ConnectionError("no redis here")

    monkeypatch.setattr(module, "Redis", _BrokenRedis)
    # Must not raise — a Redis outage degrades live streaming, never execution/persistence.
    await publish_debate_event("s1", "debate_started", {"participant_count": 2})


def test_every_lifecycle_event_name_required_by_phase_4_is_recognized():
    # The Phase 4 brief names these without the "debate_" prefix on
    # challenge/response events; our wire names add it uniformly
    # (debate_challenge_started, not challenge_started) to disambiguate from
    # Council's own event names on any shared client-side event bus.
    required_suffixes = {
        "started",
        "challenge_started",
        "challenge_completed",
        "challenge_failed",
        "response_started",
        "response_completed",
        "response_failed",
        "findings_ready",
        "conclusion_started",
        "conclusion_completed",
        "conclusion_failed",
        "completed",
        "partial",
        "failed",
        "cancelled",
    }
    actual_suffixes = {name.removeprefix("debate_") for name in DEBATE_EVENT_NAMES}
    assert required_suffixes == actual_suffixes
    assert {"debate_completed", "debate_partial", "debate_failed", "debate_cancelled"} == TERMINAL_DEBATE_EVENT_NAMES
