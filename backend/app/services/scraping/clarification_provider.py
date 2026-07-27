"""OpenRouter clarification provider for typed Step 2 campaign clarifications."""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import ValidationError as PydanticValidationError

from app.core.config import Settings, get_settings
from app.llm.providers import (
    LLMProvider,
    OpenRouterProvider,
    OpenRouterProviderError,
    _safe_provider_message,
)
from app.schemas.scraping_clarification import (
    ClarificationAllowedValue,
    ClarificationDecision,
    ClarificationProviderRequest,
    ClarificationProviderResponse,
)

CLARIFICATION_SCHEMA_NAME = "scraper_clarification_response"
CLARIFICATION_PROVIDER_PREFERENCES: dict[str, Any] = {"require_parameters": True}
CLARIFICATION_SYSTEM_PROMPT = (
    "You are the GPT-5.6 Luna clarification role for MultiMind scraper campaigns. "
    "Return one JSON object only. Select only from allowed_values. "
    "Never invent URLs, sources, regions, languages, countries, facilities, or contacts. "
    "Never expand campaign scope. Never change qualification or containment rules."
)


def _strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Pydantic JSON Schema for OpenRouter/OpenAI strict structured outputs."""
    copied = json.loads(json.dumps(schema))
    _require_object_properties(copied)
    return copied


def _require_object_properties(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["additionalProperties"] = False
                node["required"] = sorted(properties)
        for value in node.values():
            _require_object_properties(value)
    elif isinstance(node, list):
        for value in node:
            _require_object_properties(value)


def clarification_response_format() -> dict[str, Any]:
    """Strict JSON Schema response_format for ClarificationProviderResponse."""
    schema = _strict_json_schema(ClarificationProviderResponse.model_json_schema())
    return {
        "type": "json_schema",
        "json_schema": {
            "name": CLARIFICATION_SCHEMA_NAME,
            "strict": True,
            "schema": schema,
        },
    }


class ClarificationProviderError(RuntimeError):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        stage: str | None = None,
        model: str | None = None,
        retryable: bool | None = None,
        http_status: int | None = None,
        provider_error_code: str | None = None,
        provider_error_type: str | None = None,
        provider_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.stage = stage
        self.model = model
        self.retryable = retryable
        self.http_status = http_status
        self.provider_error_code = provider_error_code
        self.provider_error_type = provider_error_type
        self.provider_message = (
            _safe_provider_message(provider_message) if provider_message else None
        )

    def safe_diagnostics(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "stage": self.stage,
            "model": self.model,
            "retryable": self.retryable,
            "http_status": self.http_status,
            "provider_error_code": self.provider_error_code,
            "provider_error_type": self.provider_error_type,
            "provider_message": self.provider_message,
        }


class ClarificationProvider(Protocol):
    async def clarify(
        self, request: ClarificationProviderRequest
    ) -> ClarificationProviderResponse: ...


def clarification_provider_configured(settings: Settings | None = None) -> bool:
    """True when the exact OpenRouter clarification model slug is configured."""
    resolved = settings or get_settings()
    return bool(resolved.openrouter_scraper_clarification_model.strip())


def build_clarification_provider(
    settings: Settings | None = None,
) -> OpenRouterClarificationProvider:
    """Return the real OpenRouter clarification provider, or fail closed.

    Never returns a fake provider and never fabricates clarification decisions.
    """
    resolved = settings or get_settings()
    if not resolved.openrouter_scraper_clarification_model.strip():
        raise ClarificationProviderError(
            "configuration_missing",
            "OPENROUTER_SCRAPER_CLARIFICATION_MODEL is not configured.",
            stage="provider_factory",
            retryable=False,
        )
    if not resolved.openrouter_api_key:
        raise ClarificationProviderError(
            "configuration_missing",
            "OpenRouter is not configured.",
            stage="provider_factory",
            retryable=False,
        )
    return OpenRouterClarificationProvider(resolved)


class OpenRouterClarificationProvider:
    provider_name = "openrouter"
    role_display_name = "GPT-5.6 Luna"

    def __init__(self, settings: Settings | None = None, client: LLMProvider | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client or OpenRouterProvider()

    def validate_configuration(self) -> None:
        if not self._settings.openrouter_api_key:
            raise ClarificationProviderError(
                "configuration_missing",
                "OpenRouter is not configured.",
                stage="clarify",
                retryable=False,
            )
        if not self._settings.openrouter_scraper_clarification_model.strip():
            raise ClarificationProviderError(
                "configuration_missing",
                "OPENROUTER_SCRAPER_CLARIFICATION_MODEL is not configured.",
                stage="clarify",
                retryable=False,
            )

    async def clarify(
        self, request: ClarificationProviderRequest
    ) -> ClarificationProviderResponse:
        self.validate_configuration()
        model = self._settings.openrouter_scraper_clarification_model.strip()
        user_payload = request.model_dump(mode="json")
        response_format = clarification_response_format()
        try:
            # Omit tools entirely so the HTTP payload has no tools key.
            first = await self._client.complete(
                system=CLARIFICATION_SYSTEM_PROMPT,
                user=json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
                model=model,
                max_tokens=self._settings.openrouter_scraper_clarification_max_output_tokens,
                temperature=None,
                response_format=response_format,
                provider=CLARIFICATION_PROVIDER_PREFERENCES,
                timeout_seconds=self._settings.openrouter_scraper_clarification_timeout_seconds,
            )
        except Exception as exc:
            raise self._map_error(exc, stage="clarify", model=model) from exc

        try:
            return self._validate_response(first.text, request)
        except (json.JSONDecodeError, PydanticValidationError, ValueError) as first_exc:
            if self._is_scope_violation(first_exc, first.text):
                raise ClarificationProviderError(
                    "scope_violation",
                    "Clarification response attempted a forbidden scope change.",
                    stage="clarify",
                    model=model,
                    retryable=False,
                ) from first_exc
            try:
                repair = await self._client.complete(
                    system=CLARIFICATION_SYSTEM_PROMPT,
                    user=(
                        "The previous JSON was invalid. Return corrected JSON only for this "
                        "clarification request.\n\n"
                        f"Request:\n{json.dumps(user_payload, ensure_ascii=False)}\n\n"
                        f"Invalid response:\n{first.text}"
                    ),
                    model=model,
                    max_tokens=self._settings.openrouter_scraper_clarification_max_output_tokens,
                    temperature=None,
                    response_format=response_format,
                    provider=CLARIFICATION_PROVIDER_PREFERENCES,
                    timeout_seconds=self._settings.openrouter_scraper_clarification_timeout_seconds,
                )
            except Exception as exc:
                raise self._map_error(exc, stage="clarify_repair", model=model) from exc
            try:
                return self._validate_response(repair.text, request)
            except (json.JSONDecodeError, PydanticValidationError, ValueError) as repair_exc:
                if self._is_scope_violation(repair_exc, repair.text):
                    raise ClarificationProviderError(
                        "scope_violation",
                        "Clarification repair attempted a forbidden scope change.",
                        stage="clarify_repair",
                        model=model,
                        retryable=False,
                    ) from repair_exc
                raise ClarificationProviderError(
                    "invalid_response",
                    "Clarification provider returned invalid structured output.",
                    stage="clarify_repair",
                    model=model,
                    retryable=False,
                ) from repair_exc

    def _validate_response(
        self, text: str, request: ClarificationProviderRequest
    ) -> ClarificationProviderResponse:
        parsed = self._parse_json_object(text)
        response = ClarificationProviderResponse.model_validate(parsed)
        if response.clarification_id != request.clarification_id:
            raise ValueError("Unknown clarification ID in provider response.")
        if response.decision == ClarificationDecision.RESOLVED:
            assert response.selected_value is not None
            allowed = {
                (item.value, item.label) for item in request.allowed_values
            }
            selected = (response.selected_value.value, response.selected_value.label)
            exact_values = {item.value for item in request.allowed_values}
            if (
                selected not in allowed
                and response.selected_value.value not in exact_values
            ):
                raise ValueError("Selected value is not in allowed_values.")
            # Normalize to the exact allowed entry when value matches.
            if selected not in allowed:
                for item in request.allowed_values:
                    if item.value == response.selected_value.value:
                        response = response.model_copy(
                            update={"selected_value": ClarificationAllowedValue(
                                value=item.value, label=item.label
                            )}
                        )
                        break
            self._reject_invented_urls(request, response.selected_value)
        return response

    def _reject_invented_urls(
        self, request: ClarificationProviderRequest, selected: ClarificationAllowedValue
    ) -> None:
        if "://" not in selected.value and "://" not in selected.label:
            return
        allowed_urls = {
            item.value for item in request.allowed_values if "://" in item.value
        } | {item.label for item in request.allowed_values if "://" in item.label}
        if selected.value not in allowed_urls and selected.label not in allowed_urls:
            raise ValueError("Response URL is not present in allowed values.")

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        payload = text.strip()
        if payload.startswith("```"):
            payload = payload.strip("`")
            if payload.startswith("json"):
                payload = payload[4:]
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("Clarification response must be a JSON object.")
        return data

    def _is_scope_violation(self, exc: Exception, text: str) -> bool:
        combined = f"{exc} {text}".casefold()
        markers = (
            "country",
            "scope",
            "new region",
            "new language",
            "qualification",
            "http://",
            "https://",
        )
        return any(marker in combined for marker in markers) and (
            "allowed" in combined or "forbid" in combined or "invent" in combined
        )

    def _map_error(
        self, exc: Exception, *, stage: str, model: str
    ) -> ClarificationProviderError:
        if isinstance(exc, ClarificationProviderError):
            return exc
        if isinstance(exc, OpenRouterProviderError):
            category = (
                "authentication"
                if exc.http_status == 401
                else "rate_limited"
                if exc.http_status == 429
                else "invalid_model"
                if exc.http_status == 400
                else "provider"
            )
            return ClarificationProviderError(
                category,
                "Clarification provider request failed.",
                stage=stage,
                model=model,
                retryable=exc.retryable,
                http_status=exc.http_status,
                provider_error_code=exc.provider_error_code,
                provider_error_type=exc.provider_error_type,
                provider_message=exc.provider_message,
            )
        text = str(exc).casefold()
        category = (
            "authentication"
            if "401" in text or "auth" in text
            else "network"
            if "timeout" in text or "network" in text
            else "provider"
        )
        return ClarificationProviderError(
            category,
            "Clarification provider request failed.",
            stage=stage,
            model=model,
            retryable=category == "network",
        )
