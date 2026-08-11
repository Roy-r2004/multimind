"""OpenRouter quality routing for Claude Fable 5."""

from __future__ import annotations

from app.llm.providers import (
    CLAUDE_FABLE_5_ROUTED_SLUG,
    CLAUDE_FABLE_5_SLUG,
    build_openrouter_chat_payload,
    openrouter_provider_preferences,
    resolve_openrouter_model_slug,
)


def test_fable_5_uses_exacto_and_allows_provider_fallbacks() -> None:
    payload = build_openrouter_chat_payload(
        model=CLAUDE_FABLE_5_SLUG,
        system="sys",
        user="hello",
        max_tokens=128,
    )
    assert payload["model"] == CLAUDE_FABLE_5_ROUTED_SLUG
    assert payload["provider"] == {"allow_fallbacks": True}


def test_fable_5_preserves_explicit_variant_suffix() -> None:
    assert resolve_openrouter_model_slug("anthropic/claude-fable-5:nitro") == (
        "anthropic/claude-fable-5:nitro"
    )
    prefs = openrouter_provider_preferences("anthropic/claude-fable-5:nitro")
    assert prefs == {"allow_fallbacks": True}


def test_other_models_keep_default_routing_shape() -> None:
    payload = build_openrouter_chat_payload(
        model="openai/gpt-4.1",
        system="sys",
        user="hello",
        max_tokens=128,
    )
    assert payload["model"] == "openai/gpt-4.1"
    assert "provider" not in payload
    assert resolve_openrouter_model_slug("google/gemini-2.5-pro") == "google/gemini-2.5-pro"
    assert openrouter_provider_preferences("openai/gpt-5.5") is None
