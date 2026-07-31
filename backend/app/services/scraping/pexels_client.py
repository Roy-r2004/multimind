"""Thin client for the Pexels API — used only to fetch a representative country
landscape photo for the Maps Census hero background.

Deliberately non-fatal: every failure mode (missing key, timeout, bad
response) returns ``None`` instead of raising, so a Pexels outage never
blocks or fails a census run. The frontend falls back to a gradient
background when no hero image URL is available.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.pexels.com/v1/search"


class PexelsClient:
    name = "pexels"

    def __init__(self) -> None:
        settings = get_settings()
        self._api_key = settings.pexels_api_key
        self._timeout = settings.pexels_search_timeout_seconds

    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def search_landscape(self, query: str) -> str | None:
        text = query.strip()
        if not self._api_key or not text:
            return None
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    _SEARCH_URL,
                    headers={"Authorization": self._api_key},
                    params={"query": text, "orientation": "landscape", "per_page": 1},
                )
        except httpx.HTTPError:
            logger.warning("pexels_request_failed query=%s", text, exc_info=True)
            return None

        if response.status_code != 200:
            logger.warning(
                "pexels_request_non_200 query=%s status=%s", text, response.status_code
            )
            return None

        try:
            data = response.json()
        except ValueError:
            return None
        return _first_photo_url(data)


def _first_photo_url(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    photos = data.get("photos")
    if not isinstance(photos, list) or not photos:
        return None
    first = photos[0]
    if not isinstance(first, dict):
        return None
    src = first.get("src")
    if not isinstance(src, dict):
        return None
    for key in ("large2x", "large", "landscape", "original"):
        url = src.get(key)
        if isinstance(url, str) and url.strip():
            return url.strip()[:1024]
    return None


def create_pexels_client() -> PexelsClient:
    return PexelsClient()
