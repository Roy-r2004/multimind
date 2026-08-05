"""Hammer Gemini until content is null / finish_reason error; dump full choice."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from app.db.session import AsyncSessionLocal
from app.db.models import Turn
from app.llm.catalog import get_model
from app.llm.prompt_engine import PromptEngine
from app.llm.providers import DEFAULT_LLM_TEMPERATURE, OPENROUTER_CHAT_URL, OpenRouterProvider

TURN_ID = "5a91dd7c-d386-4777-8d16-de77ba2f86fc"
ATTEMPTS = 8


def shape(msg: dict[str, Any]) -> dict[str, Any]:
    out = {"keys": sorted(msg.keys())}
    for k, v in msg.items():
        if v is None:
            out[k] = None
        elif isinstance(v, str):
            out[k] = {"len": len(v), "preview": v[:800]}
        else:
            s = json.dumps(v, default=str)
            out[k] = {"json_len": len(s), "preview": s[:1200]}
    return out


async def one(system: str, user: str) -> dict[str, Any]:
    provider = OpenRouterProvider()
    model = get_model("gemini").provider_model
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": DEFAULT_LLM_TEMPERATURE,
        "max_tokens": 4096,
        "usage": {"include": True},
    }
    async with httpx.AsyncClient(timeout=provider._timeout) as client:
        resp = await client.post(OPENROUTER_CHAT_URL, headers=provider._headers(), json=payload)
        data = resp.json()
    choice = (data.get("choices") or [None])[0]
    msg = (choice or {}).get("message") or {}
    usage = data.get("usage") or {}
    return {
        "http_status": resp.status_code,
        "id": data.get("id"),
        "finish_reason": (choice or {}).get("finish_reason"),
        "native_finish_reason": (choice or {}).get("native_finish_reason"),
        "choice_error": (choice or {}).get("error"),
        "top_error": data.get("error"),
        "content_is_null": msg.get("content") is None,
        "content_type": type(msg.get("content")).__name__,
        "usage_cost": usage.get("cost"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
        "message": shape(msg),
        "raw_choice_keys": sorted((choice or {}).keys()),
    }


async def main() -> None:
    async with AsyncSessionLocal() as db:
        turn = (await db.execute(__import__("sqlalchemy").select(Turn).where(Turn.id == TURN_ID))).scalar_one()
        custom = turn.custom_instructions or ""
        user = turn.user_message
    model = get_model("gemini")
    system = PromptEngine().model_answer_prompt(
        user_message=user,
        model_id=model.id,
        model_name=model.name,
        vendor=model.vendor,
        model_set_name="hammer",
        custom_instructions=custom,
    )
    results = []
    null_hits = []
    for i in range(ATTEMPTS):
        r = await one(system, user)
        r["attempt"] = i + 1
        results.append(
            {
                "attempt": i + 1,
                "finish_reason": r["finish_reason"],
                "content_is_null": r["content_is_null"],
                "usage_cost": r["usage_cost"],
                "completion_tokens": r["completion_tokens"],
                "reasoning_tokens": r["reasoning_tokens"],
                "choice_error": r["choice_error"],
                "top_error": r["top_error"],
            }
        )
        if r["content_is_null"] or r["finish_reason"] == "error":
            null_hits.append(r)
    print(
        json.dumps(
            {
                "attempts": ATTEMPTS,
                "null_or_error_count": len(null_hits),
                "summaries": results,
                "null_hits_full": null_hits,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
