"""Dataset-backed worldwide geographic boundary resolution (ADM0/ADM1).

Layered ON TOP of ``maps_geo_regions``'s static bounding-box table: that
module's ``resolve_geo_anchor_detailed`` tries this module's cached-dataset
resolution first, and only falls back to its own hand-curated boxes when no
cached boundary is available for the requested country/region. Neither
layer ever geocodes, invents, or AI-generates a boundary; this module only
*reads a local, operator-acquired cache* and does geometry on it (bbox,
point-in-polygon, grid-vs-polygon intersection) — it performs **no network
I/O during a census run**, only when explicitly asked to fetch via
``fetch_and_cache_boundary`` (called only from
``scripts/fetch_geo_boundaries.py``, a deliberate operator action).

Boundary data source: geoBoundaries gbOpen
(https://www.geoboundaries.org/api/current/gbOpen/{ISO3}/{ADM_LEVEL}/).
Confirmed reachable and Public Domain licensed for at least US ADM1 at the
time this module was written; each cached file carries its own `license`
and `boundary_id` copied verbatim from the API response rather than this
module assuming a blanket license. gbAuthoritative, any paid geocoding
API, Google Places, Nominatim, and Overpass are never contacted by this
module — see ALLOWED_DOWNLOAD_HOSTS below, the only hosts a geometry
download will ever be accepted from.

Cache layout (under settings.maps_geo_boundary_cache_dir, default
``backend/var/geo_boundaries/``, gitignored, never ``backend/data/``):
    {ISO3}_{ADM_LEVEL}.json — one JSON object with source/license/
    boundary_id/download_url/fetched_at/sha256/geojson fields.

This is a deliberately small vertical slice: the ISO2->ISO3 table and the
ADM1 name/code matching below cover a handful of countries this product
has been asked to support, not "every country in the world." Resolution
returns ``None`` for anything not covered — callers must treat that as "no
dataset-backed boundary available" and fall back further (the static
table, then text-only search), never guess.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GEOBOUNDARIES_API_HOST = "www.geoboundaries.org"
ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "raw.githubusercontent.com",
    "media.githubusercontent.com",
    "objects.githubusercontent.com",
}

AdmLevel = Literal["ADM0", "ADM1"]


def iso2_to_iso3(country_code: str) -> str | None:
    """Standard ISO 3166-1 alpha-2 -> alpha-3 lookup via ``pycountry``
    (already a project dependency) rather than a hand-maintained table —
    this is what actually lets "worldwide" resolution mean every
    ISO-recognized country, not just a handful this module used to
    special-case. Returns ``None`` for anything pycountry doesn't
    recognize (never a guess)."""
    import pycountry

    try:
        country = pycountry.countries.get(alpha_2=(country_code or "").strip().upper())
    except (LookupError, KeyError):
        return None
    return country.alpha_3 if country is not None else None


_iso2_to_iso3 = iso2_to_iso3


def _subdivision_by_code(iso2: str, code: str) -> Any:
    import pycountry

    try:
        return pycountry.subdivisions.get(code=f"{iso2}-{code}")
    except (LookupError, KeyError):
        return None


def _subdivision_by_name(iso2: str, name: str) -> Any:
    import pycountry

    try:
        candidates = pycountry.subdivisions.get(country_code=iso2)
    except (LookupError, KeyError):
        candidates = None
    if not candidates:
        return None
    name_cf = name.casefold()
    for sub in candidates:
        if sub.name.casefold() == name_cf:
            return sub
    return None


def _region_code_name_conflict(iso2: str, code: str, name: str) -> bool:
    """True only when ISO 3166-2 independently resolves the given code and
    name to two *different* subdivisions (e.g. state_code ``"AL"`` paired
    with state_name ``"Alaska"`` instead of ``"Alabama"``) — a genuine,
    detectable conflict between the two inputs, never a silent pick of
    one over the other. When either side isn't a recognized ISO 3166-2
    code/name at all, this conservatively returns ``False`` (not a
    detected conflict) rather than blocking a legitimate region that
    simply isn't in pycountry's table under that exact name.
    """
    by_code = _subdivision_by_code(iso2, code)
    by_name = _subdivision_by_name(iso2, name)
    if by_code is None or by_name is None:
        return False
    return by_code.code != by_name.code


@dataclass(frozen=True)
class BoundaryRecord:
    iso3: str
    adm_level: str
    source: str
    license: str
    boundary_id: str
    download_url: str
    fetched_at: str
    sha256: str
    geojson: dict[str, Any]


def cache_dir() -> Path:
    settings = get_settings()
    path = Path(settings.maps_geo_boundary_cache_dir)
    if not path.is_absolute():
        # app/services/scraping/maps_geo_boundaries.py -> backend/
        backend_root = Path(__file__).resolve().parents[3]
        path = backend_root / path
    return path


def cache_path(iso3: str, adm_level: AdmLevel) -> Path:
    return cache_dir() / f"{iso3.upper()}_{adm_level.upper()}.json"


def load_cached_boundary(iso3: str, adm_level: AdmLevel) -> BoundaryRecord | None:
    """Read a previously-acquired boundary from the local cache.

    Never performs network I/O. Returns ``None`` on any missing, corrupt,
    or unreadable cache file so callers fall back safely rather than raise
    mid-run.
    """
    path = cache_path(iso3, adm_level)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return BoundaryRecord(
            iso3=raw["iso3"],
            adm_level=raw["adm_level"],
            source=raw["source"],
            license=raw["license"],
            boundary_id=raw["boundary_id"],
            download_url=raw["download_url"],
            fetched_at=raw["fetched_at"],
            sha256=raw["sha256"],
            geojson=raw["geojson"],
        )
    except KeyError:
        logger.warning("geo_boundary_cache_malformed path=%s", path)
        return None


def resolve_region_boundary(
    *, country_code: str | None, state_code: str | None, state_name: str | None
) -> BoundaryRecord | None:
    """Dataset-backed resolution from the local cache only.

    Country-only (no state/province) resolves to the cached ADM0 record.
    Country + state/province resolves to the matching feature extracted
    from the cached ADM1 ``FeatureCollection`` (matched on ``shapeISO``
    first, falling back to a case-insensitive ``shapeName`` match), never
    a guess. Returns ``None`` whenever the ISO2->ISO3 mapping, the cache
    file, or the name/code match is missing — and ALSO when both a state
    code and a state name are supplied but ISO 3166-2 resolves them to two
    different subdivisions (e.g. code "AL" with name "Alaska"): that is
    treated as an unresolvable conflict, never silently decided one way.
    """
    iso2 = (country_code or "").strip().upper()
    iso3 = _iso2_to_iso3(iso2)
    if not iso3:
        return None

    code = (state_code or "").strip().upper()
    name = (state_name or "").strip()
    if not code and not name:
        return load_cached_boundary(iso3, "ADM0")

    if code and name and _region_code_name_conflict(iso2, code, name):
        logger.warning(
            "geo_boundary_ambiguous_region country=%s state_code=%s state_name=%s — "
            "code and name resolve to different ISO 3166-2 subdivisions, refusing to guess",
            iso2,
            code,
            name,
        )
        return None

    adm1 = load_cached_boundary(iso3, "ADM1")
    if adm1 is None:
        return None
    features = adm1.geojson.get("features") or []
    target_iso = f"{iso2}-{code}" if code else None
    name_cf = name.casefold()
    matched: dict[str, Any] | None = None
    for feature in features:
        props = feature.get("properties") or {}
        shape_iso = str(props.get("shapeISO") or "").upper()
        shape_name = str(props.get("shapeName") or "")
        if target_iso and shape_iso == target_iso:
            matched = feature
            break
        if name_cf and shape_name.casefold() == name_cf:
            matched = feature
            break
    if matched is None:
        return None

    props = matched.get("properties") or {}
    return BoundaryRecord(
        iso3=iso3,
        adm_level="ADM1",
        source=adm1.source,
        license=adm1.license,
        boundary_id=str(props.get("shapeID") or adm1.boundary_id),
        download_url=adm1.download_url,
        fetched_at=adm1.fetched_at,
        sha256=adm1.sha256,
        geojson={"type": "FeatureCollection", "features": [matched]},
    )


def _shape_from_geojson(geojson: dict[str, Any]):
    from shapely.geometry import shape
    from shapely.ops import unary_union

    features = geojson.get("features")
    if features:
        geoms = [shape(f["geometry"]) for f in features if f.get("geometry")]
    elif geojson.get("geometry"):
        geoms = [shape(geojson["geometry"])]
    else:
        geoms = [shape(geojson)]
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return None
    union = unary_union(geoms)
    if not union.is_valid:
        union = union.buffer(0)
    return union


def bounding_box(record: BoundaryRecord) -> dict[str, float] | None:
    """Standard (west/south/east/north) bbox of the verified polygon.

    A search convenience derived from real geometry — never a substitute
    for it. An abnormally wide raw longitude span (an antimeridian
    crossing, e.g. Fiji or Alaska's Aleutians) is widened to the full
    [-180, 180] range rather than returned as a backwards/inverted box;
    callers doing real cell generation for such a region should prefer
    ``polygon_cells``/``contains_point`` over this bbox alone.
    """
    geom = _shape_from_geojson(record.geojson)
    if geom is None:
        return None
    minx, miny, maxx, maxy = geom.bounds
    if maxx - minx > 180:
        minx, maxx = -180.0, 180.0
    return {"west": minx, "south": miny, "east": maxx, "north": maxy}


def contains_point(record: BoundaryRecord, *, lat: float, lon: float) -> bool:
    """True only if ``(lat, lon)`` falls inside (or on the boundary of) the
    verified polygon — used to validate discovery results against the real
    boundary rather than just its bounding rectangle (geographic result
    validation, not a substitute for Phase 2 eligibility rules)."""
    from shapely.geometry import Point

    geom = _shape_from_geojson(record.geojson)
    if geom is None:
        return False
    point = Point(lon, lat)
    return bool(geom.contains(point) or geom.touches(point))


def bbox_intersects_polygon(record: BoundaryRecord, bbox: dict[str, float]) -> bool:
    """True if ``bbox`` (north/south/east/west) has any real area overlap
    with the verified polygon — used to filter subdivision quadrants so a
    cell that lands entirely outside the selected region's real shape
    (e.g. in a neighboring state/country inside the bounding rectangle, or
    in the empty notch of an irregularly-shaped region) is never created.
    """
    geom = _shape_from_geojson(record.geojson)
    if geom is None:
        return True  # fail open: an unreadable/invalid cached geometry must
        # never block subdivision that worked fine before this feature existed.
    from shapely.geometry import box as shapely_box

    try:
        north = float(bbox["north"])
        south = float(bbox["south"])
        east = float(bbox["east"])
        west = float(bbox["west"])
    except (KeyError, TypeError, ValueError):
        return True
    candidate = shapely_box(west, south, east, north)
    return geom.intersection(candidate).area > 0


def polygon_cells(record: BoundaryRecord, *, cell_degrees: float) -> list[dict[str, float]]:
    """Grid cells (as bboxes) covering the verified polygon, skipping any
    grid square that does not intersect the real geometry at all.

    This prevents wasting search budget on empty ocean or a neighboring
    country/state inside the bounding rectangle, and keeps every disjoint
    component (islands) covered since each is tested independently against
    the real (multi)polygon rather than a single bbox. Used by
    ``maps_census_service._persist_cells`` to distribute distinct
    polygon-filtered sub-boxes across region-wide seed cells instead of
    repeating one full bounding rectangle for every one of them.
    """
    geom = _shape_from_geojson(record.geojson)
    if geom is None or cell_degrees <= 0:
        return []
    from shapely.geometry import box as shapely_box

    minx, miny, maxx, maxy = geom.bounds
    cells: list[dict[str, float]] = []
    lat = miny
    while lat < maxy:
        next_lat = min(lat + cell_degrees, maxy)
        lon = minx
        while lon < maxx:
            next_lon = min(lon + cell_degrees, maxx)
            candidate = shapely_box(lon, lat, next_lon, next_lat)
            # Require actual area overlap, not just a shared edge/corner —
            # a cell that only touches the polygon's boundary covers zero
            # real area and would otherwise still waste search budget.
            if geom.intersection(candidate).area > 0:
                cells.append({"west": lon, "south": lat, "east": next_lon, "north": next_lat})
            lon = next_lon
        lat = next_lat
    return cells


async def _bounded_get(
    client: Any,
    url: str,
    *,
    allowed_hosts: set[str],
    max_bytes: int,
    max_redirects: int = 5,
) -> bytes:
    """Fetch ``url`` with the host/scheme allowlist re-checked on every hop
    (so a redirect can never escape to an untrusted host — SSRF guard) and
    the response size capped while streaming, not after a full download
    (so an oversized response is aborted mid-transfer rather than fully
    buffered first).
    """
    from urllib.parse import urljoin, urlparse

    current_url = url
    for _ in range(max_redirects + 1):
        parsed = urlparse(current_url)
        if parsed.scheme != "https" or (parsed.hostname or "") not in allowed_hosts:
            raise ValueError(f"refusing to fetch from untrusted host/scheme: {current_url}")
        async with client.stream("GET", current_url) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    resp.raise_for_status()
                current_url = urljoin(current_url, location)
                continue
            resp.raise_for_status()
            chunks = bytearray()
            async for chunk in resp.aiter_bytes():
                chunks.extend(chunk)
                if len(chunks) > max_bytes:
                    raise ValueError(
                        f"download from {current_url} exceeded the {max_bytes}-byte cap"
                    )
            return bytes(chunks)
    raise ValueError(f"too many redirects fetching {url}")


def _validate_geojson_or_raise(geojson: Any, *, iso3: str, adm_level: str) -> None:
    if not isinstance(geojson, dict):
        raise ValueError(f"boundary geometry for {iso3}/{adm_level} is not a JSON object")
    if geojson.get("type") not in ("FeatureCollection", "Feature"):
        raise ValueError(
            f"boundary geometry for {iso3}/{adm_level} has unexpected GeoJSON "
            f"type {geojson.get('type')!r}"
        )
    geom = _shape_from_geojson(geojson)
    if geom is None or geom.is_empty:
        raise ValueError(f"boundary geometry for {iso3}/{adm_level} contains no usable geometry")


async def fetch_and_cache_boundary(
    iso3: str, adm_level: AdmLevel, *, force: bool = False
) -> BoundaryRecord:
    """Deliberately fetch one country/level's boundary from geoBoundaries
    gbOpen and cache it locally.

    Never called from the request/run path — only from
    ``scripts/fetch_geo_boundaries.py``, an explicit operator action.
    Raises on any network, host-allowlist, size-cap, or validation failure
    rather than silently writing a partial/corrupt cache entry. Follows
    redirects (geoBoundaries' own GitHub-hosted geometry downloads 302 in
    practice) but re-validates the host allowlist on every hop and streams
    with the size cap enforced throughout, not just on the final buffer.
    """
    import httpx

    from urllib.parse import urlparse

    settings = get_settings()
    path = cache_path(iso3, adm_level)
    if path.exists() and not force:
        raise FileExistsError(f"{path} already cached; pass force=True to re-fetch")

    timeout = settings.maps_geo_boundary_fetch_timeout_seconds
    max_bytes = settings.maps_geo_boundary_max_download_bytes

    api_url = f"https://{GEOBOUNDARIES_API_HOST}/api/current/gbOpen/{iso3.upper()}/{adm_level.upper()}/"
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        meta_bytes = await _bounded_get(
            client, api_url, allowed_hosts={GEOBOUNDARIES_API_HOST}, max_bytes=max_bytes
        )
        meta = json.loads(meta_bytes)

        download_url = meta.get("simplifiedGeometryGeoJSON") or meta.get("gjDownloadURL")
        if not download_url:
            raise ValueError(f"geoBoundaries metadata for {iso3}/{adm_level} has no download URL")
        parsed = urlparse(download_url)
        if parsed.scheme != "https" or (parsed.hostname or "") not in ALLOWED_DOWNLOAD_HOSTS:
            raise ValueError(
                f"refusing to fetch boundary data from untrusted host: {parsed.hostname}"
            )

        content = await _bounded_get(
            client, download_url, allowed_hosts=ALLOWED_DOWNLOAD_HOSTS, max_bytes=max_bytes
        )
        geojson = json.loads(content)
        _validate_geojson_or_raise(geojson, iso3=iso3, adm_level=adm_level)

    record = BoundaryRecord(
        iso3=iso3.upper(),
        adm_level=adm_level.upper(),
        source="geoBoundaries gbOpen",
        license=str(meta.get("boundaryLicense") or "unknown"),
        boundary_id=str(meta.get("boundaryID") or ""),
        download_url=download_url,
        fetched_at=datetime.now(UTC).isoformat(),
        sha256=hashlib.sha256(content).hexdigest(),
        geojson=geojson,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "iso3": record.iso3,
                "adm_level": record.adm_level,
                "source": record.source,
                "license": record.license,
                "boundary_id": record.boundary_id,
                "download_url": record.download_url,
                "fetched_at": record.fetched_at,
                "sha256": record.sha256,
                "geojson": record.geojson,
            }
        ),
        encoding="utf-8",
    )
    return record
