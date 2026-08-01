"""Thin client for the Google Places API (New) — Text Search only.

Uses ``places:searchText`` with a field mask that already includes website and
phone, so a single call per query is usually enough (no separate Place
Details round-trip for the fields this census needs). ``search_text_paginated``
additionally follows Google's ``nextPageToken`` up to a bounded number of
pages per cell, so a single dense query (e.g. "rehab clinic Paris") is not
silently capped at the first 20 raw results.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings

_FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.types",
        "places.internationalPhoneNumber",
        "places.websiteUri",
        "places.photos",
        "nextPageToken",
    ]
)

# Exceptions that are worth retrying (transient) vs. not (config/auth/4xx).
_RETRYABLE_EXCEPTIONS = None  # set below, after the exception classes exist


class PlacesProviderError(Exception):
    code = "places_provider_error"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class PlacesConfigurationError(PlacesProviderError):
    code = "configuration_missing"


class PlacesAuthError(PlacesProviderError):
    code = "authentication_failed"


class PlacesRateLimitedError(PlacesProviderError):
    code = "rate_limited"


class PlacesTimeoutError(PlacesProviderError):
    code = "request_timeout"


class PlacesUnavailableError(PlacesProviderError):
    code = "provider_unavailable"


class PlacesInvalidResponseError(PlacesProviderError):
    code = "invalid_response"


_RETRYABLE_EXCEPTIONS = (PlacesTimeoutError, PlacesUnavailableError, PlacesRateLimitedError)


@dataclass(frozen=True)
class PlaceResult:
    google_place_id: str
    raw_name: str
    formatted_address: str | None
    place_types: list[str] = field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    international_phone_number: str | None = None
    website: str | None = None
    photo_reference: str | None = None


@dataclass
class PlacesSearchOutcome:
    """Result of one ``search_text_paginated`` call, spanning 1..N pages."""

    places: list[PlaceResult] = field(default_factory=list)
    pages_fetched: int = 0
    raw_results_found: int = 0
    unique_results_found: int = 0
    duplicates_found: int = 0
    # True when Google returned a nextPageToken that this call did not
    # (yet) consume — either because the page cap was hit or the caller
    # cancelled mid-pagination.
    next_page_available: bool = False
    # True specifically when pagination stopped because the per-cell page
    # cap was reached while more pages were still available — the signal
    # capped-cell subdivision watches for.
    result_cap_reached: bool = False
    pagination_error: str | None = None
    resume_page_token: str | None = None


class GooglePlacesClient:
    name = "google_places"

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.google_places_api_key
        self._base_url = settings.google_places_base_url.rstrip("/")
        self._timeout = settings.google_places_timeout_seconds

    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def search_text(
        self,
        *,
        query: str,
        region_code: str,
        max_results: int = 20,
    ) -> list[PlaceResult]:
        """Legacy single-page search — kept for callers that only need the
        first page (e.g. quick lookups outside the census pipeline)."""
        if not self._api_key:
            raise PlacesConfigurationError("GOOGLE_PLACES_API_KEY is not configured")
        text = query.strip()
        if not text:
            return []
        payload = await self._request(
            query=text,
            region_code=region_code,
            page_size=min(max(max_results, 1), 20),
        )
        return self._parse(payload)

    async def search_text_paginated(
        self,
        *,
        query: str,
        region_code: str,
        page_size: int | None = None,
        max_pages: int | None = None,
        resume_page_token: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> PlacesSearchOutcome:
        """Search with Google Places pagination, following ``nextPageToken``
        up to ``max_pages`` (default: ``maps_census_max_pages_per_cell``).

        Each page is retried independently (exponential backoff + jitter) so
        a transient failure on page 2 does not discard page 1's results.
        ``cancel_check`` is polled between pages so a cancelled run can stop
        mid-pagination instead of finishing every page first.
        """
        if not self._api_key:
            raise PlacesConfigurationError("GOOGLE_PLACES_API_KEY is not configured")
        text = query.strip()
        if not text:
            return PlacesSearchOutcome()

        settings = get_settings()
        size = min(max(int(page_size or settings.maps_census_places_page_size), 1), 20)
        pages_cap = max(1, int(max_pages or settings.maps_census_max_pages_per_cell))

        seen_ids: set[str] = set()
        places: list[PlaceResult] = []
        raw_count = 0
        duplicates = 0
        pages_fetched = 0
        page_token = (resume_page_token or "").strip() or None
        pagination_error: str | None = None
        next_page_available = False

        while pages_fetched < pages_cap:
            if cancel_check is not None:
                try:
                    cancelled = cancel_check()
                except Exception:  # noqa: BLE001 — a broken cancel_check must never abort discovery
                    cancelled = False
                if cancelled:
                    next_page_available = bool(page_token)
                    break

            try:
                payload = await self._request_page_with_retry(
                    query=text,
                    region_code=region_code,
                    page_size=size,
                    page_token=page_token,
                )
            except PlacesProviderError as exc:
                pagination_error = str(exc)[:500]
                next_page_available = bool(page_token) and pages_fetched > 0
                break

            pages_fetched += 1
            page_places = self._parse(payload)
            raw_count += len(page_places)
            for place in page_places:
                if place.google_place_id in seen_ids:
                    duplicates += 1
                    continue
                seen_ids.add(place.google_place_id)
                places.append(place)

            raw_token = payload.get("nextPageToken")
            page_token = raw_token.strip() if isinstance(raw_token, str) and raw_token.strip() else None
            next_page_available = bool(page_token)
            if not next_page_available:
                break

        result_cap_reached = next_page_available and pages_fetched >= pages_cap

        return PlacesSearchOutcome(
            places=places,
            pages_fetched=pages_fetched,
            raw_results_found=raw_count,
            unique_results_found=len(places),
            duplicates_found=duplicates,
            next_page_available=next_page_available,
            result_cap_reached=result_cap_reached,
            pagination_error=pagination_error,
            resume_page_token=page_token if next_page_available else None,
        )

    async def _request_page_with_retry(
        self,
        *,
        query: str,
        region_code: str,
        page_size: int,
        page_token: str | None,
    ) -> dict[str, Any]:
        """Retry a single page independently with exponential backoff + jitter.

        Deliberately not a tenacity decorator on the whole paginated method:
        each page gets its own retry budget so a failure on a later page
        never discards already-fetched earlier pages.
        """
        settings = get_settings()
        max_attempts = max(1, int(settings.maps_census_places_page_max_attempts))
        attempt = 0
        last_exc: PlacesProviderError | None = None
        while attempt < max_attempts:
            try:
                return await self._do_request(
                    query=query,
                    region_code=region_code,
                    page_size=page_size,
                    page_token=page_token,
                )
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                attempt += 1
                if attempt >= max_attempts:
                    break
                backoff = min(2**attempt, 8) + random.uniform(0, 1)
                await asyncio.sleep(backoff)
        assert last_exc is not None
        raise last_exc

    @retry(
        retry=retry_if_exception_type((PlacesTimeoutError, PlacesUnavailableError)),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    async def _request(self, *, query: str, region_code: str, page_size: int) -> dict[str, Any]:
        return await self._do_request(query=query, region_code=region_code, page_size=page_size)

    async def _do_request(
        self,
        *,
        query: str,
        region_code: str,
        page_size: int,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key or "",
            "X-Goog-FieldMask": _FIELD_MASK,
        }
        body: dict[str, Any] = {"textQuery": query, "pageSize": page_size}
        region = (region_code or "").strip().lower()
        if len(region) == 2:
            body["regionCode"] = region
        if page_token:
            body["pageToken"] = page_token

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/places:searchText", headers=headers, json=body
                )
        except httpx.TimeoutException as exc:
            raise PlacesTimeoutError("Google Places request timed out") from exc
        except httpx.HTTPError as exc:
            raise PlacesUnavailableError("Google Places request failed") from exc

        if response.status_code in {401, 403}:
            raise PlacesAuthError("Google Places authentication failed")
        if response.status_code == 429:
            raise PlacesRateLimitedError("Google Places rate limit exceeded")
        if response.status_code >= 500:
            raise PlacesUnavailableError("Google Places is unavailable")
        if response.status_code >= 400:
            raise PlacesInvalidResponseError(f"Google Places returned HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise PlacesInvalidResponseError("Google Places returned non-JSON response") from exc
        if not isinstance(data, dict):
            raise PlacesInvalidResponseError("Google Places response root is invalid")
        return data

    def _parse(self, payload: dict[str, Any]) -> list[PlaceResult]:
        raw_places = payload.get("places", [])
        if raw_places is None:
            return []
        if not isinstance(raw_places, list):
            raise PlacesInvalidResponseError("Google Places 'places' section is invalid")

        results: list[PlaceResult] = []
        for raw in raw_places:
            if not isinstance(raw, dict):
                continue
            place_id = raw.get("id")
            if not isinstance(place_id, str) or not place_id.strip():
                continue
            display_name = raw.get("displayName") or {}
            name = display_name.get("text") if isinstance(display_name, dict) else None
            location = raw.get("location") or {}
            lat = location.get("latitude") if isinstance(location, dict) else None
            lng = location.get("longitude") if isinstance(location, dict) else None
            types = raw.get("types")
            results.append(
                PlaceResult(
                    google_place_id=place_id.strip(),
                    raw_name=str(name or "Unknown").strip()[:512],
                    formatted_address=_bounded_optional(raw.get("formattedAddress"), 512),
                    place_types=[str(t) for t in types][:20] if isinstance(types, list) else [],
                    latitude=float(lat) if isinstance(lat, (int, float)) else None,
                    longitude=float(lng) if isinstance(lng, (int, float)) else None,
                    international_phone_number=_bounded_optional(
                        raw.get("internationalPhoneNumber"), 64
                    ),
                    website=_bounded_optional(raw.get("websiteUri"), 512),
                    photo_reference=_first_photo_reference(raw.get("photos")),
                )
            )
        return results


def _bounded_optional(value: Any, max_length: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:max_length] or None


def _first_photo_reference(photos: Any) -> str | None:
    if not isinstance(photos, list) or not photos:
        return None
    first = photos[0]
    if not isinstance(first, dict):
        return None
    return _bounded_optional(first.get("name"), 300)


def create_places_client() -> GooglePlacesClient:
    return GooglePlacesClient()
