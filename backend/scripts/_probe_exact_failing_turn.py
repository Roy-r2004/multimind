"""Replay the exact failing turn prompt against Gemini/GPT/Claude. Prints redacted shapes only."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import httpx
from sqlalchemy import select, text

from app.db.session import AsyncSessionLocal
from app.llm.catalog import get_model
from app.llm.prompt_engine import PromptEngine
from app.llm.providers import (
    DEFAULT_LLM_TEMPERATURE,
    OPENROUTER_CHAT_URL,
    OpenRouterProvider,
    _content_to_text,
)
from app.db.models import Turn


TURN_ID = "5a91dd7c-d386-4777-8d16-de77ba2f86fc"
MODELS = ["gemini", "gpt-4.1-mini", "claude"]


def summarize_msg(msg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"keys": sorted(msg.keys())}
    for k, v in msg.items():
        if v is None:
            out[k] = {"type": "null"}
        elif isinstance(v, str):
            out[k] = {
                "type": "str",
                "len": len(v),
                "preview": v[:400] + ("…" if len(v) > 400 else ""),
            }
        elif isinstance(v, list):
            preview = json.dumps(v, default=str)
            out[k] = {
                "type": "list",
                "len": len(v),
                "preview": preview[:600] + ("…" if len(preview) > 600 else ""),
            }
        else:
            out[k] = {"type": type(v).__name__, "preview": repr(v)[:200]}
    return out


async def call_raw(system: str, user: str, model_slug: str, max_tokens: int = 4096) -> dict:
    provider = OpenRouterProvider()
    payload = {
        "model": model_slug,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": DEFAULT_LLM_TEMPERATURE,
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    async with httpx.AsyncClient(timeout=provider._timeout) as client:
        resp = await client.post(
            OPENROUTER_CHAT_URL,
            headers=provider._headers(),
            json=payload,
        )
        data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content = msg.get("content", "")
    parsed = _content_to_text(content)
    return {
        "http_status": resp.status_code,
        "model_returned": data.get("model"),
        "finish_reason": choice.get("finish_reason"),
        "native_finish_reason": choice.get("native_finish_reason"),
        "choice_error": choice.get("error"),
        "top_error": data.get("error"),
        "usage": data.get("usage"),
        "message": summarize_msg(msg),
        "parsed_current": {
            "repr": repr(parsed)[:120],
            "equals_None_string": parsed == "None",
            "len": len(parsed) if isinstance(parsed, str) else None,
        },
        "request": {
            "model": model_slug,
            "max_tokens": max_tokens,
            "temperature": DEFAULT_LLM_TEMPERATURE,
            "system_chars": len(system),
            "user_chars": len(user),
            "system_preview": system[:250],
            "user": user,
            "provider_options": None,
        },
    }


async def main() -> None:
    async with AsyncSessionLocal() as db:
        turn = (
            await db.execute(select(Turn).where(Turn.id == TURN_ID))
        ).scalar_one()
        custom = turn.custom_instructions or ""
        user_message = turn.user_message

    prompts = PromptEngine()
    results = {}
    for model_id in MODELS:
        model = get_model(model_id)
        system = prompts.model_answer_prompt(
            user_message=user_message,
            model_id=model.id,
            model_name=model.name,
            vendor=model.vendor,
            model_set_name="replay",
            custom_instructions=custom,
        )
        # Also measure where custom_instructions land
        results[f"{model_id}_meta"] = {
            "provider_model": model.provider_model,
            "system_chars": len(system),
            "custom_in_system": custom[:80] in system if custom else False,
            "user_message_in_system": user_message in system,
        }
        try:
            results[model_id] = await call_raw(system, user_message, model.provider_model)
        except Exception as exc:  # noqa: BLE001
            results[model_id] = {"error": str(exc)}

    # Extra: Gemini twice to see if null content is intermittent with this exact prompt
    model = get_model("gemini")
    system = prompts.model_answer_prompt(
        user_message=user_message,
        model_id=model.id,
        model_name=model.name,
        vendor=model.vendor,
        model_set_name="replay",
        custom_instructions=custom,
    )
    results["gemini_retry_2"] = await call_raw(system, user_message, model.provider_model)
    results["gemini_retry_3"] = await call_raw(system, user_message, model.provider_model)

    # Size diagnostics
    results["sizes"] = {
        "user_message_chars": len(user_message),
        "custom_instructions_chars": len(custom),
        "system_prompt_chars_gemini": len(system),
        "approx_system_tokens_chars_div_4": len(system) // 4,
        "approx_custom_tokens_chars_div_4": len(custom) // 4,
    }
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
