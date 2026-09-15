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
from app.llm.catalog import is_shadow_model, model_id_to_slug

logger = get_logger(__name__)

CONFIDENCE_PATTERN = re.compile(r"CONFIDENCE:\s*(\d{1,3})", re.IGNORECASE)
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_LLM_TEMPERATURE = 0.7
# Strongest → weakest. Speed-profile fallback only walks downward.
_SHADOW_REASONING_STRENGTH = (
    "max",
    "xhigh",
    "high",
    "medium",
    "low",
    "minimal",
)
SHADOW_PREFERRED_REASONING_EFFORT = {
    "nvidia/nemotron-3-ultra-550b-a55b": "medium",
    "qwen/qwen3.8-max-0902": "low",
    "deepseek/deepseek-v4.1-flash": "low",
}
SHADOW_WEB_PLUGIN = {
    "id": "web",
    "engine": "parallel",
    "mode": "turbo",
    "max_results": 3,
}


class OpenRouterError(RuntimeError):
    """Gateway failure retaining status for narrowly scoped Council failover."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"OpenRouter error ({status_code}): {detail}")
        self.status_code = status_code


def council_fallback_reason(exc: Exception) -> str | None:
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.NetworkError):
        return "provider_connection"
    if isinstance(exc, OpenRouterError):
        if exc.status_code == 429:
            return "rate_limit"
        if exc.status_code in (408, 504):
            return "timeout"
        if exc.status_code == 404:
            return "model_unavailable"
        if 500 <= exc.status_code < 600:
            return "provider_unavailable"
    return None


# Claude Fable 5: prefer OpenRouter Exacto (quality/reliability) over price-weighted
# load balancing, while keeping same-model provider failover enabled.
CLAUDE_FABLE_5_SLUG = "anthropic/claude-fable-5"
CLAUDE_FABLE_5_ROUTED_SLUG = f"{CLAUDE_FABLE_5_SLUG}:exacto"
CLAUDE_FABLE_5_PROVIDER_PREFERENCES: dict[str, Any] = {
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
    finish_reason: str | None = None


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
        preserve_whitespace: bool = False,
        timeout: float | None = None,
        allow_provider_fallbacks: bool = False,
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
        preserve_whitespace: bool = False,
        timeout: float | None = None,
        allow_provider_fallbacks: bool = False,
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
                    preserve_whitespace=preserve_whitespace,
                    timeout=timeout,
                    allow_provider_fallbacks=allow_provider_fallbacks,
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
        preserve_whitespace: bool = False,
        timeout: float | None = None,
        allow_provider_fallbacks: bool = False,
    ) -> LLMResponse:
        async with httpx.AsyncClient(timeout=timeout if timeout is not None else self._timeout) as client:
            payload = build_openrouter_chat_payload(
                model=model,
                system=system,
                user=user,
                max_tokens=max_tokens,
                response_format=response_format,
                temperature=temperature,
                allow_provider_fallbacks=allow_provider_fallbacks,
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
                raise OpenRouterError(resp.status_code, detail)
            data = resp.json()

        content = _content_to_text(data["choices"][0]["message"].get("content", ""))
        finish_reason = data["choices"][0].get("finish_reason") or data["choices"][0].get(
            "native_finish_reason"
        )
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        cost_usd = _parse_reported_cost(usage.get("cost"))
        _record_maps_quota_cost(cost_usd)
        if preserve_whitespace:
            text, confidence = content, None
        else:
            text, confidence = self.parse_confidence(content)
        return LLMResponse(
            text=text,
            tokens_input=usage.get("prompt_tokens", len(system) // 4),
            tokens_output=usage.get("completion_tokens", len(text) // 4),
            cost_usd=cost_usd,
            confidence=confidence,
            raw=data,
            finish_reason=str(finish_reason) if finish_reason is not None else None,
        )


def _is_claude_fable_5(model: str) -> bool:
    """True for anthropic/claude-fable-5, with or without a routing suffix."""
    base = (model or "").split(":", 1)[0].strip().lower()
    return base == CLAUDE_FABLE_5_SLUG


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
    """Existing per-model routing preferences; custom Council chains opt in separately."""
    if _is_claude_fable_5(model):
        return dict(CLAUDE_FABLE_5_PROVIDER_PREFERENCES)
    return None


def _shadow_supported_reasoning_efforts(model: str) -> list[str] | None:
    """Return OpenRouter `reasoning.supported_efforts` when cached metadata has it."""
    try:
        from app.llm.pricing import get_pricing_service

        meta = get_pricing_service().get_slug_metadata(model_id_to_slug(model)) or {}
    except Exception:  # noqa: BLE001 — missing catalog must not fail Council calls
        return None
    reasoning_meta = meta.get("reasoning")
    if not isinstance(reasoning_meta, dict):
        return None
    supported = reasoning_meta.get("supported_efforts")
    if not isinstance(supported, list) or not supported:
        return None
    efforts = [str(item).strip().lower() for item in supported if str(item).strip()]
    return efforts or None


def preferred_shadow_reasoning_effort(model: str) -> str:
    """Explicit faster Shadow profile; never inherit a model's default xhigh/high."""
    slug = model_id_to_slug(model)
    return SHADOW_PREFERRED_REASONING_EFFORT.get(slug, "medium")


def normalize_shadow_reasoning_effort(model: str) -> str:
    """Use the shadow speed preference, then the closest weaker supported effort."""
    preferred = preferred_shadow_reasoning_effort(model)
    supported = _shadow_supported_reasoning_efforts(model)
    if not supported:
        return preferred
    supported_set = set(supported)
    if preferred in supported_set:
        return preferred
    try:
        rank = _SHADOW_REASONING_STRENGTH.index(preferred)
    except ValueError:
        rank = _SHADOW_REASONING_STRENGTH.index("medium")
    for effort in _SHADOW_REASONING_STRENGTH[rank + 1 :]:
        if effort in supported_set:
            return effort
    return preferred


def shadow_openrouter_reasoning(model: str) -> dict[str, Any]:
    return {
        "enabled": True,
        "effort": normalize_shadow_reasoning_effort(model),
        "exclude": True,
    }


def apply_shadow_openrouter_request_options(payload: dict[str, Any], model: str) -> None:
    """Enable hidden reasoning + web search for the three Shadow Council models only."""
    if not is_shadow_model(model):
        return
    payload["reasoning"] = shadow_openrouter_reasoning(model)
    payload["plugins"] = [{**SHADOW_WEB_PLUGIN}]


def build_openrouter_chat_payload(
    *,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    response_format: dict[str, Any] | None = None,
    temperature: float | None = None,
    allow_provider_fallbacks: bool = False,
) -> dict[str, Any]:
    """Build the OpenRouter chat/completions JSON body (routing prefs included)."""
    routed_model = resolve_openrouter_model_slug(model)
    payload: dict[str, Any] = {
        "model": routed_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": DEFAULT_LLM_TEMPERATURE if temperature is None else temperature,
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }
    provider = openrouter_provider_preferences(model)
    if allow_provider_fallbacks:
        provider = {**(provider or {}), "allow_fallbacks": True}
    if provider is not None:
        payload["provider"] = provider
    if response_format is not None:
        payload["response_format"] = response_format
    apply_shadow_openrouter_request_options(payload, model)
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
    """Flatten message content. Reasoning/annotation metadata is ignored."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                item_type = str(item.get("type") or "").lower()
                if item_type in {"reasoning", "thinking", "reasoning_text"}:
                    continue
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
