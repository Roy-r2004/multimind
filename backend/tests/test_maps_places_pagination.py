"""Google Places pagination — proves page 2 and page 3 are requested."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import get_settings
from app.services.scraping.maps_places_client import GooglePlacesClient


def _place_payload(place_id: str, name: str) -> dict:
    return {
        "id": place_id,
        "displayName": {"text": name},
        "formattedAddress": "1 Rue Example, Paris, France",
        "location": {"latitude": 48.85, "longitude": 2.35},
        "types": ["health"],
    }


class _MockResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload


@pytest.mark.asyncio
async def test_search_text_paginated_requests_page_2_and_3_when_tokens_exist(monkeypatch):
    """When Google returns nextPageToken, the client must send pageToken on
    subsequent requests — page 2 and page 3 must both be fetched."""
    monkeypatch.setattr(get_settings(), "google_places_api_key", "test-key")
    request_bodies: list[dict] = []

    async def fake_post(_self, _url, *, headers=None, json=None, **kwargs):
        del headers, kwargs
        body = dict(json or {})
        request_bodies.append(body)
        token = body.get("pageToken")
        if not token:
            return _MockResponse(
                {
                    "places": [_place_payload("place-page-1", "Centre A")],
                    "nextPageToken": "token-for-page-2",
                }
            )
        if token == "token-for-page-2":
            return _MockResponse(
                {
                    "places": [_place_payload("place-page-2", "Centre B")],
                    "nextPageToken": "token-for-page-3",
                }
            )
        if token == "token-for-page-3":
            return _MockResponse({"places": [_place_payload("place-page-3", "Centre C")]})
        raise AssertionError(f"unexpected pageToken: {token!r}")

    with patch("httpx.AsyncClient.post", new=fake_post):
        client = GooglePlacesClient()
        outcome = await client.search_text_paginated(
            query="addiction rehabilitation Paris",
            region_code="FR",
            max_pages=3,
            page_size=20,
        )

    assert outcome.pages_fetched == 3
    assert outcome.unique_results_found == 3
    assert {p.google_place_id for p in outcome.places} == {
        "place-page-1",
        "place-page-2",
        "place-page-3",
    }
    assert len(request_bodies) == 3
    assert "pageToken" not in request_bodies[0]
    assert request_bodies[1]["pageToken"] == "token-for-page-2"
    assert request_bodies[2]["pageToken"] == "token-for-page-3"
    assert request_bodies[0]["pageSize"] == 20


@pytest.mark.asyncio
async def test_search_text_paginated_resumes_from_saved_token(monkeypatch):
    monkeypatch.setattr(get_settings(), "google_places_api_key", "test-key")
    request_bodies: list[dict] = []

    async def fake_post(_self, _url, *, headers=None, json=None, **kwargs):
        del headers, kwargs
        body = dict(json or {})
        request_bodies.append(body)
        token = body.get("pageToken")
        assert token == "saved-resume-token"
        return _MockResponse({"places": [_place_payload("place-resumed", "Resumed Centre")]})

    with patch("httpx.AsyncClient.post", new=fake_post):
        client = GooglePlacesClient()
        outcome = await client.search_text_paginated(
            query="rehab Lyon",
            region_code="FR",
            resume_page_token="saved-resume-token",
            max_pages=1,
        )

    assert outcome.pages_fetched == 1
    assert outcome.places[0].google_place_id == "place-resumed"
    assert request_bodies[0]["pageToken"] == "saved-resume-token"
