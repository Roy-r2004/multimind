"""LLM provider abstraction — OpenRouter (multi-model gateway)."""

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import RetryError, retry, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

CONFIDENCE_PATTERN = re.compile(r"CONFIDENCE:\s*(\d{1,3})", re.IGNORECASE)
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


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
    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
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

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(multiplier=1, min=1, max=8))
    async def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                OPENROUTER_CHAT_URL,
                headers=self._headers(),
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.7,
                    "max_tokens": max_tokens,
                    "usage": {"include": True},
                },
            )
            if resp.status_code >= 400:
                detail = resp.text
                try:
                    detail = resp.json().get("error", {}).get("message", detail)
                except Exception:
                    pass
                raise RuntimeError(f"OpenRouter error ({resp.status_code}): {detail}")
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        reported_cost = usage.get("cost")
        cost_usd = float(reported_cost) if reported_cost is not None else None
        text, confidence = self.parse_confidence(content)
        return LLMResponse(
            text=text,
            tokens_input=usage.get("prompt_tokens", len(system) // 4),
            tokens_output=usage.get("completion_tokens", len(text) // 4),
            cost_usd=cost_usd,
            confidence=confidence,
            raw=data,
        )


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
