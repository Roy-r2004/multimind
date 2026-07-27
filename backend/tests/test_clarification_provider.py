"""Clarification provider contract tests; no network calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.config import Settings
from app.llm import providers as llm_providers
from app.llm.providers import OpenRouterProvider
from app.schemas.scraping_clarification import (
    ClarificationAllowedValue,
    ClarificationConstraints,
    ClarificationProviderRequest,
    ClarificationType,
)
from app.schemas.scraping_execution_plan import FrozenPlanCountry
from app.services.scraping.clarification_provider import (
    CLARIFICATION_PROVIDER_PREFERENCES,
    CLARIFICATION_SCHEMA_NAME,
    ClarificationProviderError,
    OpenRouterClarificationProvider,
    build_clarification_provider,
    clarification_provider_configured,
    clarification_response_format,
)


def _request() -> ClarificationProviderRequest:
    return ClarificationProviderRequest(
        clarification_id="a" * 40,
        clarification_type=ClarificationType.REGION_REFERENCE_ALIAS,
        field_path="crawl_policy.region_coverage_actions[0].region_name",
        question="Which region?",
        allowed_values=[
            ClarificationAllowedValue(value="Vienna", label="Vienna"),
            ClarificationAllowedValue(value="Tyrol", label="Tyrol"),
        ],
        country=FrozenPlanCountry(
            country_code="AT",
            country_name="Austria",
            country_iso3="AUT",
            continent="Europe",
        ),
        frozen_plan_excerpt={"reference_region_name": "Austria"},
        constraints=ClarificationConstraints(),
    )


def test_factory_returns_openrouter_when_configured() -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_scraper_clarification_model="openai/test-luna-slug",
    )
    assert clarification_provider_configured(settings) is True
    provider = build_clarification_provider(settings)
    assert isinstance(provider, OpenRouterClarificationProvider)


def test_factory_fails_closed_when_model_missing() -> None:
    settings = Settings(openrouter_api_key="test-key", openrouter_scraper_clarification_model="")
    assert clarification_provider_configured(settings) is False
    with pytest.raises(ClarificationProviderError) as exc_info:
        build_clarification_provider(settings)
    assert exc_info.value.category == "configuration_missing"
    assert exc_info.value.retryable is False


def test_factory_fails_closed_when_api_key_missing() -> None:
    settings = Settings(
        openrouter_api_key=None,
        openrouter_scraper_clarification_model="openai/test-luna-slug",
    )
    with pytest.raises(ClarificationProviderError) as exc_info:
        build_clarification_provider(settings)
    assert exc_info.value.category == "configuration_missing"


def test_production_modules_do_not_reference_fake_clarification_provider() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    for path in app_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "FakeClarificationProvider" in text:
            offenders.append(str(path.relative_to(app_root.parent)))
    assert offenders == []


def test_clarification_response_format_is_strict_json_schema() -> None:
    fmt = clarification_response_format()
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == CLARIFICATION_SCHEMA_NAME
    assert fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert schema.get("additionalProperties") is False
    assert "clarification_id" in schema.get("properties", {})
    assert "decision" in schema.get("properties", {})
    assert set(schema.get("required", [])) == set(schema["properties"])
    # No loose json_object shortcut in the clarification contract.
    assert "json_object" not in json.dumps(fmt)


@pytest.mark.asyncio
async def test_openrouter_clarification_payload_is_strict_schema_without_tools(
    monkeypatch,
) -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_scraper_clarification_model="openai/test-luna-slug",
    )

    class Response:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "clarification_id": "a" * 40,
                                    "decision": "RESOLVED",
                                    "selected_value": {
                                        "value": "Vienna",
                                        "label": "Vienna",
                                    },
                                    "reason": "Match",
                                    "confidence": 0.9,
                                    "requires_human_review": False,
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    class Client:
        request: dict | None = None

        def __init__(self, **kwargs) -> None:
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url, **kwargs):
            Client.request = {"url": url, "json": kwargs["json"]}
            return Response()

    monkeypatch.setattr(llm_providers.httpx, "AsyncClient", Client)
    transport = OpenRouterProvider()
    transport._api_key = "test-key"
    provider = OpenRouterClarificationProvider(settings, client=transport)
    response = await provider.clarify(_request())
    assert response.selected_value.value == "Vienna"

    payload = Client.request["json"]
    assert payload["model"] == "openai/test-luna-slug"
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["response_format"]["json_schema"]["name"] == CLARIFICATION_SCHEMA_NAME
    assert payload["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
    assert payload["provider"] == CLARIFICATION_PROVIDER_PREFERENCES
    assert payload["provider"]["require_parameters"] is True
    assert "temperature" not in payload
    assert "tools" not in payload
    assert "plugins" not in payload
    encoded = json.dumps(payload).casefold()
    assert "web_search" not in encoded
    assert "json_object" not in encoded


@pytest.mark.asyncio
async def test_openrouter_provider_uses_configured_slug_and_strict_schema() -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_scraper_clarification_model="openai/test-luna-slug",
    )
    captured: dict = {}

    class StubClient:
        async def complete(self, **kwargs):
            captured.update(kwargs)
            return type(
                "Resp",
                (),
                {
                    "text": json.dumps(
                        {
                            "clarification_id": "a" * 40,
                            "decision": "RESOLVED",
                            "selected_value": {"value": "Vienna", "label": "Vienna"},
                            "reason": "Match",
                            "confidence": 0.9,
                            "requires_human_review": False,
                        }
                    )
                },
            )()

    provider = OpenRouterClarificationProvider(settings, client=StubClient())
    response = await provider.clarify(_request())
    assert response.selected_value.value == "Vienna"
    assert captured["model"] == "openai/test-luna-slug"
    assert captured.get("temperature") is None
    assert "tools" not in captured
    assert captured["provider"] == {"require_parameters": True}
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert "web_search" not in json.dumps(captured).casefold()


@pytest.mark.asyncio
async def test_unknown_clarification_id_and_disallowed_value_rejected() -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_scraper_clarification_model="openai/test-luna-slug",
    )

    class BadIdClient:
        async def complete(self, **kwargs):
            return type(
                "Resp",
                (),
                {
                    "text": json.dumps(
                        {
                            "clarification_id": "b" * 40,
                            "decision": "RESOLVED",
                            "selected_value": {"value": "Vienna", "label": "Vienna"},
                            "reason": "x",
                            "confidence": 0.5,
                            "requires_human_review": False,
                        }
                    )
                },
            )()

    provider = OpenRouterClarificationProvider(settings, client=BadIdClient())
    with pytest.raises(ClarificationProviderError):
        await provider.clarify(_request())

    class BadValueClient:
        calls = 0

        async def complete(self, **kwargs):
            BadValueClient.calls += 1
            return type(
                "Resp",
                (),
                {
                    "text": json.dumps(
                        {
                            "clarification_id": "a" * 40,
                            "decision": "RESOLVED",
                            "selected_value": {"value": "Salzburg", "label": "Salzburg"},
                            "reason": "x",
                            "confidence": 0.5,
                            "requires_human_review": False,
                        }
                    )
                },
            )()

    provider = OpenRouterClarificationProvider(settings, client=BadValueClient())
    with pytest.raises(ClarificationProviderError):
        await provider.clarify(_request())
    assert BadValueClient.calls == 2  # one repair attempt


@pytest.mark.asyncio
async def test_extra_response_fields_rejected() -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_scraper_clarification_model="openai/test-luna-slug",
    )

    class ExtraClient:
        async def complete(self, **kwargs):
            return type(
                "Resp",
                (),
                {
                    "text": json.dumps(
                        {
                            "clarification_id": "a" * 40,
                            "decision": "RESOLVED",
                            "selected_value": {"value": "Vienna", "label": "Vienna"},
                            "reason": "x",
                            "confidence": 0.5,
                            "requires_human_review": False,
                            "extra_field": "nope",
                        }
                    )
                },
            )()

    provider = OpenRouterClarificationProvider(settings, client=ExtraClient())
    with pytest.raises(ClarificationProviderError):
        await provider.clarify(_request())


@pytest.mark.asyncio
async def test_scope_expanding_url_response_rejected_without_repair() -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_scraper_clarification_model="openai/test-luna-slug",
    )

    class UrlClient:
        calls = 0

        async def complete(self, **kwargs):
            UrlClient.calls += 1
            return type(
                "Resp",
                (),
                {
                    "text": json.dumps(
                        {
                            "clarification_id": "a" * 40,
                            "decision": "RESOLVED",
                            "selected_value": {
                                "value": "https://evil.example/new",
                                "label": "https://evil.example/new",
                            },
                            "reason": "invented url outside allowed values",
                            "confidence": 0.5,
                            "requires_human_review": False,
                        }
                    )
                },
            )()

    provider = OpenRouterClarificationProvider(settings, client=UrlClient())
    with pytest.raises(ClarificationProviderError) as exc_info:
        await provider.clarify(_request())
    assert exc_info.value.category == "scope_violation"
    assert UrlClient.calls == 1


@pytest.mark.asyncio
async def test_malformed_json_receives_at_most_one_repair_attempt() -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_scraper_clarification_model="openai/test-luna-slug",
    )

    class BadJsonClient:
        calls = 0

        async def complete(self, **kwargs):
            BadJsonClient.calls += 1
            return type("Resp", (), {"text": "{not-json"})()

    provider = OpenRouterClarificationProvider(settings, client=BadJsonClient())
    with pytest.raises(ClarificationProviderError) as exc_info:
        await provider.clarify(_request())
    assert BadJsonClient.calls == 2
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_retryable_and_non_retryable_error_taxonomy() -> None:
    from app.llm.providers import OpenRouterProviderError

    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_scraper_clarification_model="openai/test-luna-slug",
    )

    class RateLimitClient:
        async def complete(self, **kwargs):
            raise OpenRouterProviderError(
                http_status=429,
                provider_error_code="rate_limited",
                provider_error_type="rate_limit",
                provider_message="rate limited",
                retryable=True,
            )

    provider = OpenRouterClarificationProvider(settings, client=RateLimitClient())
    with pytest.raises(ClarificationProviderError) as retryable:
        await provider.clarify(_request())
    assert retryable.value.retryable is True
    assert retryable.value.category == "rate_limited"
    rate_limit_diagnostics = retryable.value.safe_diagnostics()
    assert rate_limit_diagnostics["provider_error_code"] == "rate_limited"
    assert rate_limit_diagnostics["provider_error_type"] == "rate_limit"
    assert rate_limit_diagnostics["provider_message"] == "rate limited"
    assert "test-key" not in json.dumps(rate_limit_diagnostics)

    class AuthClient:
        async def complete(self, **kwargs):
            raise OpenRouterProviderError(
                http_status=401,
                provider_error_code="unauthorized",
                provider_error_type="auth",
                provider_message="unauthorized",
                retryable=False,
            )

    provider = OpenRouterClarificationProvider(settings, client=AuthClient())
    with pytest.raises(ClarificationProviderError) as auth:
        await provider.clarify(_request())
    assert auth.value.retryable is False
    assert auth.value.category == "authentication"
    auth_diagnostics = auth.value.safe_diagnostics()
    assert "test-key" not in str(auth_diagnostics)
    assert "test-key" not in json.dumps(auth_diagnostics)

    class SecretLeakClient:
        async def complete(self, **kwargs):
            raise OpenRouterProviderError(
                http_status=404,
                provider_error_code="404",
                provider_error_type="invalid_request_error",
                provider_message="api_key=sk-or-v1-secret-token",
                retryable=False,
            )

    provider = OpenRouterClarificationProvider(settings, client=SecretLeakClient())
    with pytest.raises(ClarificationProviderError) as secret:
        await provider.clarify(_request())
    secret_diagnostics = secret.value.safe_diagnostics()
    assert "sk-or-v1-secret-token" not in json.dumps(secret_diagnostics)
    assert "api_key=[redacted]" in secret_diagnostics["provider_message"]


@pytest.mark.asyncio
async def test_generic_transport_omits_tools_and_provider_unless_supplied(monkeypatch) -> None:
    class Response:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    class Client:
        request: dict | None = None

        def __init__(self, **kwargs) -> None:
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url, **kwargs):
            Client.request = {"url": url, "json": kwargs["json"]}
            return Response()

    monkeypatch.setattr(llm_providers.httpx, "AsyncClient", Client)
    provider = OpenRouterProvider()
    provider._api_key = "test-key"

    await provider.complete(system="s", user="u", model="openai/gpt-4.1-mini")
    legacy = Client.request["json"]
    assert legacy["temperature"] == 0.7
    assert "tools" not in legacy
    assert "provider" not in legacy
    assert legacy["model"] == "openai/gpt-4.1-mini"

    await provider.complete(
        system="s",
        user="u",
        model="openai/gpt-4.1-mini",
        temperature=None,
    )
    omitted = Client.request["json"]
    assert "temperature" not in omitted

    await provider.complete(
        system="s",
        user="u",
        model="openai/gpt-4.1-mini",
        tools=[{"type": "web_search"}],
        provider={"require_parameters": True},
        response_format={"type": "json_object"},
    )
    extended = Client.request["json"]
    assert extended["tools"] == [{"type": "web_search"}]
    assert extended["provider"] == {"require_parameters": True}
    assert extended["response_format"] == {"type": "json_object"}
    assert extended["temperature"] == 0.7


@pytest.mark.asyncio
async def test_openrouter_clarification_repair_request_omits_temperature(
    monkeypatch,
) -> None:
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_scraper_clarification_model="openai/test-luna-slug",
    )

    class Response:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"content": "{not-json"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    class Client:
        requests: list[dict] = []

        def __init__(self, **kwargs) -> None:
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, url, **kwargs):
            Client.requests.append({"url": url, "json": kwargs["json"]})
            return Response()

    monkeypatch.setattr(llm_providers.httpx, "AsyncClient", Client)
    transport = OpenRouterProvider()
    transport._api_key = "test-key"
    provider = OpenRouterClarificationProvider(settings, client=transport)
    with pytest.raises(ClarificationProviderError):
        await provider.clarify(_request())

    assert len(Client.requests) == 2
    requests_payload = [req_record["json"] for req_record in Client.requests]
    initial_user_contents = {payload["messages"][1]["content"] for payload in requests_payload}
    assert any(
        "The previous JSON was invalid" not in content for content in initial_user_contents
    )
    assert any("The previous JSON was invalid" in content for content in initial_user_contents)

    for payload in requests_payload:
        assert "temperature" not in payload
        assert payload["response_format"]["type"] == "json_schema"
        assert payload["response_format"]["json_schema"]["strict"] is True
        assert payload["provider"] == CLARIFICATION_PROVIDER_PREFERENCES
        assert "tools" not in payload
        assert "plugins" not in payload
        encoded = json.dumps(payload).casefold()
        assert "web_search" not in encoded
