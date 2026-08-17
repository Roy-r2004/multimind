"""LLM provider abstraction — OpenRouter (multi-model gateway)."""

import asyncio
import json
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

CONFIDENCE_PATTERN = re.compile(r"CONFIDENCE:\s*(\d{1,3})", re.IGNORECASE)
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_LLM_TEMPERATURE = 0.7

# Claude Fable 5: prefer OpenRouter Exacto (quality/reliability) over price-weighted
# load balancing, while keeping same-model provider failover enabled.
CLAUDE_FABLE_5_SLUG = "anthropic/claude-fable-5"
CLAUDE_FABLE_5_ROUTED_SLUG = f"{CLAUDE_FABLE_5_SLUG}:exacto"
CLAUDE_FABLE_5_PROVIDER_PREFERENCES: dict[str, Any] = {
    "allow_fallbacks": True,
}

# Grok 4: try Grok 4 providers first, then Grok 4.3 only if Grok 4 cannot complete.
GROK_4_SLUG = "x-ai/grok-4"
GROK_4_EMERGENCY_FALLBACK_SLUG = "x-ai/grok-4.3"
GROK_4_MODEL_FALLBACKS = [GROK_4_SLUG, GROK_4_EMERGENCY_FALLBACK_SLUG]
GROK_4_PROVIDER_PREFERENCES: dict[str, Any] = {
    "allow_fallbacks": True,
}


@dataclass
class LLMResponse:
    text: str
    tokens_input: int
    tokens_output: int
    cost_usd: float | None = None
    confidence: int | None = None
    raw: dict[str, Any] | None = None


class LLMProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        pass

    @staticmethod
    def parse_confidence(text: str) -> tuple[str, int | None]:
        match = CONFIDENCE_PATTERN.search(text)
        if not match:
            return text.strip(), None
        confidence = min(100, max(0, int(match.group(1))))
        cleaned = CONFIDENCE_PATTERN.sub("", text).strip()
        return cleaned, confidence

    @staticmethod
    def parse_json_response(text: str) -> dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        return json.loads(text)

    @staticmethod
    def parse_json_object_lenient(text: str) -> dict[str, Any] | None:
        """Best-effort JSON object recovery from a model response.

        Tolerates prose around the object and a response cut off by the token
        cap. Returns ``None`` when nothing usable can be recovered so callers
        can fall back to the raw text instead of discarding the response.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return None
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)
            cleaned = cleaned.strip()

        try:
            parsed = json.loads(cleaned)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass

        start = cleaned.find("{")
        if start == -1:
            return None
        candidate = cleaned[start:]

        # Single scan: find the first balanced object, and record how much is
        # still open at the end so a truncated response can be repaired.
        depth = 0
        in_string = False
        escaped = False
        balanced_end: int | None = None
        for index, char in enumerate(candidate):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    balanced_end = index + 1
                    break

        if balanced_end is not None:
            try:
                parsed = json.loads(candidate[:balanced_end])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        # Truncated mid-object: close the dangling string and braces.
        repaired = candidate
        if in_string:
            repaired += '"'
        if depth > 0:
            repaired += "}" * depth
        try:
            parsed = json.loads(repaired)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


class OpenRouterProvider(LLMProvider):
    """Unified gateway — one key routes to OpenAI, Anthropic, Google, DeepSeek, etc."""

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.openrouter_api_key
        self._timeout = settings.llm_timeout_seconds
        self._site_url = settings.openrouter_site_url or settings.public_app_url
        self._app_name = settings.openrouter_app_name

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if self._site_url:
            headers["HTTP-Referer"] = self._site_url
        if self._app_name:
            headers["X-OpenRouter-Title"] = self._app_name
        return headers

    async def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return await self._complete_once(
                    system=system,
                    user=user,
                    model=model,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    temperature=temperature,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt == 0:
                    await asyncio.sleep(1.0)
                    continue
                raise
        assert last_error is not None
        raise last_error

    async def _complete_once(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int,
        response_format: dict[str, Any] | None,
        temperature: float | None = None,
    ) -> LLMResponse:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            payload = build_openrouter_chat_payload(
                model=model,
                system=system,
                user=user,
                max_tokens=max_tokens,
                response_format=response_format,
                temperature=temperature,
            )
            resp = await client.post(
                OPENROUTER_CHAT_URL,
                headers=self._headers(),
                json=payload,
            )
            if resp.status_code >= 400:
                detail = resp.text
                try:
                    detail = resp.json().get("error", {}).get("message", detail)
                except Exception:
                    pass
                raise RuntimeError(f"OpenRouter error ({resp.status_code}): {detail}")
            data = resp.json()

        content = _content_to_text(data["choices"][0]["message"].get("content", ""))
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        cost_usd = _parse_reported_cost(usage.get("cost"))
        _record_maps_quota_cost(cost_usd)
        text, confidence = self.parse_confidence(content)
        return LLMResponse(
            text=text,
            tokens_input=usage.get("prompt_tokens", len(system) // 4),
            tokens_output=usage.get("completion_tokens", len(text) // 4),
            cost_usd=cost_usd,
            confidence=confidence,
            raw=data,
        )


def _openrouter_base_slug(model: str) -> str:
    return (model or "").split(":", 1)[0].strip().lower()


def _is_claude_fable_5(model: str) -> bool:
    """True for anthropic/claude-fable-5, with or without a routing suffix."""
    return _openrouter_base_slug(model) == CLAUDE_FABLE_5_SLUG


def _is_grok_4(model: str) -> bool:
    """True for x-ai/grok-4, with or without a routing suffix — not grok-4.3."""
    return _openrouter_base_slug(model) == GROK_4_SLUG


def resolve_openrouter_model_slug(model: str) -> str:
    """Apply per-model OpenRouter routing variants without changing catalog ids."""
    if _is_claude_fable_5(model):
        # Keep an already-selected variant (e.g. :nitro) if the caller set one;
        # otherwise prefer Exacto quality routing over default price weighting.
        if ":" in model:
            return model
        return CLAUDE_FABLE_5_ROUTED_SLUG
    return model


def openrouter_provider_preferences(model: str) -> dict[str, Any] | None:
    """Optional OpenRouter ``provider`` object for a request model slug."""
    if _is_claude_fable_5(model):
        return dict(CLAUDE_FABLE_5_PROVIDER_PREFERENCES)
    if _is_grok_4(model):
        return dict(GROK_4_PROVIDER_PREFERENCES)
    return None


def build_openrouter_chat_payload(
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    response_format: dict[str, Any] | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Build the OpenRouter chat/completions JSON body (routing prefs included)."""
    payload: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": DEFAULT_LLM_TEMPERATURE if temperature is None else temperature,
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    if _is_grok_4(model):
        # Ordered models[] is the current OpenRouter fallback chain; omit `model`.
        payload["models"] = list(GROK_4_MODEL_FALLBACKS)
    else:
        payload["model"] = resolve_openrouter_model_slug(model)
    provider = openrouter_provider_preferences(model)
    if provider is not None:
        payload["provider"] = provider
    if response_format is not None:
        payload["response_format"] = response_format
    return payload


def _record_maps_quota_cost(cost_usd: float | None) -> None:
    """Feed real per-call OpenRouter cost to a Maps census run's quota tracker,
    if one is currently active for this task (see maps_quota_tracker.py).
    No-ops for chat/brain/lessons calls, which never set a tracker."""
    try:
        from app.services.scraping.maps_quota_tracker import record_llm_cost

        record_llm_cost(cost_usd)
    except Exception:  # noqa: BLE001 - cost bookkeeping must never break an LLM call
        logger.warning("maps_quota_cost_record_failed", exc_info=True)


def _parse_reported_cost(value: Any) -> float | None:
    """Return OpenRouter usage.cost as float, or None when missing/invalid."""
    if value is None:
        return None
    try:
        cost = float(value)
    except (TypeError, ValueError):
        logger.warning("openrouter_cost_parse_failed")
        return None
    if math.isnan(cost) or math.isinf(cost) or cost < 0:
        logger.warning("openrouter_cost_parse_failed")
        return None
    return cost


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content)


class ProviderRegistry:
    def __init__(self) -> None:
        self._openrouter = OpenRouterProvider()

    def get_provider(self, _provider_name: str) -> LLMProvider:
        return self._openrouter

    def validate_configured(self) -> None:
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise AppError(
                "OPENROUTER_API_KEY is required for LLM calls",
                code="LLM_NOT_CONFIGURED",
            )


_registry: ProviderRegistry | None = None


def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry
