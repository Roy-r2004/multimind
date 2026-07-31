"""Thin client for the Google Places API (New) — Text Search only.

Uses ``places:searchText`` with a field mask that already includes website and
phone, so a single call per query is usually enough (no separate Place
Details round-trip for the fields this census needs).
"""

from __future__ import annotations

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
    ]
)


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

    @retry(
        retry=retry_if_exception_type((PlacesTimeoutError, PlacesUnavailableError)),
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        reraise=True,
    )
    async def _request(self, *, query: str, region_code: str, page_size: int) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self._api_key or "",
            "X-Goog-FieldMask": _FIELD_MASK,
        }
        body: dict[str, Any] = {"textQuery": query, "pageSize": page_size}
        region = (region_code or "").strip().lower()
        if len(region) == 2:
            body["regionCode"] = region

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
