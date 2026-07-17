"""Serper organic search provider implementation for source discovery."""

from __future__ import annotations

import re
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
COUNTRY_RE = re.compile(r"^[a-z]{2}$")
LANGUAGE_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2})?$")
SUPPORTED_GOOGLE_LANGUAGES = {
    "ar",
    "de",
    "en",
    "es",
    "fr",
    "it",
    "nl",
    "pl",
    "pt",
    "ru",
    "tr",
}


class SerperSearchProvider:
    name = "serper"

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.serper_api_key
        self._base_url = settings.serper_search_base_url
        self._timeout = settings.serper_search_timeout_seconds
        self._default_limit = settings.serper_search_results_per_query

    async def search(self, request: SearchProviderRequest) -> list[SearchProviderResult]:
        if not self._api_key:
            raise SearchProviderConfigurationError("SERPER_API_KEY is not configured")
        query = request.query.strip()
        if not query:
            raise SearchProviderInvalidResponseError("Search query cannot be blank")

        count = min(max(request.result_limit or self._default_limit, 1), MAX_RESULT_LIMIT)
        payload = await self._request(
            query=query,
            country=request.country_code,
            language=request.search_language,
            count=count,
        )
        return self._parse(payload)

    @retry(
        retry=retry_if_exception_type((SearchProviderTimeoutError, SearchProviderUnavailableError)),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    async def _request(self, *, query: str, country: str, language: str, count: int) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "X-API-KEY": self._api_key or "",
        }
        body: dict[str, Any] = {
            "q": query,
            "num": count,
        }
        gl = _google_country(country)
        if gl:
            body["gl"] = gl
        hl = _google_language(language)
        if hl:
            body["hl"] = hl

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(self._base_url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise SearchProviderTimeoutError("Serper Search request timed out") from exc
        except httpx.HTTPError as exc:
            raise SearchProviderUnavailableError("Serper Search request failed") from exc

        if response.status_code in {401, 403}:
            raise SearchProviderAuthError("Serper Search authentication failed")
        if response.status_code == 429:
            raise SearchProviderRateLimitedError("Serper Search rate or credit limit exceeded")
        if response.status_code >= 500:
            raise SearchProviderUnavailableError("Serper Search is unavailable")
        if response.status_code >= 400:
            raise SearchProviderInvalidResponseError(f"Serper Search returned HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise SearchProviderInvalidResponseError("Serper Search returned non-JSON response") from exc
        if not isinstance(data, dict):
            raise SearchProviderInvalidResponseError("Serper Search response root is invalid")
        return data

    def _parse(self, payload: dict[str, Any]) -> list[SearchProviderResult]:
        raw_results = payload.get("organic", [])
        if raw_results is None:
            return []
        if not isinstance(raw_results, list):
            raise SearchProviderInvalidResponseError("Serper Search organic results section is invalid")

        parsed: list[SearchProviderResult] = []
        for fallback_rank, raw in enumerate(raw_results, start=1):
            if not isinstance(raw, dict):
                raise SearchProviderInvalidResponseError("Serper Search result item is invalid")
            url = raw.get("link")
            if not isinstance(url, str):
                continue
            try:
                canonicalize_discovery_url(url)
            except UrlRejected:
                continue
            rank = _rank(raw.get("position"), fallback_rank)
            parsed.append(
                SearchProviderResult(
                    rank=rank,
                    provider_result_id=_bounded_optional(url, 255),
                    url=url.strip()[:2048],
                    title=_bounded_text(raw.get("title"), MAX_TITLE_LENGTH),
                    snippet=_bounded_text(raw.get("snippet"), MAX_SNIPPET_LENGTH),
                    metadata={
                        "position": rank,
                    },
                )
            )
        return parsed


def _google_country(value: str) -> str | None:
    country = str(value or "").strip().lower()
    if COUNTRY_RE.fullmatch(country):
        return country
    return None


def _google_language(value: str) -> str | None:
    language = str(value or "").strip().lower()
    if language in SUPPORTED_GOOGLE_LANGUAGES:
        return language
    if LANGUAGE_RE.fullmatch(language) and language.split("-", 1)[0] in SUPPORTED_GOOGLE_LANGUAGES:
        return language
    return None


def _rank(value: Any, fallback: int) -> int:
    if isinstance(value, int) and value >= 1:
        return value
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 1 else fallback


def _bounded_text(value: Any, max_length: int) -> str:
    text = str(value or "").strip()
    return text[:max_length]


def _bounded_optional(value: Any, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_length] or None
