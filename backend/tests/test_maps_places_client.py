"""Unit tests for the Google Places (New) response parser."""

from __future__ import annotations

from app.services.scraping.maps_places_client import GooglePlacesClient, PlacesInvalidResponseError
import pytest


def _client() -> GooglePlacesClient:
    return GooglePlacesClient()


def test_parse_extracts_core_fields():
    client = _client()
    payload = {
        "places": [
            {
                "id": "place-123",
                "displayName": {"text": "Centre Alpha Rehab"},
                "formattedAddress": "1 Main St, Minsk, Belarus",
                "location": {"latitude": 53.9, "longitude": 27.5667},
                "types": ["health", "point_of_interest"],
                "internationalPhoneNumber": "+375 17 123 4567",
                "websiteUri": "https://centre-alpha.by/",
                "photos": [{"name": "places/place-123/photos/photo-abc"}],
            }
        ]
    }
    results = client._parse(payload)
    assert len(results) == 1
    place = results[0]
    assert place.google_place_id == "place-123"
    assert place.raw_name == "Centre Alpha Rehab"
    assert place.latitude == 53.9
    assert place.longitude == 27.5667
    assert place.website == "https://centre-alpha.by/"
    assert place.place_types == ["health", "point_of_interest"]
    assert place.photo_reference == "places/place-123/photos/photo-abc"


def test_parse_handles_missing_photos():
    client = _client()
    payload = {"places": [{"id": "place-no-photo", "displayName": {"text": "No Photo Clinic"}}]}
    results = client._parse(payload)
    assert len(results) == 1
    assert results[0].photo_reference is None


def test_parse_handles_malformed_photos_field():
    client = _client()
    payload = {
        "places": [
            {"id": "p1", "displayName": {"text": "A"}, "photos": "not-a-list"},
            {"id": "p2", "displayName": {"text": "B"}, "photos": [{"no_name_key": "x"}]},
            {"id": "p3", "displayName": {"text": "C"}, "photos": []},
        ]
    }
    results = client._parse(payload)
    assert [r.photo_reference for r in results] == [None, None, None]


def test_parse_skips_places_missing_id():
    client = _client()
    payload = {"places": [{"displayName": {"text": "No id"}}]}
    assert client._parse(payload) == []


def test_parse_handles_empty_places_list():
    client = _client()
    assert client._parse({"places": []}) == []
    assert client._parse({}) == []


def test_parse_rejects_non_list_places():
    client = _client()
    with pytest.raises(PlacesInvalidResponseError):
        client._parse({"places": "not-a-list"})
