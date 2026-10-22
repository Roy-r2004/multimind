"""Unit tests for the dataset-backed boundary cache/geometry module.

All fixtures here are small, hand-written GeoJSON — no network calls, no
live geoBoundaries fetch. These tests only exercise reading an
already-cached file and the shapely-based geometry helpers.
"""

from __future__ import annotations

import json

import pytest

from app.core.config import get_settings
from app.services.scraping import maps_geo_boundaries as geo


def _write_cache_file(tmp_path, iso3: str, adm_level: str, geojson: dict) -> None:
    payload = {
        "iso3": iso3,
        "adm_level": adm_level,
        "source": "geoBoundaries gbOpen",
        "license": "Public Domain",
        "boundary_id": f"{iso3}-{adm_level}-TEST",
        "download_url": "https://raw.githubusercontent.com/example/example.geojson",
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "sha256": "0" * 64,
        "geojson": geojson,
    }
    path = tmp_path / f"{iso3}_{adm_level}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")


def _square_feature(name: str, shape_iso: str, minx, miny, maxx, maxy) -> dict:
    return {
        "type": "Feature",
        "properties": {"shapeName": name, "shapeISO": shape_iso, "shapeID": f"{shape_iso}-ID"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]
            ],
        },
    }


@pytest.fixture
def geo_cache_dir(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_geo_boundary_cache_dir", str(tmp_path))
    yield tmp_path


def test_load_cached_boundary_returns_none_when_missing(geo_cache_dir):
    assert geo.load_cached_boundary("ZZZ", "ADM0") is None


def test_load_cached_boundary_returns_none_on_corrupt_file(geo_cache_dir):
    path = geo_cache_dir / "ZZZ_ADM0.json"
    path.write_text("{not json", encoding="utf-8")
    assert geo.load_cached_boundary("ZZZ", "ADM0") is None


def test_resolve_region_boundary_country_only_reads_adm0(geo_cache_dir):
    geojson = {
        "type": "FeatureCollection",
        "features": [_square_feature("United States", "US", -125, 25, -66, 49)],
    }
    _write_cache_file(geo_cache_dir, "USA", "ADM0", geojson)

    record = geo.resolve_region_boundary(country_code="US", state_code=None, state_name=None)
    assert record is not None
    assert record.adm_level == "ADM0"


def test_resolve_region_boundary_unmapped_country_returns_none(geo_cache_dir):
    assert (
        geo.resolve_region_boundary(country_code="ZZ", state_code=None, state_name=None) is None
    )


def test_resolve_region_boundary_matches_state_by_shape_iso(geo_cache_dir):
    geojson = {
        "type": "FeatureCollection",
        "features": [
            _square_feature("Alabama", "US-AL", -88.5, 30.1, -84.9, 35.1),
            _square_feature("Michigan", "US-MI", -90.5, 41.6, -82.1, 48.3),
        ],
    }
    _write_cache_file(geo_cache_dir, "USA", "ADM1", geojson)

    record = geo.resolve_region_boundary(country_code="US", state_code="AL", state_name=None)
    assert record is not None
    assert len(record.geojson["features"]) == 1
    assert record.geojson["features"][0]["properties"]["shapeName"] == "Alabama"


def test_resolve_region_boundary_matches_state_by_name_when_no_code(geo_cache_dir):
    geojson = {
        "type": "FeatureCollection",
        "features": [_square_feature("Ontario", "CA-ON", -95.2, 41.7, -74.3, 56.9)],
    }
    _write_cache_file(geo_cache_dir, "CAN", "ADM1", geojson)

    record = geo.resolve_region_boundary(
        country_code="CA", state_code=None, state_name="Ontario"
    )
    assert record is not None
    assert record.geojson["features"][0]["properties"]["shapeName"] == "Ontario"


def test_resolve_region_boundary_rejects_conflicting_code_and_name(geo_cache_dir):
    # "AL" is Alabama's ISO 3166-2 code, but the name given is Alaska's —
    # a genuine, detectable conflict that must never be silently resolved
    # one way or the other.
    geojson = {
        "type": "FeatureCollection",
        "features": [
            _square_feature("Alabama", "US-AL", -88.5, 30.1, -84.9, 35.1),
            _square_feature("Alaska", "US-AK", -170.0, 51.0, -129.0, 71.5),
        ],
    }
    _write_cache_file(geo_cache_dir, "USA", "ADM1", geojson)

    record = geo.resolve_region_boundary(
        country_code="US", state_code="AL", state_name="Alaska"
    )
    assert record is None


def test_resolve_region_boundary_country_only_covers_more_than_hardcoded_list(geo_cache_dir):
    # Any ISO 3166-1 country should resolve via pycountry, not just a
    # hand-picked shortlist — exercised here with a country that was never
    # in the old hardcoded ISO2_TO_ISO3 table.
    geojson = {
        "type": "FeatureCollection",
        "features": [_square_feature("Japan", "JP", 123.0, 24.0, 146.0, 46.0)],
    }
    _write_cache_file(geo_cache_dir, "JPN", "ADM0", geojson)

    record = geo.resolve_region_boundary(country_code="JP", state_code=None, state_name=None)
    assert record is not None
    assert record.iso3 == "JPN"


def test_resolve_region_boundary_returns_none_for_unknown_state(geo_cache_dir):
    geojson = {
        "type": "FeatureCollection",
        "features": [_square_feature("Alabama", "US-AL", -88.5, 30.1, -84.9, 35.1)],
    }
    _write_cache_file(geo_cache_dir, "USA", "ADM1", geojson)

    record = geo.resolve_region_boundary(
        country_code="US", state_code="CA", state_name=None
    )
    assert record is None


def test_bounding_box_matches_simple_square(geo_cache_dir):
    geojson = {
        "type": "FeatureCollection",
        "features": [_square_feature("Alabama", "US-AL", -88.5, 30.1, -84.9, 35.1)],
    }
    _write_cache_file(geo_cache_dir, "USA", "ADM1", geojson)
    record = geo.resolve_region_boundary(country_code="US", state_code="AL", state_name=None)

    bbox = geo.bounding_box(record)
    assert bbox == pytest.approx(
        {"west": -88.5, "south": 30.1, "east": -84.9, "north": 35.1}
    )


def test_bounding_box_widens_antimeridian_crossing_geometry(geo_cache_dir):
    # A feature spanning from 170 to -170 longitude (crossing the
    # antimeridian) stored naively as a single polygon — shapely sees this
    # as a very wide span, which must be widened to the full range rather
    # than returned as a backwards box.
    geojson = {
        "type": "FeatureCollection",
        "features": [_square_feature("Fiji-like", "ZZ-FJ", 170, -20, -170, -15)],
    }
    _write_cache_file(geo_cache_dir, "ZZZ", "ADM0", geojson)
    record = geo.load_cached_boundary("ZZZ", "ADM0")

    bbox = geo.bounding_box(record)
    assert bbox["west"] == -180.0
    assert bbox["east"] == 180.0


def test_contains_point_true_inside_false_outside(geo_cache_dir):
    geojson = {
        "type": "FeatureCollection",
        "features": [_square_feature("Alabama", "US-AL", -88.5, 30.1, -84.9, 35.1)],
    }
    _write_cache_file(geo_cache_dir, "USA", "ADM1", geojson)
    record = geo.resolve_region_boundary(country_code="US", state_code="AL", state_name=None)

    assert geo.contains_point(record, lat=32.5, lon=-86.5) is True
    assert geo.contains_point(record, lat=42.0, lon=-83.0) is False


def test_polygon_cells_skips_empty_grid_squares(geo_cache_dir):
    # An L-shaped region (union of two squares) built from two features —
    # a naive bbox-based grid would include the empty corner; polygon_cells
    # must skip any grid cell that doesn't intersect either square.
    geojson = {
        "type": "FeatureCollection",
        "features": [
            _square_feature("part-a", "ZZ-A", 0, 0, 1, 2),
            _square_feature("part-b", "ZZ-B", 0, 0, 2, 1),
        ],
    }
    _write_cache_file(geo_cache_dir, "ZZZ", "ADM0", geojson)
    record = geo.load_cached_boundary("ZZZ", "ADM0")

    cells = geo.polygon_cells(record, cell_degrees=1.0)
    bboxes = {(round(c["west"]), round(c["south"])) for c in cells}
    assert (0, 0) in bboxes
    assert (0, 1) in bboxes
    assert (1, 0) in bboxes
    # (1, 1) is the empty corner of the L-shape and must be excluded.
    assert (1, 1) not in bboxes


class _FakeStreamResponse:
    """Minimal stand-in for the ``async with client.stream(...) as resp``
    object ``_bounded_get`` relies on: a status code, a headers mapping,
    ``raise_for_status``, and an async byte iterator."""

    def __init__(self, status_code: int, body: bytes = b"", headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_bytes(self, chunk_size: int = 1024):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class _FakeStreamClient:
    """Routes ``client.stream("GET", url)`` to a canned response by URL —
    lets tests drive the exact same redirect-following / size-capping /
    host-revalidation code path ``fetch_and_cache_boundary`` uses in
    production, without any real network access."""

    def __init__(self, responses: dict, *args, **kwargs):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def stream(self, method: str, url: str):
        return self._responses[url]


def _make_fake_client_factory(responses: dict):
    def factory(*args, **kwargs):
        return _FakeStreamClient(responses)

    return factory


def test_fetch_and_cache_boundary_refuses_untrusted_download_host(monkeypatch, tmp_path):
    """Defense in depth: even if geoBoundaries metadata were ever
    tampered with or pointed somewhere unexpected, the download host
    allowlist must reject it rather than fetch from an arbitrary host
    (SSRF guard)."""
    import asyncio
    import json as json_mod

    import httpx

    settings = get_settings()
    monkeypatch.setattr(settings, "maps_geo_boundary_cache_dir", str(tmp_path))

    api_url = "https://www.geoboundaries.org/api/current/gbOpen/USA/ADM0/"
    meta = json_mod.dumps({"simplifiedGeometryGeoJSON": "https://evil.example.com/x.json"}).encode()
    responses = {api_url: _FakeStreamResponse(200, meta)}
    monkeypatch.setattr(httpx, "AsyncClient", _make_fake_client_factory(responses))

    with pytest.raises(ValueError, match="untrusted host"):
        asyncio.run(geo.fetch_and_cache_boundary("USA", "ADM0"))


def test_fetch_and_cache_boundary_follows_trusted_redirect(monkeypatch, tmp_path):
    """geoBoundaries' GitHub-hosted downloads 302 in practice — a redirect
    between two allowlisted hosts must be followed, with the final host
    still re-validated against the allowlist."""
    import asyncio
    import json as json_mod

    import httpx

    settings = get_settings()
    monkeypatch.setattr(settings, "maps_geo_boundary_cache_dir", str(tmp_path))

    api_url = "https://www.geoboundaries.org/api/current/gbOpen/USA/ADM0/"
    redirect_url = "https://github.com/example/geo.json"
    final_url = "https://raw.githubusercontent.com/example/geo.json"
    geojson_body = json_mod.dumps(
        {
            "type": "FeatureCollection",
            "features": [_square_feature("United States", "US", -125, 25, -66, 49)],
        }
    ).encode()
    meta = json_mod.dumps(
        {
            "simplifiedGeometryGeoJSON": redirect_url,
            "boundaryLicense": "Public Domain",
            "boundaryID": "USA-ADM0-TEST",
        }
    ).encode()
    responses = {
        api_url: _FakeStreamResponse(200, meta),
        redirect_url: _FakeStreamResponse(302, b"", headers={"location": final_url}),
        final_url: _FakeStreamResponse(200, geojson_body),
    }
    monkeypatch.setattr(httpx, "AsyncClient", _make_fake_client_factory(responses))

    record = asyncio.run(geo.fetch_and_cache_boundary("USA", "ADM0"))
    assert record.download_url == redirect_url
    assert record.license == "Public Domain"
    assert geo.load_cached_boundary("USA", "ADM0") is not None


def test_fetch_and_cache_boundary_rejects_oversized_download(monkeypatch, tmp_path):
    """The size cap must be enforced while streaming, not only after a
    full buffer — an oversized response must abort, not get fully
    downloaded then rejected."""
    import asyncio
    import json as json_mod

    import httpx

    settings = get_settings()
    monkeypatch.setattr(settings, "maps_geo_boundary_cache_dir", str(tmp_path))
    monkeypatch.setattr(settings, "maps_geo_boundary_max_download_bytes", 100)

    api_url = "https://www.geoboundaries.org/api/current/gbOpen/USA/ADM0/"
    download_url = "https://raw.githubusercontent.com/example/geo.json"
    meta = json_mod.dumps({"simplifiedGeometryGeoJSON": download_url}).encode()
    oversized_body = b"x" * 500
    responses = {
        api_url: _FakeStreamResponse(200, meta),
        download_url: _FakeStreamResponse(200, oversized_body),
    }
    monkeypatch.setattr(httpx, "AsyncClient", _make_fake_client_factory(responses))

    with pytest.raises(ValueError, match="byte cap"):
        asyncio.run(geo.fetch_and_cache_boundary("USA", "ADM0"))
    assert geo.load_cached_boundary("USA", "ADM0") is None


def test_fetch_and_cache_boundary_rejects_corrupt_geometry(monkeypatch, tmp_path):
    """A downloaded file that parses as JSON but isn't usable GeoJSON must
    be rejected before it's ever written to the cache."""
    import asyncio
    import json as json_mod

    import httpx

    settings = get_settings()
    monkeypatch.setattr(settings, "maps_geo_boundary_cache_dir", str(tmp_path))

    api_url = "https://www.geoboundaries.org/api/current/gbOpen/USA/ADM0/"
    download_url = "https://raw.githubusercontent.com/example/geo.json"
    meta = json_mod.dumps({"simplifiedGeometryGeoJSON": download_url}).encode()
    corrupt_body = json_mod.dumps({"type": "NotAFeatureCollection"}).encode()
    responses = {
        api_url: _FakeStreamResponse(200, meta),
        download_url: _FakeStreamResponse(200, corrupt_body),
    }
    monkeypatch.setattr(httpx, "AsyncClient", _make_fake_client_factory(responses))

    with pytest.raises(ValueError, match="unexpected GeoJSON"):
        asyncio.run(geo.fetch_and_cache_boundary("USA", "ADM0"))
    assert geo.load_cached_boundary("USA", "ADM0") is None


def test_fetch_and_cache_boundary_never_overwrites_without_force(monkeypatch, tmp_path):
    _write_cache_file(
        tmp_path,
        "USA",
        "ADM0",
        {"type": "FeatureCollection", "features": [_square_feature("US", "US", -125, 25, -66, 49)]},
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_geo_boundary_cache_dir", str(tmp_path))

    import asyncio

    with pytest.raises(FileExistsError):
        asyncio.run(geo.fetch_and_cache_boundary("USA", "ADM0"))
