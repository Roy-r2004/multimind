"""Serper organic search provider implementation for source discovery."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.services.scraping.search_providers.base import (
    SearchProviderAuthError,
    SearchProviderConfigurationError,
    SearchProviderInvalidRequestError,
    SearchProviderInvalidResponseError,
    SearchProviderNetworkError,
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

ClientFactory = Callable[..., httpx.AsyncClient]


class SerperSearchProvider:
    name = "serper"

    def __init__(self, *, client_factory: ClientFactory | None = None) -> None:
        settings = get_settings()
        self._api_key = settings.serper_api_key
        self._base_url = settings.serper_search_base_url
        self._timeout = settings.serper_search_timeout_seconds
        self._default_limit = settings.serper_search_results_per_query
        self._client_factory: ClientFactory = client_factory or httpx.AsyncClient

    async def search(self, request: SearchProviderRequest) -> list[SearchProviderResult]:
        payload = await self.fetch_payload(request)
        return self._parse(payload)

    async def fetch_payload(self, request: SearchProviderRequest) -> dict[str, Any]:
        """Perform the Serper HTTP call and return the decoded JSON object.

        Legacy ``search()`` continues to parse and URL-filter this payload.
        Phase 4 v2 executors parse without canonicalization/persistence.
        """
        if not self._api_key:
            raise SearchProviderConfigurationError("SERPER_API_KEY is not configured")
        query = request.query.strip()
        if not query:
            raise SearchProviderInvalidRequestError("Search query cannot be blank")

        count = min(max(request.result_limit or self._default_limit, 1), MAX_RESULT_LIMIT)
        page = _page_number(request.page)
        return await self._request(
            query=query,
            country=request.country_code,
            language=request.search_language,
            count=count,
            page=page,
        )

    @retry(
        retry=retry_if_exception_type((SearchProviderTimeoutError, SearchProviderUnavailableError)),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    async def _request(
        self,
        *,
        query: str,
        country: str,
        language: str,
        count: int,
        page: int,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "X-API-KEY": self._api_key or "",
        }
        body: dict[str, Any] = {
            "q": query,
            "num": count,
            "page": page,
        }
        gl = _google_country(country)
        if gl:
            body["gl"] = gl
        hl = _google_language(language)
        if hl:
            body["hl"] = hl

        try:
            async with self._client_factory(timeout=self._timeout) as client:
                response = await client.post(self._base_url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise SearchProviderTimeoutError("Serper Search request timed out") from exc
        except httpx.RequestError as exc:
            raise SearchProviderNetworkError("Serper Search network request failed") from exc
        except httpx.HTTPError as exc:
            raise SearchProviderUnavailableError("Serper Search request failed") from exc

        if response.status_code in {401, 403}:
            raise SearchProviderAuthError(
                "Serper Search authentication failed",
                http_status=response.status_code,
            )
        if response.status_code == 429:
            raise SearchProviderRateLimitedError(
                "Serper Search rate or credit limit exceeded",
                http_status=429,
                retry_after_seconds=_parse_retry_after(response.headers.get("Retry-After")),
            )
        if response.status_code >= 500:
            raise SearchProviderUnavailableError(
                "Serper Search is unavailable",
                http_status=response.status_code,
            )
        if response.status_code in {400, 422}:
            raise SearchProviderInvalidRequestError(
                f"Serper Search rejected request with HTTP {response.status_code}",
                http_status=response.status_code,
            )
        if response.status_code >= 400:
            raise SearchProviderInvalidResponseError(
                f"Serper Search returned HTTP {response.status_code}",
                http_status=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise SearchProviderInvalidResponseError(
                "Serper Search returned non-JSON response",
                http_status=response.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise SearchProviderInvalidResponseError(
                "Serper Search response root is invalid",
                http_status=response.status_code,
            )
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


def _page_number(value: int | None) -> int:
    """Serper uses a 1-indexed ``page`` body field. Default first page."""
    if value is None:
        return 1
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


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


def _parse_retry_after(value: str | None) -> float | None:
    """Parse HTTP Retry-After as delay-seconds. Ignore HTTP-date forms safely."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        return None
    if seconds < 0 or seconds != seconds or seconds == float("inf"):  # NaN/inf
        return None
    return seconds
