"""LLM provider abstraction — OpenRouter (multi-model gateway)."""

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

CONFIDENCE_PATTERN = re.compile(r"CONFIDENCE:\s*(\d{1,3})", re.IGNORECASE)
OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_RESPONSES_URL = "https://openrouter.ai/api/v1/responses"


@dataclass
class LLMResponse:
    text: str
    tokens_input: int
    tokens_output: int
    cost_usd: float | None = None
    confidence: int | None = None
    raw: dict[str, Any] | None = None


class OpenRouterProviderError(RuntimeError):
    """Safe, structured OpenRouter request failure."""

    def __init__(
        self,
        *,
        http_status: int | None,
        provider_error_code: str | None,
        provider_error_type: str | None,
        provider_message: str,
        retryable: bool,
    ) -> None:
        super().__init__("OpenRouter request failed.")
        self.http_status = http_status
        self.provider_error_code = provider_error_code
        self.provider_error_type = provider_error_type
        self.provider_message = _safe_provider_message(provider_message)
        self.retryable = retryable


def _is_retryable_openrouter_error(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException)) or (
        isinstance(exc, OpenRouterProviderError) and exc.retryable
    )


def _safe_provider_message(value: Any) -> str:
    message = str(value).replace("\n", " ").replace("\r", " ").strip()
    message = re.sub(r"(?i)(bearer\s+)[^\s,;]+", r"\1[redacted]", message)
    message = re.sub(r"(?i)(authorization:\s*)[^\s,;]+", r"\1[redacted]", message)
    message = re.sub(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+", r"\1[redacted]", message)
    return message[:240] or "OpenRouter returned an error without a message."


def _is_generic_provider_message(value: Any) -> bool:
    message = str(value or "").strip().lower()
    return message in {"", "provider returned error", "openrouter returned an error without a message."}


def _nested_provider_error_fields(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    fields: dict[str, Any] = {}
    for key in ("message", "error_message", "provider_message"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            fields["message"] = value
            break
    raw = metadata.get("raw")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            nested_error = parsed.get("error", parsed)
            if isinstance(nested_error, dict):
                if "message" not in fields and isinstance(nested_error.get("message"), str):
                    fields["message"] = nested_error["message"]
                if nested_error.get("code") is not None:
                    fields["code"] = nested_error.get("code")
                if nested_error.get("type") is not None:
                    fields["type"] = nested_error.get("type")
    if metadata.get("code") is not None and "code" not in fields:
        fields["code"] = metadata.get("code")
    if metadata.get("type") is not None and "type" not in fields:
        fields["type"] = metadata.get("type")
    return fields


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
        tools: list[dict[str, Any]] | None = None,
        timeout_seconds: float | None = None,
    ) -> LLMResponse:
        pass

    @abstractmethod
    async def complete_responses(
        self,
        *,
        input_text: str,
        model: str,
        tools: list[dict[str, Any]],
        timeout_seconds: float,
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

    @staticmethod
    def _raise_for_error(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        error_code = None
        error_type = None
        detail: Any = resp.text
        try:
            error = resp.json().get("error", {})
            if isinstance(error, dict):
                detail = error.get("message", detail)
                error_code = error.get("code")
                error_type = error.get("type")
                nested = _nested_provider_error_fields(error.get("metadata"))
                if nested:
                    generic = _is_generic_provider_message(detail)
                    if generic and nested.get("message"):
                        detail = nested["message"]
                    top_code_is_http_status = (
                        error_code is not None and str(error_code) == str(resp.status_code)
                    )
                    if nested.get("code") is not None and (
                        error_code is None or top_code_is_http_status or generic
                    ):
                        error_code = nested["code"]
                    if nested.get("type") is not None and (error_type is None or generic):
                        error_type = nested["type"]
        except (TypeError, ValueError):
            error_code = None
            error_type = None
        raise OpenRouterProviderError(
            http_status=resp.status_code,
            provider_error_code=str(error_code) if error_code is not None else None,
            provider_error_type=str(error_type) if error_type is not None else None,
            provider_message=_safe_provider_message(detail),
            retryable=resp.status_code in {408, 409, 429} or resp.status_code >= 500,
        )

    @retry(
        retry=retry_if_exception(_is_retryable_openrouter_error),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=8),
    )
    async def complete(
        self,
        *,
        system: str,
        user: str,
        model: str,
        max_tokens: int = 4096,
        response_format: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        timeout_seconds: float | None = None,
    ) -> LLMResponse:
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")

        timeout = self._timeout if timeout_seconds is None else timeout_seconds
        async with httpx.AsyncClient(timeout=timeout) as client:
            payload: dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.7,
                "max_tokens": max_tokens,
                "usage": {"include": True},
            }
            if response_format is not None:
                payload["response_format"] = response_format
            if tools is not None:
                payload["tools"] = tools
            resp = await client.post(
                OPENROUTER_CHAT_URL,
                headers=self._headers(),
                json=payload,
            )
            self._raise_for_error(resp)
            data = resp.json()

        message = data["choices"][0]["message"]
        content = _content_to_text(message.get("content", ""))
        annotations = _chat_message_annotations(message)
        usage = data.get("usage", {})
        reported_cost = usage.get("cost")
        cost_usd = float(reported_cost) if reported_cost is not None else None
        text, confidence = self.parse_confidence(content)
        raw = dict(data)
        if annotations:
            raw["annotations"] = annotations
        return LLMResponse(
            text=text,
            tokens_input=usage.get("prompt_tokens", len(system) // 4),
            tokens_output=usage.get("completion_tokens", len(text) // 4),
            cost_usd=cost_usd,
            confidence=confidence,
            raw=raw,
        )

    @retry(
        retry=retry_if_exception(_is_retryable_openrouter_error),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=8),
    )
    async def complete_responses(
        self,
        *,
        input_text: str,
        model: str,
        tools: list[dict[str, Any]],
        timeout_seconds: float,
    ) -> LLMResponse:
        """Run a stateless OpenRouter Responses request with server tools."""
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")

        payload = {"model": model, "input": input_text, "tools": tools}
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.post(
                OPENROUTER_RESPONSES_URL,
                headers=self._headers(),
                json=payload,
            )
            self._raise_for_error(resp)
            data = resp.json()

        text, annotations = _responses_output_text_and_annotations(data)
        usage = data.get("usage", {})
        return LLMResponse(
            text=text,
            tokens_input=usage.get("input_tokens", len(input_text) // 4),
            tokens_output=usage.get("output_tokens", len(text) // 4),
            cost_usd=float(usage["cost"]) if usage.get("cost") is not None else None,
            raw={"id": data.get("id"), "annotations": annotations},
        )


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


def _chat_message_annotations(message: dict[str, Any]) -> list[dict[str, Any]]:
    annotations: list[dict[str, Any]] = []

    def _append(item: Any) -> None:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            annotations.append(
                {
                    "url": item["url"],
                    "title": item.get("title"),
                    "source_type": item.get("type", "url_citation"),
                }
            )

    for annotation in message.get("annotations") or []:
        _append(annotation)
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                for annotation in part.get("annotations") or []:
                    _append(annotation)
    return annotations


def _responses_output_text_and_annotations(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    text_parts: list[str] = []
    annotations: list[dict[str, Any]] = []
    for output in data.get("output", []):
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if not isinstance(content, dict) or content.get("type") != "output_text":
                continue
            text = content.get("text")
            if isinstance(text, str):
                text_parts.append(text)
            for annotation in content.get("annotations", []):
                if isinstance(annotation, dict) and isinstance(annotation.get("url"), str):
                    annotations.append(
                        {
                            "url": annotation["url"],
                            "title": annotation.get("title"),
                            "source_type": annotation.get("type", "url_citation"),
                        }
                    )
    return "\n".join(text_parts), annotations


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
