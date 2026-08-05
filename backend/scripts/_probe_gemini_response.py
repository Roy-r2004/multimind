"""One-shot OpenRouter probe: Gemini vs GPT vs Claude. Redacts secrets. Do not commit results."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from app.llm.providers import OpenRouterProvider, _content_to_text


MODELS = {
    "gemini": "google/gemini-2.5-pro",
    "gpt": "openai/gpt-4.1-mini",
    "claude": "anthropic/claude-sonnet-4",
}

SHORT_SYSTEM = (
    "You are answering as one model in a multi-model panel. "
    "Give a direct, useful answer. End with CONFIDENCE: <0-100>."
)
SHORT_USER = "In one sentence: what is 2+2?"

# Approximate attachment-sized system prompt (chars ~ tokens*4 for rough sizing).
LARGE_ATTACHMENT_BLOCK = (
    "=== ATTACHED FILES (excerpts) ===\n"
    + ("Paragraph about evidence and chain of custody. " * 200)
    + "\n=== END ATTACHED FILES ===\n"
)
LARGE_SYSTEM = SHORT_SYSTEM + "\n\n" + LARGE_ATTACHMENT_BLOCK
LARGE_USER = "Summarize the attached evidence in 3 bullets."


def _summarize_value(v: Any, *, max_chars: int = 240) -> Any:
    if v is None:
        return {"type": "null", "value": None}
    if isinstance(v, str):
        return {
            "type": "str",
            "len": len(v),
            "preview": v[:max_chars] + ("…" if len(v) > max_chars else ""),
        }
    if isinstance(v, list):
        return {
            "type": "list",
            "len": len(v),
            "item_types": [type(x).__name__ for x in v[:8]],
            "preview": json.dumps(v, default=str)[:max_chars],
        }
    if isinstance(v, dict):
        return {
            "type": "dict",
            "keys": sorted(v.keys()),
            "preview": json.dumps(v, default=str)[:max_chars],
        }
    return {"type": type(v).__name__, "value": repr(v)[:max_chars]}


def _shape_message(msg: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"keys": sorted(msg.keys())}
    for k, v in msg.items():
        out[k] = _summarize_value(v)
    return out


def _shape_usage(usage: Any) -> Any:
    if not isinstance(usage, dict):
        return usage
    # Keep full usage — no secrets here.
    return usage


async def probe_one(
    provider: OpenRouterProvider,
    *,
    label: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int = 4096,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Call underlying once but also capture raw via monkey-patch path:
    # Use complete() which stores raw on LLMResponse.
    # For extra_payload we need a direct HTTP path — temporarily inject via complete_once payload.
    if extra_payload:
        # Direct low-level call mirroring providers.py
        import httpx
        from app.llm.providers import OPENROUTER_CHAT_URL, DEFAULT_LLM_TEMPERATURE

        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": DEFAULT_LLM_TEMPERATURE,
            "max_tokens": max_tokens,
            "usage": {"include": True},
            **extra_payload,
        }
        async with httpx.AsyncClient(timeout=provider._timeout) as client:
            resp = await client.post(
                OPENROUTER_CHAT_URL,
                headers=provider._headers(),
                json=payload,
            )
            status = resp.status_code
            try:
                data = resp.json()
            except Exception:
                data = {"_raw_text": resp.text[:2000]}
        llm_text = None
        parsed_via = None
        if status < 400 and isinstance(data, dict) and data.get("choices"):
            msg = data["choices"][0].get("message") or {}
            raw_content = msg.get("content", "")
            llm_text = _content_to_text(raw_content)
            parsed_via = "current_parser"
        result = {
            "label": label,
            "model": model,
            "http_status": status,
            "system_chars": len(system),
            "user_chars": len(user),
            "max_tokens": max_tokens,
            "extra_payload": extra_payload,
            "request_messages_roles": ["system", "user"],
            "parsed_text_via_current_parser": (
                None
                if llm_text is None
                else {
                    "type": type(llm_text).__name__,
                    "repr": repr(llm_text)[:120],
                    "len": len(llm_text),
                    "equals_None_string": llm_text == "None",
                }
            ),
            "parsed_via": parsed_via,
        }
        if isinstance(data, dict) and data.get("choices"):
            choice = data["choices"][0]
            msg = choice.get("message") or {}
            result["finish_reason"] = choice.get("finish_reason")
            result["native_finish_reason"] = choice.get("native_finish_reason")
            result["message"] = _shape_message(msg)
            result["usage"] = _shape_usage(data.get("usage"))
            # Full message keys with truncated string values for forensics
            full_msg = {}
            for k, v in msg.items():
                if isinstance(v, str) and len(v) > 500:
                    full_msg[k] = v[:500] + f"…[truncated len={len(v)}]"
                else:
                    full_msg[k] = v
            result["message_full_truncated"] = full_msg
        else:
            result["error_body"] = data
        # Never include Authorization
        result["request_payload_redacted"] = {
            "model": payload["model"],
            "temperature": payload["temperature"],
            "max_tokens": payload["max_tokens"],
            "usage": payload.get("usage"),
            "extra": extra_payload,
            "system_preview": system[:200],
            "user": user[:500],
            "system_chars": len(system),
            "user_chars": len(user),
        }
        return result

    resp = await provider.complete(
        system=system, user=user, model=model, max_tokens=max_tokens
    )
    data = resp.raw or {}
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return {
        "label": label,
        "model": model,
        "system_chars": len(system),
        "user_chars": len(user),
        "max_tokens": max_tokens,
        "extra_payload": None,
        "finish_reason": choice.get("finish_reason"),
        "native_finish_reason": choice.get("native_finish_reason"),
        "usage": _shape_usage(data.get("usage")),
        "message": _shape_message(msg),
        "message_full_truncated": {
            k: (v[:500] + f"…[truncated len={len(v)}]" if isinstance(v, str) and len(v) > 500 else v)
            for k, v in msg.items()
        },
        "llm_response": {
            "text_repr": repr(resp.text)[:200],
            "text_len": len(resp.text),
            "equals_None_string": resp.text == "None",
            "tokens_input": resp.tokens_input,
            "tokens_output": resp.tokens_output,
            "confidence": resp.confidence,
        },
        "request_payload_redacted": {
            "model": model,
            "temperature": 0.7,
            "max_tokens": max_tokens,
            "system_preview": system[:200],
            "user": user[:500],
            "system_chars": len(system),
            "user_chars": len(user),
        },
    }


async def main() -> None:
    provider = OpenRouterProvider()
    results: list[dict[str, Any]] = []

    # 1) Same short prompt across models (production-like: no reasoning param)
    for name, slug in MODELS.items():
        try:
            results.append(
                await probe_one(
                    provider,
                    label=f"short_{name}",
                    model=slug,
                    system=SHORT_SYSTEM,
                    user=SHORT_USER,
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append({"label": f"short_{name}", "model": slug, "error": str(exc)})

    # 2) Large attachment-like system prompt for Gemini only (matches failing path shape)
    try:
        results.append(
            await probe_one(
                provider,
                label="large_gemini_default",
                model=MODELS["gemini"],
                system=LARGE_SYSTEM,
                user=LARGE_USER,
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append({"label": "large_gemini_default", "error": str(exc)})

    # 3) Gemini with explicit reasoning enabled (to see where answer lands)
    try:
        results.append(
            await probe_one(
                provider,
                label="short_gemini_reasoning_enabled",
                model=MODELS["gemini"],
                system=SHORT_SYSTEM,
                user=SHORT_USER,
                extra_payload={"reasoning": {"enabled": True}},
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append({"label": "short_gemini_reasoning_enabled", "error": str(exc)})

    # 4) Gemini with reasoning excluded (thinking internal only)
    try:
        results.append(
            await probe_one(
                provider,
                label="short_gemini_reasoning_exclude",
                model=MODELS["gemini"],
                system=SHORT_SYSTEM,
                user=SHORT_USER,
                extra_payload={"reasoning": {"enabled": True, "exclude": True}},
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append({"label": "short_gemini_reasoning_exclude", "error": str(exc)})

    # 5) Gemini max_tokens small — check if thinking eats budget
    try:
        results.append(
            await probe_one(
                provider,
                label="short_gemini_max_tokens_256",
                model=MODELS["gemini"],
                system=SHORT_SYSTEM,
                user=SHORT_USER,
                max_tokens=256,
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append({"label": "short_gemini_max_tokens_256", "error": str(exc)})

    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
