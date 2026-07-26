"""Mocked OpenRouter provider coverage; never makes network calls."""

import pytest
from test_country_blueprint_foundation import valid_structured_blueprint

from app.core.config import Settings
from app.llm.providers import LLMResponse
from app.schemas.api import CountryMaximumCoverageStructuredBlueprint
from app.services.scraping.blueprint_provider import (
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


def test_missing_configuration_is_safe_and_does_not_echo_key() -> None:
    provider = OpenRouterBlueprintProvider(Settings(openrouter_api_key=None))
    with pytest.raises(BlueprintProviderError) as exc:
        provider.validate_configuration()
    assert exc.value.category == "configuration"
    assert "KEY" not in str(exc.value)


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
async def test_provider_research_then_structures_and_retains_valid_citations() -> None:
    research = LLMResponse(
        text="A source-backed research report.",
        tokens_input=13,
        tokens_output=21,
        raw={
            "id": "request-123",
            "annotations": [
                {"url": "https://registry.example/a", "title": "Registry"},
                {"url": 42, "title": "Ignored"},
                "not-an-annotation",
            ],
        },
    )
    structured = LLMResponse(
        text=CountryMaximumCoverageStructuredBlueprint.model_validate(
            valid_structured_blueprint()
        ).model_dump_json(),
        tokens_input=21,
        tokens_output=34,
    )
    client = StubClient([research, structured])
    settings = Settings(
        openrouter_api_key="test-key",
        openrouter_blueprint_research_model="research-model",
        openrouter_blueprint_structuring_model="structuring-model",
    )

    result = await OpenRouterBlueprintProvider(settings, client=client).generate_blueprint(
        mission=object(),
        rendered_prompt="Austria coverage",
        structured_output_schema=CountryMaximumCoverageStructuredBlueprint,
    )

    assert [call["model"] for call in client.calls] == ["research-model", "structuring-model"]
    assert client.calls[0]["user"] == "Austria coverage"
    assert client.calls[1]["user"] == research.text
    assert client.calls[1]["response_format"]["type"] == "json_schema"
    assert result.human_readable_blueprint == research.text
    assert result.operation_id == "request-123"
    assert result.citations == [
        {
            "url": "https://registry.example/a",
            "title": "Registry",
            "source_type": "openrouter_annotation",
        }
    ]
    assert result.execution_metadata == {
        "research_model": "research-model",
        "structuring_model": "structuring-model",
        "research_tokens_input": 13,
        "research_tokens_output": 21,
    }


@pytest.mark.asyncio
async def test_provider_maps_provider_failures_without_exposing_response_details() -> None:
    provider = OpenRouterBlueprintProvider(
        Settings(openrouter_api_key="test-key"),
        client=StubClient([RuntimeError("OpenRouter error (429): internal provider detail")]),
    )

    with pytest.raises(BlueprintProviderError) as exc:
        await provider.generate_blueprint(
            mission=object(),
            rendered_prompt="local test",
            structured_output_schema=CountryMaximumCoverageStructuredBlueprint,
        )

    assert exc.value.category == "rate_limit"
    assert str(exc.value) == "OpenRouter blueprint generation failed."


@pytest.mark.asyncio
async def test_provider_rejects_empty_research_and_invalid_structured_response() -> None:
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

    invalid_provider = OpenRouterBlueprintProvider(
        settings,
        client=StubClient(
            [
                LLMResponse(text="Research", tokens_input=0, tokens_output=0),
                LLMResponse(text="{not json", tokens_input=0, tokens_output=0),
            ]
        ),
    )
    with pytest.raises(BlueprintProviderError, match="invalid structured output") as invalid_error:
        await invalid_provider.generate_blueprint(
            mission=object(),
            rendered_prompt="local test",
            structured_output_schema=CountryMaximumCoverageStructuredBlueprint,
        )
    assert invalid_error.value.category == "structured_output"
