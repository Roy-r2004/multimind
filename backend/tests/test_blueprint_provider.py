"""Mocked OpenRouter provider coverage; never makes network calls."""

import json

import pytest
from test_country_blueprint_foundation import valid_structured_blueprint

from app.core.config import Settings
from app.llm import providers as llm_providers
from app.llm.providers import LLMResponse, OpenRouterProvider, OpenRouterProviderError
from app.schemas.api import CountryMaximumCoverageStructuredBlueprint
from app.services.scraping.blueprint_provider import (
    BLUEPRINT_RESEARCH_SYSTEM_PROMPT,
    BLUEPRINT_RESEARCH_WEB_SEARCH_TOOL,
    BLUEPRINT_STRUCTURING_RESPONSE_FORMAT,
    BlueprintProviderError,
    OpenRouterBlueprintProvider,
)


class StubClient:
    def __init__(self, responses: list[LLMResponse | Exception]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    async def complete(self, **kwargs) -> LLMResponse:
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def complete_responses(self, **kwargs) -> LLMResponse:
        raise AssertionError("Blueprint Stage 1 must not call complete_responses")


def _valid_structured_json() -> str:
    return CountryMaximumCoverageStructuredBlueprint.model_validate(
        valid_structured_blueprint()
    ).model_dump_json()


def test_missing_configuration_is_safe_and_does_not_echo_key() -> None:
    provider = OpenRouterBlueprintProvider(Settings(openrouter_api_key=None))
    with pytest.raises(BlueprintProviderError) as exc:
        provider.validate_configuration()
    assert exc.value.category == "configuration"
    assert "KEY" not in str(exc.value)


def test_default_research_model_is_gpt_5_5() -> None:
    assert Settings().openrouter_blueprint_research_model == "openai/gpt-5.5"


def test_default_structuring_model_uses_canonical_openrouter_slug() -> None:
    assert Settings().openrouter_blueprint_structuring_model == "openai/gpt-4.1-mini"


@pytest.mark.asyncio
async def test_openrouter_malformed_model_response_is_safe_and_not_retried(monkeypatch) -> None:
    class Response:
        status_code = 400
        text = '{"error":{"code":"invalid_model","message":"No model found for gpt-4.1-mini"}}'

        def json(self):
            return {
                "error": {
                    "code": "invalid_model",
                    "message": "No model found for gpt-4.1-mini",
                }
            }

    class Client:
        calls = 0

        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            Client.calls += 1
            return Response()

    monkeypatch.setattr(llm_providers.httpx, "AsyncClient", Client)
    provider = OpenRouterProvider()
    provider._api_key = "test-key"

    with pytest.raises(OpenRouterProviderError) as exc:
        await provider.complete(
            system="test system",
            user="test user",
            model="gpt-4.1-mini",
        )

    assert Client.calls == 1
    assert exc.value.http_status == 400
    assert exc.value.provider_error_code == "invalid_model"
    assert exc.value.provider_message == "No model found for gpt-4.1-mini"
    assert exc.value.retryable is False


@pytest.mark.asyncio
async def test_openrouter_generic_provider_error_extracts_nested_metadata(monkeypatch) -> None:
    nested = {
        "error": {
            "message": "Unsupported response_format for this model",
            "type": "invalid_request_error",
            "code": "unsupported_parameter",
        }
    }

    class Response:
        status_code = 400
        text = "Provider returned error"

        def json(self):
            return {
                "error": {
                    "message": "Provider returned error",
                    "code": 400,
                    "metadata": {
                        "provider_name": "OpenAI",
                        "raw": json.dumps(nested),
                    },
                }
            }

    class Client:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(llm_providers.httpx, "AsyncClient", Client)
    provider = OpenRouterProvider()
    provider._api_key = "test-key"

    with pytest.raises(OpenRouterProviderError) as exc:
        await provider.complete(system="s", user="u", model="openai/gpt-4.1-mini")

    assert exc.value.http_status == 400
    assert exc.value.provider_error_code == "unsupported_parameter"
    assert exc.value.provider_error_type == "invalid_request_error"
    assert exc.value.provider_message == "Unsupported response_format for this model"
    assert "Bearer" not in exc.value.provider_message
    assert "raw" not in exc.value.provider_message


@pytest.mark.asyncio
async def test_research_chat_completions_request_parses_text_and_citations(monkeypatch) -> None:
    class Response:
        status_code = 200

        def json(self):
            return {
                "id": "chatcmpl-blueprint-1",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Research report",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://registry.example/source",
                                    "title": "Registry",
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 11},
            }

    class Client:
        request: dict | None = None
        timeout = None

        def __init__(self, **kwargs) -> None:
            Client.timeout = kwargs.get("timeout")

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

    response = await provider.complete(
        system=BLUEPRINT_RESEARCH_SYSTEM_PROMPT,
        user="Research Austria rehabilitation facilities",
        model="openai/gpt-5.5",
        max_tokens=16_000,
        tools=[BLUEPRINT_RESEARCH_WEB_SEARCH_TOOL],
        timeout_seconds=900,
    )

    assert Client.timeout == 900
    assert Client.request["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert Client.request["json"]["model"] == "openai/gpt-5.5"
    assert Client.request["json"]["tools"] == [BLUEPRINT_RESEARCH_WEB_SEARCH_TOOL]
    assert response.text == "Research report"
    assert response.raw["annotations"][0]["url"] == "https://registry.example/source"


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("401 authentication failed", "authentication"),
        ("429 rate limit", "rate_limit"),
        ("402 credit exhausted", "payment"),
        ("model not found", "invalid_model"),
        ("connection reset", "network"),
    ],
)
def test_provider_error_categories_are_safe(message: str, category: str) -> None:
    error = OpenRouterBlueprintProvider._map_error(RuntimeError(message))
    assert error.category == category
    assert str(error) == "OpenRouter blueprint generation failed."


@pytest.mark.asyncio
async def test_provider_research_then_structures_with_json_object_and_retains_citations() -> None:
    research = LLMResponse(
        text="A source-backed research report.",
        tokens_input=13,
        tokens_output=21,
        raw={
            "id": "request-123",
            "annotations": [
                {"url": "https://registry.example/a", "title": "Registry"},
                {"url": 42, "title": "Ignored"},
            ],
        },
    )
    structured = LLMResponse(text=_valid_structured_json(), tokens_input=21, tokens_output=34)
    client = StubClient([research, structured])
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_blueprint_research_model="openai/gpt-5.5",
        openrouter_blueprint_structuring_model="openai/gpt-4.1-mini",
    )

    result = await OpenRouterBlueprintProvider(settings, client=client).generate_blueprint(
        mission=object(),
        rendered_prompt="Austria coverage",
        structured_output_schema=CountryMaximumCoverageStructuredBlueprint,
    )

    assert [call["model"] for call in client.calls] == ["openai/gpt-5.5", "openai/gpt-4.1-mini"]
    assert client.calls[0]["tools"] == [BLUEPRINT_RESEARCH_WEB_SEARCH_TOOL]
    assert client.calls[1]["response_format"] == BLUEPRINT_STRUCTURING_RESPONSE_FORMAT
    assert client.calls[1]["response_format"] == {"type": "json_object"}
    assert "json_schema" not in client.calls[1]
    assert "Canonical JSON skeleton" in client.calls[1]["user"]
    assert "country_containment_rules" in client.calls[1]["user"]
    assert "do not invent facilities" in client.calls[1]["user"]
    assert result.human_readable_blueprint == research.text
    assert result.citations == [
        {
            "url": "https://registry.example/a",
            "title": "Registry",
            "source_type": "openrouter_annotation",
        }
    ]
    assert result.execution_metadata["structuring_correction_attempted"] is False


@pytest.mark.asyncio
async def test_malformed_json_triggers_one_correction_and_succeeds() -> None:
    client = StubClient(
        [
            LLMResponse(text="Research report", tokens_input=1, tokens_output=1, raw={"id": "r1"}),
            LLMResponse(text="{not json", tokens_input=1, tokens_output=1),
            LLMResponse(text=_valid_structured_json(), tokens_input=1, tokens_output=1),
        ]
    )
    result = await OpenRouterBlueprintProvider(
        Settings(openrouter_api_key="test-key"), client=client
    ).generate_blueprint(
        mission=object(),
        rendered_prompt="Austria coverage",
        structured_output_schema=CountryMaximumCoverageStructuredBlueprint,
    )

    assert len(client.calls) == 3
    assert client.calls[1]["response_format"] == {"type": "json_object"}
    assert client.calls[2]["response_format"] == {"type": "json_object"}
    assert "Validation errors:" in client.calls[2]["user"]
    assert "{not json" in client.calls[2]["user"]
    assert result.execution_metadata["structuring_correction_attempted"] is True
    assert result.human_readable_blueprint == "Research report"


@pytest.mark.asyncio
async def test_schema_invalid_json_triggers_one_correction_and_succeeds() -> None:
    invalid = json.dumps({"country_dossier": {"country_name": "Austria"}})
    client = StubClient(
        [
            LLMResponse(text="Research report", tokens_input=1, tokens_output=1),
            LLMResponse(text=invalid, tokens_input=1, tokens_output=1),
            LLMResponse(text=_valid_structured_json(), tokens_input=1, tokens_output=1),
        ]
    )
    result = await OpenRouterBlueprintProvider(
        Settings(openrouter_api_key="test-key"), client=client
    ).generate_blueprint(
        mission=object(),
        rendered_prompt="Austria coverage",
        structured_output_schema=CountryMaximumCoverageStructuredBlueprint,
    )

    assert len(client.calls) == 3
    assert "Validation errors:" in client.calls[2]["user"]
    assert invalid in client.calls[2]["user"]
    assert result.structured_blueprint.country_dossier.country_name == "Austria"
    assert result.execution_metadata["structuring_correction_attempted"] is True


@pytest.mark.asyncio
async def test_failed_correction_persists_safe_structuring_failure_without_partial_data() -> None:
    client = StubClient(
        [
            LLMResponse(
                text="Research report with citations",
                tokens_input=1,
                tokens_output=1,
                raw={"annotations": [{"url": "https://registry.example/a", "title": "A"}]},
            ),
            LLMResponse(text="{not json", tokens_input=1, tokens_output=1),
            LLMResponse(text='{"still":"invalid"}', tokens_input=1, tokens_output=1),
            LLMResponse(text=_valid_structured_json(), tokens_input=1, tokens_output=1),
        ]
    )
    with pytest.raises(BlueprintProviderError) as exc:
        await OpenRouterBlueprintProvider(
            Settings(openrouter_api_key="test-key"), client=client
        ).generate_blueprint(
            mission=object(),
            rendered_prompt="Austria coverage",
            structured_output_schema=CountryMaximumCoverageStructuredBlueprint,
        )

    assert len(client.calls) == 3
    assert exc.value.category == "structured_output"
    assert exc.value.stage == "structuring"
    assert exc.value.model == "openai/gpt-4.1-mini"
    assert exc.value.retryable is False
    assert exc.value.research_text == "Research report with citations"
    assert exc.value.citations == [
        {
            "url": "https://registry.example/a",
            "title": "A",
            "source_type": "openrouter_annotation",
        }
    ]
    assert "test-key" not in str(exc.value)
    assert "Austria coverage" not in str(exc.value)


@pytest.mark.asyncio
async def test_provider_records_safe_research_failure_diagnostics() -> None:
    provider = OpenRouterBlueprintProvider(
        Settings(openrouter_api_key="test-key"),
        client=StubClient(
            [
                OpenRouterProviderError(
                    http_status=400,
                    provider_error_code="invalid_model",
                    provider_error_type=None,
                    provider_message="Model gpt-5.5 was not found; Bearer secret-token",
                    retryable=False,
                )
            ]
        ),
    )

    with pytest.raises(BlueprintProviderError) as exc:
        await provider.generate_blueprint(
            mission=object(),
            rendered_prompt="local prompt must not leak",
            structured_output_schema=CountryMaximumCoverageStructuredBlueprint,
        )

    assert exc.value.safe_diagnostics() == {
        "stage": "research",
        "model": "openai/gpt-5.5",
        "http_status": 400,
        "provider_error_code": "invalid_model",
        "provider_error_type": None,
        "provider_message": "Model gpt-5.5 was not found; Bearer [redacted]",
        "retryable": False,
        "category": "invalid_model",
    }
    assert "test-key" not in str(exc.value)
    assert "local prompt" not in str(exc.value)


@pytest.mark.asyncio
async def test_provider_records_safe_structuring_failure_diagnostics() -> None:
    provider = OpenRouterBlueprintProvider(
        Settings(openrouter_api_key="test-key"),
        client=StubClient(
            [
                LLMResponse(text="Research", tokens_input=1, tokens_output=1),
                OpenRouterProviderError(
                    http_status=400,
                    provider_error_code="unsupported_parameter",
                    provider_error_type="invalid_request_error",
                    provider_message="response_format is unsupported",
                    retryable=False,
                ),
            ]
        ),
    )

    with pytest.raises(BlueprintProviderError) as exc:
        await provider.generate_blueprint(
            mission=object(),
            rendered_prompt="local test",
            structured_output_schema=CountryMaximumCoverageStructuredBlueprint,
        )

    assert exc.value.safe_diagnostics() == {
        "stage": "structuring",
        "model": "openai/gpt-4.1-mini",
        "http_status": 400,
        "provider_error_code": "unsupported_parameter",
        "provider_error_type": "invalid_request_error",
        "provider_message": "response_format is unsupported",
        "retryable": False,
        "category": "provider",
    }
    assert exc.value.research_text == "Research"


@pytest.mark.asyncio
async def test_provider_rejects_empty_research() -> None:
    settings = Settings(openrouter_api_key="test-key")
    empty_provider = OpenRouterBlueprintProvider(
        settings, client=StubClient([LLMResponse(text=" ", tokens_input=0, tokens_output=0)])
    )
    with pytest.raises(BlueprintProviderError, match="empty research") as empty_error:
        await empty_provider.generate_blueprint(
            mission=object(),
            rendered_prompt="local test",
            structured_output_schema=CountryMaximumCoverageStructuredBlueprint,
        )
    assert empty_error.value.category == "empty_research"
