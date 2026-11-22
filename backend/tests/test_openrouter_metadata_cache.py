"""Output capability cache lifecycle checks; no live OpenRouter calls."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
import pytest

from app.core.config import get_settings
from app.llm import pricing
from app.services import document_context


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload=None, error=None, **_kwargs):
        self.payload = payload
        self.error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get(self, *_args, **_kwargs):
        if self.error:
            raise self.error
        return _Response(self.payload)


def _model(slug: str, limit, *, pricing_data=None):
    return {
        "id": slug,
        "name": slug,
        "context_length": 200_000,
        "top_provider": {"max_completion_tokens": limit},
        "pricing": pricing_data if pricing_data is not None else {"prompt": "0", "completion": "0"},
    }


@pytest.fixture
def cache_file(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_context_cache_path", str(tmp_path / "model-cache.json"))
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    return tmp_path / "model-cache.json"


@pytest.mark.asyncio
async def test_refresh_persists_output_metadata_and_restart_restores_it(cache_file, monkeypatch):
    slug = "test/powerful-model"
    item = _model(slug, 128_000)
    item.pop("pricing")
    payload = {"data": [item]}
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda **kwargs: _Client(payload, **kwargs))

    first = pricing.OpenRouterPricingService()
    monkeypatch.setattr(pricing, "get_pricing_service", lambda: first)
    monkeypatch.setattr(document_context, "get_pricing_service", lambda: first)
    await first.refresh(force=True)
    assert document_context.lookup_model_output_limit(slug) == (50_000, "provider_metadata")

    cached = json.loads(cache_file.read_text(encoding="utf-8"))
    assert cached["models"][slug]["top_provider"]["max_completion_tokens"] == 128_000
    second = pricing.OpenRouterPricingService()
    monkeypatch.setattr(pricing, "get_pricing_service", lambda: second)
    monkeypatch.setattr(document_context, "get_pricing_service", lambda: second)
    assert second.output_metadata_is_reliable()
    assert document_context.lookup_model_output_limit(slug) == (50_000, "provider_metadata")


@pytest.mark.asyncio
async def test_refresh_replaces_decreased_output_limit_and_missing_metadata(cache_file, monkeypatch):
    slug = "test/model-limit-changed"
    service = pricing.OpenRouterPricingService()
    monkeypatch.setattr(pricing, "get_pricing_service", lambda: service)
    monkeypatch.setattr(document_context, "get_pricing_service", lambda: service)
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda **kwargs: _Client({"data": [_model(slug, 64_000)]}, **kwargs))
    await service.refresh(force=True)
    assert document_context.lookup_model_output_limit(slug)[0] == 50_000

    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda **kwargs: _Client({"data": [_model(slug, 16_000)]}, **kwargs))
    await service.refresh(force=True)
    assert document_context.lookup_model_output_limit(slug) == (16_000, "provider_metadata")

    # Missing on the new catalog snapshot must clear the previous model entry.
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda **kwargs: _Client({"data": []}, **kwargs))
    await service.refresh(force=True)
    assert document_context.lookup_model_output_limit(slug) == (4_096, "conservative_estimate")


@pytest.mark.asyncio
async def test_expired_cache_is_conservative_after_failed_refresh(cache_file, monkeypatch):
    slug = "test/stale-model"
    fetched_at = datetime.now(UTC) - timedelta(seconds=pricing.MAX_OUTPUT_METADATA_STALE_SECONDS + 1)
    cache_file.write_text(
        json.dumps({
            "fetched_at": fetched_at.isoformat(),
            "models": {slug: _model(slug, 128_000)},
        }),
        encoding="utf-8",
    )
    service = pricing.OpenRouterPricingService()
    monkeypatch.setattr(pricing, "get_pricing_service", lambda: service)
    monkeypatch.setattr(document_context, "get_pricing_service", lambda: service)
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda **kwargs: _Client(error=OSError("offline"), **kwargs))

    await service.ensure_context_metadata()

    assert service.get_slug_metadata(slug)["top_provider"]["max_completion_tokens"] == 128_000
    assert not service.output_metadata_is_reliable()
    assert document_context.lookup_model_output_limit(slug) == (4_096, "conservative_estimate")


@pytest.mark.asyncio
async def test_recent_stale_cache_is_used_during_temporary_refresh_failure(cache_file, monkeypatch):
    slug = "test/recent-stale-model"
    fetched_at = datetime.now(UTC) - timedelta(hours=2)
    cache_file.write_text(
        json.dumps({"fetched_at": fetched_at.isoformat(), "models": {slug: _model(slug, 32_000)}}),
        encoding="utf-8",
    )
    service = pricing.OpenRouterPricingService()
    monkeypatch.setattr(pricing, "get_pricing_service", lambda: service)
    monkeypatch.setattr(document_context, "get_pricing_service", lambda: service)
    monkeypatch.setattr(pricing.httpx, "AsyncClient", lambda **kwargs: _Client(error=OSError("offline"), **kwargs))

    await service.ensure_context_metadata()

    assert service.output_metadata_is_reliable()
    assert document_context.lookup_model_output_limit(slug) == (32_000, "provider_metadata")


def test_old_context_only_cache_does_not_invent_output_capability(cache_file, monkeypatch):
    slug = "test/legacy-cache-model"
    cache_file.write_text(json.dumps({slug: 200_000}), encoding="utf-8")
    service = pricing.OpenRouterPricingService()
    monkeypatch.setattr(pricing, "get_pricing_service", lambda: service)
    monkeypatch.setattr(document_context, "get_pricing_service", lambda: service)

    assert service.get_slug_metadata(slug)["context_length"] == 200_000
    assert document_context.lookup_model_output_limit(slug) == (4_096, "conservative_estimate")
