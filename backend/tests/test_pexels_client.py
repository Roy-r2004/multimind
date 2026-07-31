"""Unit tests for the Pexels client — every failure mode must be non-fatal."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.services.scraping.pexels_client import PexelsClient


class _FakeAsyncClient:
    def __init__(self, response: httpx.Response | None = None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.requests: list[dict[str, Any]] = []

    def __call__(self, *, timeout: float) -> "_FakeAsyncClient":
        self._timeout = timeout
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url, *, headers, params):
        self.requests.append({"url": url, "headers": headers, "params": params})
        if self._error is not None:
            raise self._error
        return self._response


@pytest.mark.asyncio
async def test_search_landscape_returns_first_large2x_url(monkeypatch):
    fake_client = _FakeAsyncClient(
        response=httpx.Response(
            200,
            json={
                "photos": [
                    {"src": {"large2x": "https://images.pexels.com/austria-large2x.jpg", "large": "https://images.pexels.com/austria-large.jpg"}}
                ]
            },
        )
    )
    monkeypatch.setenv("PEXELS_API_KEY", "secret-pexels-key")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    try:
        url = await PexelsClient().search_landscape("Austria landscape")
    finally:
        get_settings.cache_clear()

    assert url == "https://images.pexels.com/austria-large2x.jpg"
    assert fake_client.requests[0]["headers"]["Authorization"] == "secret-pexels-key"
    assert fake_client.requests[0]["params"]["query"] == "Austria landscape"


@pytest.mark.asyncio
async def test_search_landscape_falls_back_to_large_when_large2x_missing(monkeypatch):
    fake_client = _FakeAsyncClient(
        response=httpx.Response(
            200, json={"photos": [{"src": {"large": "https://images.pexels.com/only-large.jpg"}}]}
        )
    )
    monkeypatch.setenv("PEXELS_API_KEY", "secret-pexels-key")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    try:
        url = await PexelsClient().search_landscape("Finland landscape")
    finally:
        get_settings.cache_clear()

    assert url == "https://images.pexels.com/only-large.jpg"


@pytest.mark.asyncio
async def test_search_landscape_returns_none_when_key_missing(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        url = await PexelsClient().search_landscape("Belarus landscape")
    finally:
        get_settings.cache_clear()
    assert url is None


@pytest.mark.asyncio
async def test_search_landscape_returns_none_on_blank_query(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "secret-pexels-key")
    get_settings.cache_clear()
    try:
        url = await PexelsClient().search_landscape("   ")
    finally:
        get_settings.cache_clear()
    assert url is None


@pytest.mark.asyncio
async def test_search_landscape_returns_none_on_network_error(monkeypatch):
    fake_client = _FakeAsyncClient(error=httpx.ConnectError("boom"))
    monkeypatch.setenv("PEXELS_API_KEY", "secret-pexels-key")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    try:
        url = await PexelsClient().search_landscape("Lebanon landscape")
    finally:
        get_settings.cache_clear()
    assert url is None


@pytest.mark.asyncio
async def test_search_landscape_returns_none_on_non_200(monkeypatch):
    fake_client = _FakeAsyncClient(response=httpx.Response(401, json={"error": "unauthorized"}))
    monkeypatch.setenv("PEXELS_API_KEY", "bad-key")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    try:
        url = await PexelsClient().search_landscape("Austria landscape")
    finally:
        get_settings.cache_clear()
    assert url is None


@pytest.mark.asyncio
async def test_search_landscape_returns_none_on_malformed_response(monkeypatch):
    fake_client = _FakeAsyncClient(response=httpx.Response(200, json={"photos": []}))
    monkeypatch.setenv("PEXELS_API_KEY", "secret-pexels-key")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "AsyncClient", fake_client)
    try:
        url = await PexelsClient().search_landscape("Austria landscape")
    finally:
        get_settings.cache_clear()
    assert url is None
