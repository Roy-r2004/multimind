"""Shadow Council OpenRouter payload: reasoning + web plugin, production unchanged."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm.catalog import slug_to_model_id
from app.llm.providers import (
    OpenRouterProvider,
    SHADOW_WEB_PLUGIN,
    _content_to_text,
    build_openrouter_chat_payload,
    normalize_shadow_reasoning_effort,
)


NEMOTRON = "nvidia/nemotron-3-ultra-550b-a55b"
QWEN_SHADOW = "qwen/qwen3.8-max-0902"
DEEPSEEK_SHADOW = "deepseek/deepseek-v4.1-flash"
SHADOW_EFFORTS = {
    NEMOTRON: "medium",
    QWEN_SHADOW: "medium",
    DEEPSEEK_SHADOW: "low",
}
PRODUCTION_MODELS = (
    "openai/gpt-4.1",
    "gpt-4.1",
    "google/gemini-2.5-pro",
    "gemini",
    "deepseek/deepseek-chat-v3-0324",
    "deepseek",
    "qwen/qwen-2.5-72b-instruct",
    "qwen",
)


def _assert_shadow_reasoning_and_web(payload: dict, *, effort: str) -> None:
    assert payload["reasoning"] == {
        "enabled": True,
        "effort": effort,
        "exclude": True,
    }
    assert payload["plugins"] == [
        {
            "id": "web",
            "engine": "parallel",
            "mode": "turbo",
            "max_results": 3,
        }
    ]
    assert payload["plugins"] == [{**SHADOW_WEB_PLUGIN}]
    assert not str(payload["model"]).endswith(":online")


@pytest.mark.parametrize("model,effort", list(SHADOW_EFFORTS.items()))
def test_shadow_slugs_use_fast_reasoning_and_parallel_turbo_web(model: str, effort: str) -> None:
    payload = build_openrouter_chat_payload(
        model=model,
        system="sys",
        user="hello",
        max_tokens=256,
    )
    assert payload["model"] == model
    _assert_shadow_reasoning_and_web(payload, effort=effort)


@pytest.mark.parametrize("slug,effort", list(SHADOW_EFFORTS.items()))
def test_shadow_or_ids_use_fast_reasoning_and_parallel_turbo_web(slug: str, effort: str) -> None:
    model_id = slug_to_model_id(slug)
    payload = build_openrouter_chat_payload(
        model=model_id,
        system="sys",
        user="hello",
        max_tokens=256,
    )
    assert payload["model"] == model_id
    _assert_shadow_reasoning_and_web(payload, effort=effort)


@pytest.mark.parametrize("model", PRODUCTION_MODELS)
def test_production_models_do_not_receive_shadow_reasoning_or_web(model: str) -> None:
    payload = build_openrouter_chat_payload(
        model=model,
        system="sys",
        user="hello",
        max_tokens=128,
    )
    assert "reasoning" not in payload
    assert "plugins" not in payload
    assert payload["usage"] == {"include": True}


def test_qwen_explicit_medium_is_not_overridden_by_xhigh_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _meta(slug: str):
        if slug == QWEN_SHADOW:
            return {
                "reasoning": {
                    "mandatory": True,
                    "default_enabled": True,
                    "supported_efforts": ["xhigh", "high", "medium", "low", "minimal"],
                    "default_effort": "xhigh",
                }
            }
        return None

    monkeypatch.setattr(
        "app.llm.pricing.get_pricing_service",
        lambda: SimpleNamespace(get_slug_metadata=_meta),
    )
    assert normalize_shadow_reasoning_effort(QWEN_SHADOW) == "medium"
    payload = build_openrouter_chat_payload(
        model=QWEN_SHADOW,
        system="sys",
        user="hello",
        max_tokens=256,
    )
    _assert_shadow_reasoning_and_web(payload, effort="medium")


def test_medium_preference_falls_down_to_low_when_medium_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _meta(slug: str):
        if slug == NEMOTRON:
            return {"reasoning": {"supported_efforts": ["max", "high", "low"]}}
        return None

    monkeypatch.setattr(
        "app.llm.pricing.get_pricing_service",
        lambda: SimpleNamespace(get_slug_metadata=_meta),
    )
    assert normalize_shadow_reasoning_effort(NEMOTRON) == "low"
    payload = build_openrouter_chat_payload(
        model=NEMOTRON,
        system="sys",
        user="hello",
        max_tokens=256,
    )
    _assert_shadow_reasoning_and_web(payload, effort="low")


def test_deepseek_keeps_low_instead_of_upgrading_to_high(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _meta(slug: str):
        if slug == DEEPSEEK_SHADOW:
            return {"reasoning": {"supported_efforts": ["max", "high", "low"]}}
        return None

    monkeypatch.setattr(
        "app.llm.pricing.get_pricing_service",
        lambda: SimpleNamespace(get_slug_metadata=_meta),
    )
    assert normalize_shadow_reasoning_effort(DEEPSEEK_SHADOW) == "low"
    payload = build_openrouter_chat_payload(
        model=DEEPSEEK_SHADOW,
        system="sys",
        user="hello",
        max_tokens=256,
    )
    _assert_shadow_reasoning_and_web(payload, effort="low")


def test_content_parser_keeps_final_text_and_drops_reasoning_parts() -> None:
    assert _content_to_text("Final answer only.") == "Final answer only."
    assert (
        _content_to_text(
            [
                {"type": "reasoning", "text": "SECRET_CHAIN_OF_THOUGHT"},
                {"type": "text", "text": "Visible council answer."},
            ]
        )
        == "Visible council answer."
    )


@pytest.mark.asyncio
async def test_shadow_provider_hides_reasoning_and_keeps_usage_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "Council-visible answer.",
                            "reasoning": "SECRET_INTERNAL_TRACE",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 21,
                    "completion_tokens": 44,
                    "completion_tokens_details": {"reasoning_tokens": 30},
                    "cost": 0.123456,
                },
            }

        @property
        def text(self):
            return ""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, *, headers=None, json=None):
            captured["json"] = json
            return _FakeResponse()

    monkeypatch.setattr("app.llm.providers.httpx.AsyncClient", _FakeClient)
    provider = OpenRouterProvider()
    provider._api_key = "test-key"
    response = await provider.complete(
        system="sys",
        user="hello",
        model=NEMOTRON,
        max_tokens=256,
    )
    _assert_shadow_reasoning_and_web(captured["json"], effort="medium")
    assert response.text == "Council-visible answer."
    assert "SECRET_INTERNAL_TRACE" not in response.text
    assert response.tokens_input == 21
    assert response.tokens_output == 44
    assert response.cost_usd == pytest.approx(0.123456)
