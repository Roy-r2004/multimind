"""Brave Search provider implementation for source discovery."""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.services.scraping.search_providers.base import (
    SearchProviderAuthError,
    SearchProviderConfigurationError,
    SearchProviderInvalidResponseError,
    SearchProviderRateLimitedError,
    SearchProviderRequest,
    SearchProviderResult,
    SearchProviderTimeoutError,
    SearchProviderUnavailableError,
)
from app.services.scraping.url_canonicalization import UrlRejected, canonicalize_discovery_url

MAX_TITLE_LENGTH = 300
MAX_SNIPPET_LENGTH = 1000
MAX_RESULT_LIMIT = 20


class BraveSearchProvider:
    name = "brave"

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.brave_search_api_key
        self._base_url = settings.brave_search_base_url
        self._timeout = settings.brave_search_timeout_seconds
        self._default_limit = settings.brave_search_results_per_query

    async def search(self, request: SearchProviderRequest) -> list[SearchProviderResult]:
        if not self._api_key:
            raise SearchProviderConfigurationError("BRAVE_SEARCH_API_KEY is not configured")
        query = request.query.strip()
        if not query:
            raise SearchProviderInvalidResponseError("Search query cannot be blank")

        count = min(max(request.result_limit or self._default_limit, 1), MAX_RESULT_LIMIT)
        payload = await self._request(query=query, country=request.country_code, language=request.search_language, count=count)
        return self._parse(payload)

    @retry(
        retry=retry_if_exception_type((SearchProviderTimeoutError, SearchProviderUnavailableError)),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    async def _request(self, *, query: str, country: str, language: str, count: int) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self._api_key or "",
        }
        params = {
            "q": query,
            "count": count,
            "country": country.upper(),
            "search_lang": language.lower(),
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self._base_url, headers=headers, params=params)
        except httpx.TimeoutException as exc:
            raise SearchProviderTimeoutError("Brave Search request timed out") from exc
        except httpx.HTTPError as exc:
            raise SearchProviderUnavailableError("Brave Search request failed") from exc

        if response.status_code in {401, 403}:
            raise SearchProviderAuthError("Brave Search authentication failed")
        if response.status_code == 429:
            raise SearchProviderRateLimitedError("Brave Search rate limit exceeded")
        if response.status_code >= 500:
            raise SearchProviderUnavailableError("Brave Search is unavailable")
        if response.status_code >= 400:
            raise SearchProviderInvalidResponseError(f"Brave Search returned HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise SearchProviderInvalidResponseError("Brave Search returned non-JSON response") from exc
        if not isinstance(data, dict):
            raise SearchProviderInvalidResponseError("Brave Search response root is invalid")
        return data

    def _parse(self, payload: dict[str, Any]) -> list[SearchProviderResult]:
        web = payload.get("web")
        if web is None:
            return []
        if not isinstance(web, dict):
            raise SearchProviderInvalidResponseError("Brave Search web section is invalid")
        raw_results = web.get("results", [])
        if not isinstance(raw_results, list):
            raise SearchProviderInvalidResponseError("Brave Search results section is invalid")

        parsed: list[SearchProviderResult] = []
        for provider_rank, raw in enumerate(raw_results, start=1):
            if not isinstance(raw, dict):
                raise SearchProviderInvalidResponseError("Brave Search result item is invalid")
            url = raw.get("url")
            if not isinstance(url, str):
                raise SearchProviderInvalidResponseError("Brave Search result URL is invalid")
            try:
                canonicalize_discovery_url(url)
            except UrlRejected:
                continue
            parsed.append(
                SearchProviderResult(
                    rank=provider_rank,
                    provider_result_id=_bounded_optional(raw.get("profile", {}).get("url") if isinstance(raw.get("profile"), dict) else raw.get("url"), 255),
                    url=url,
                    title=_bounded_text(raw.get("title"), MAX_TITLE_LENGTH),
                    snippet=_bounded_text(raw.get("description"), MAX_SNIPPET_LENGTH),
                    metadata={
                        "family_friendly": raw.get("family_friendly"),
                        "age": _bounded_optional(raw.get("age"), 80),
                    },
                )
            )
        return parsed


def _bounded_text(value: Any, max_length: int) -> str:
    text = str(value or "").strip()
    return text[:max_length]


def _bounded_optional(value: Any, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_length] or None
