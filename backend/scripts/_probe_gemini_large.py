"""Focused probe: large Gemini call — dump finish_reason/error + full message fields."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.llm.providers import (
    DEFAULT_LLM_TEMPERATURE,
    OPENROUTER_CHAT_URL,
    OpenRouterProvider,
    _content_to_text,
)


SHORT_SYSTEM = (
    "You are answering as one model in a multi-model panel. "
    "Give a direct, useful answer. End with CONFIDENCE: <0-100>."
)
LARGE_ATTACHMENT_BLOCK = (
    "=== ATTACHED FILES (excerpts) ===\n"
    + ("Paragraph about evidence and chain of custody. " * 200)
    + "\n=== END ATTACHED FILES ===\n"
)
LARGE_SYSTEM = SHORT_SYSTEM + "\n\n" + LARGE_ATTACHMENT_BLOCK
LARGE_USER = "Summarize the attached evidence in 3 bullets."


def truncate(v: Any, n: int = 4000) -> Any:
    if isinstance(v, str) and len(v) > n:
        return v[:n] + f"…[truncated len={len(v)}]"
    if isinstance(v, list):
        return [truncate(x, n) for x in v]
    if isinstance(v, dict):
        return {k: truncate(val, n) for k, val in v.items()}
    return v


async def call(system: str, user: str, *, max_tokens: int = 4096, extra: dict | None = None) -> dict:
    provider = OpenRouterProvider()
    payload: dict[str, Any] = {
        "model": "google/gemini-2.5-pro",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": DEFAULT_LLM_TEMPERATURE,
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    if extra:
        payload.update(extra)
    async with httpx.AsyncClient(timeout=provider._timeout) as client:
        resp = await client.post(
            OPENROUTER_CHAT_URL,
            headers=provider._headers(),
            json=payload,
        )
        data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content_raw = msg.get("content", "__MISSING__")
    parsed = _content_to_text(msg.get("content", ""))
    return {
        "http_status": resp.status_code,
        "id": data.get("id"),
        "model": data.get("model"),
        "error_top_level": data.get("error"),
        "finish_reason": choice.get("finish_reason"),
        "native_finish_reason": choice.get("native_finish_reason"),
        "choice_error": choice.get("error"),
        "usage": data.get("usage"),
        "message_keys": sorted(msg.keys()),
        "content_is_null": content_raw is None,
        "content_raw_type": type(content_raw).__name__,
        "parsed_via_current": {"repr": repr(parsed)[:80], "equals_None_string": parsed == "None"},
        "message_truncated": truncate(msg, 6000),
        "system_chars": len(system),
        "user_chars": len(user),
        "prompt_tokens_reported": (data.get("usage") or {}).get("prompt_tokens"),
    }


async def main() -> None:
    out = {
        "large_default": await call(LARGE_SYSTEM, LARGE_USER),
        "large_max_tokens_8192": await call(LARGE_SYSTEM, LARGE_USER, max_tokens=8192),
        "large_reasoning_max_tokens_1024": await call(
            LARGE_SYSTEM,
            LARGE_USER,
            extra={"reasoning": {"max_tokens": 1024}},
        ),
        "large_reasoning_effort_low": await call(
            LARGE_SYSTEM,
            LARGE_USER,
            extra={"reasoning": {"effort": "low"}},
        ),
    }
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
