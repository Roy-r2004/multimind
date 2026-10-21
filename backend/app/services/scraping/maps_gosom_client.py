"""Maps Census discovery adapter for the self-hosted gosom Google Maps scraper.

Speaks the same ``search_text_paginated`` / ``PlaceResult`` contract as
``GooglePlacesClient``. One census query becomes one gosom REST job:

``POST /api/v1/jobs`` → poll ``GET /api/v1/jobs/{id}`` →
``GET /api/v1/jobs/{id}/download`` (CSV).

gosom's ``DELETE /api/v1/jobs/{id}`` removes the job row and CSV. It does not
document a hard abort of an in-flight browser, so cancellation stops waiting
and deletes the job record only.

Official ``place_id`` values (``ChI…``) are kept so they match Places records.
Anything else is stored as ``gosom:`` so it is never presented as a Place ID.
Photos are left empty: the CSV thumbnail is not a Places photo resource, and
the census photo route would otherwise call ``places.googleapis.com``.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.scraping.maps_places_client import (
    PlaceResult,
    PlacesConfigurationError,
    PlacesInvalidResponseError,
    PlacesProviderError,
    PlacesSearchOutcome,
    PlacesTimeoutError,
    PlacesUnavailableError,
    _bounded_optional,
)

_JOB_PREFIX = "gosom-job:"
_MIN_MAX_TIME_SECONDS = 180


class GosomPlacesClient:
    name = "gosom"

    def __init__(self) -> None:
        settings = get_settings()
        self._base_url = (settings.gosom_base_url or "").rstrip("/")
        self._timeout = settings.gosom_timeout_seconds

    def is_configured(self) -> bool:
        return bool(self._base_url)

    async def search_text(
        self,
        *,
        query: str,
        region_code: str,
        max_results: int = 20,
    ) -> list[PlaceResult]:
        outcome = await self.search_text_paginated(
            query=query,
            region_code=region_code,
            page_size=max_results,
            max_pages=1,
        )
        if outcome.pagination_error and not outcome.places:
            raise PlacesProviderError(outcome.pagination_error)
        return outcome.places

    async def search_text_paginated(
        self,
        *,
        query: str,
        region_code: str,
        page_size: int | None = None,
        max_pages: int | None = None,
        resume_page_token: str | None = None,
        cancel_check: Callable[[], bool] | None = None,
        heartbeat_cb: Callable[[], Awaitable[None]] | None = None,
        viewport_bounds: dict[str, Any] | None = None,
    ) -> PlacesSearchOutcome:
        if not self._base_url:
            raise PlacesConfigurationError("GOSOM_BASE_URL is not configured")
        text = query.strip()
        if not text:
            return PlacesSearchOutcome()

        settings = get_settings()
        size = max(int(page_size or settings.maps_census_places_page_size), 1)
        pages_cap = max(1, int(max_pages or settings.maps_census_max_pages_per_cell))
        depth = int(settings.gosom_depth) if int(settings.gosom_depth) > 0 else pages_cap
        budget = size * pages_cap
        geo = _center_and_zoom_from_viewport(viewport_bounds)

        if _cancelled(cancel_check):
            raise PlacesProviderError("gosom job cancelled")

        job_id = await self._resolve_job(
            query=text,
            region_code=region_code,
            depth=depth,
            resume_page_token=resume_page_token,
            geo=geo,
        )
        status = await self._poll_until_terminal(
            job_id,
            max_time_seconds=max(int(settings.gosom_job_max_time_seconds), _MIN_MAX_TIME_SECONDS),
            poll_interval=max(float(settings.gosom_poll_interval_seconds), 1.0),
            cancel_check=cancel_check,
            heartbeat_cb=heartbeat_cb,
        )
        if status == "cancelled":
            raise PlacesProviderError("gosom job cancelled")
        if status != "ok":
            await self._delete_job(job_id)
            raise PlacesUnavailableError(f"gosom job {job_id} finished with status {status}")

        places = await self._download_places(job_id)
        await self._delete_job(job_id)
        unique = _dedupe(places)
        cap_reached = len(unique) >= budget
        return PlacesSearchOutcome(
            places=unique,
            pages_fetched=1,
            raw_results_found=len(places),
            unique_results_found=len(unique),
            duplicates_found=len(places) - len(unique),
            next_page_available=cap_reached,
            result_cap_reached=cap_reached,
            resume_page_token=None,
        )

    async def _resolve_job(
        self,
        *,
        query: str,
        region_code: str,
        depth: int,
        resume_page_token: str | None,
        geo: tuple[str, str, int] | None,
    ) -> str:
        token = (resume_page_token or "").strip()
        if token.startswith(_JOB_PREFIX):
            job_id = token[len(_JOB_PREFIX) :].strip()
            if job_id:
                return job_id

        lat, lon, zoom = geo if geo is not None else ("", "", 15)
        # lat/lon/zoom must be part of the dedup/reuse key: two cells with the
        # same query text but different geography (e.g. quadrant-subdivision
        # siblings) are genuinely different searches and must never reuse
        # each other's job — see _center_and_zoom_from_viewport.
        name = _stable_job_name(query=query, region_code=region_code, depth=depth, lat=lat, lon=lon, zoom=zoom)
        existing = await self._find_reusable_job(name)
        if existing:
            return existing

        settings = get_settings()
        body = {
            "name": name,
            "keywords": [query],
            "lang": settings.gosom_language,
            "zoom": zoom,
            "lat": lat,
            "lon": lon,
            "fast_mode": False,
            # radius is only read by gosom in fast_mode (a different search
            # path with its own required params); in the mode we use here it
            # is accepted but has no effect. Left at a value matching the
            # single-request result budget for forward-compatibility, not
            # because it currently does anything.
            "radius": 10000,
            "depth": depth,
            "email": False,
            "extra_reviews": False,
            "max_time": max(int(settings.gosom_job_max_time_seconds), _MIN_MAX_TIME_SECONDS),
            "proxies": [],
        }
        payload = await self._request("POST", "/api/v1/jobs", json_body=body, ok_status=201)
        job_id = _job_id(payload)
        if not job_id:
            raise PlacesInvalidResponseError("gosom create-job response missing id")
        return job_id

    async def _find_reusable_job(self, name: str) -> str | None:
        payload = await self._request("GET", "/api/v1/jobs")
        jobs = payload if isinstance(payload, list) else []
        reusable: list[dict[str, Any]] = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            if str(job.get("Name") or job.get("name") or "") != name:
                continue
            status = str(job.get("Status") or job.get("status") or "").lower()
            if status in {"pending", "working", "ok"}:
                reusable.append(job)
        if not reusable:
            return None
        chosen = reusable[-1]
        return _job_id(chosen)

    async def _poll_until_terminal(
        self,
        job_id: str,
        *,
        max_time_seconds: int,
        poll_interval: float,
        cancel_check: Callable[[], bool] | None,
        heartbeat_cb: Callable[[], Awaitable[None]] | None = None,
    ) -> str:
        deadline = asyncio.get_running_loop().time() + max_time_seconds + 30
        while True:
            if _cancelled(cancel_check):
                await self._delete_job(job_id)
                return "cancelled"
            if heartbeat_cb is not None:
                # A single Gosom job can legitimately take up to
                # ~max_time_seconds (minutes) to finish. Without this, the
                # orchestrator's heartbeat only gets refreshed once per cell
                # (before this poll loop starts), so a long-but-healthy scrape
                # can look "stale" to the watchdog — this keeps it visibly
                # alive for the whole wait, not just at the start.
                await heartbeat_cb()
            payload = await self._request("GET", f"/api/v1/jobs/{job_id}")
            status = str(payload.get("Status") or payload.get("status") or "").lower()
            if status in {"ok", "failed"}:
                return status
            if status not in {"pending", "working", ""}:
                return status or "unknown"
            if asyncio.get_running_loop().time() >= deadline:
                await self._delete_job(job_id)
                raise PlacesTimeoutError(
                    f"gosom job {job_id} exceeded {max_time_seconds}s"
                )
            await asyncio.sleep(poll_interval)

    async def _download_places(self, job_id: str) -> list[PlaceResult]:
        url = f"{self._base_url}/api/v1/jobs/{job_id}/download"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code == 404:
                        raise PlacesInvalidResponseError(
                            f"gosom job {job_id} completed without a results file"
                        )
                    if response.status_code >= 500:
                        raise PlacesUnavailableError("gosom download failed")
                    if response.status_code >= 400:
                        raise PlacesInvalidResponseError(
                            f"gosom download returned HTTP {response.status_code}"
                        )
                    buffer = io.StringIO()
                    async for chunk in response.aiter_text():
                        buffer.write(chunk)
        except httpx.TimeoutException as exc:
            raise PlacesTimeoutError("gosom download timed out") from exc
        except httpx.HTTPError as exc:
            raise PlacesUnavailableError("gosom download failed") from exc

        buffer.seek(0)
        reader = csv.DictReader(buffer)
        places: list[PlaceResult] = []
        for row in reader:
            place = normalize_gosom_row(row)
            if place is not None:
                places.append(place)
        return places

    async def _delete_job(self, job_id: str) -> None:
        try:
            await self._request("DELETE", f"/api/v1/jobs/{job_id}", ok_status=200)
        except PlacesProviderError:
            return

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        ok_status: int = 200,
    ) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(
                    method, f"{self._base_url}{path}", json=json_body
                )
        except httpx.TimeoutException as exc:
            raise PlacesTimeoutError("gosom request timed out") from exc
        except httpx.HTTPError as exc:
            raise PlacesUnavailableError("gosom is unavailable") from exc

        if method == "DELETE" and response.status_code in {200, 204, 404}:
            return None
        if response.status_code == 404 and method == "GET":
            raise PlacesUnavailableError("gosom job was not found")
        if response.status_code >= 500:
            raise PlacesUnavailableError(f"gosom returned HTTP {response.status_code}")
        if response.status_code != ok_status:
            detail = response.text[:300]
            raise PlacesInvalidResponseError(
                f"gosom returned HTTP {response.status_code}: {detail}"
            )
        if method == "DELETE":
            return None
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise PlacesInvalidResponseError("gosom returned non-JSON response") from exc


def normalize_gosom_row(row: dict[str, str | None]) -> PlaceResult | None:
    identifier = discovery_identifier(row)
    title = (row.get("title") or "").strip()
    if not identifier or not title:
        return None
    category = (row.get("category") or "").strip()
    return PlaceResult(
        google_place_id=identifier[:255],
        raw_name=title[:512],
        formatted_address=_bounded_optional(row.get("address"), 512),
        place_types=[category][:20] if category else [],
        latitude=_float_or_none(row.get("latitude")),
        longitude=_float_or_none(row.get("longitude")),
        international_phone_number=_bounded_optional(row.get("phone"), 64),
        website=_bounded_optional(row.get("website"), 512),
        photo_reference=None,
        maps_url=_bounded_optional(row.get("link"), 512),
    )


def discovery_identifier(row: dict[str, str | None]) -> str | None:
    """Prefer a genuine Maps place id. Otherwise namespace the source key."""
    place_id = (row.get("place_id") or "").strip()
    if place_id.startswith("ChI") and len(place_id) >= 8:
        return place_id
    cid = (row.get("cid") or "").strip()
    if cid:
        return f"gosom:cid:{cid}"
    data_id = (row.get("data_id") or "").strip()
    if data_id:
        return f"gosom:data:{data_id}"
    link = (row.get("link") or "").strip()
    if link:
        digest = hashlib.sha256(link.encode("utf-8")).hexdigest()[:40]
        return f"gosom:link:{digest}"
    if place_id:
        return f"gosom:raw:{place_id}"
    return None


def _stable_job_name(
    *, query: str, region_code: str, depth: int, lat: str = "", lon: str = "", zoom: int = 15
) -> str:
    raw = f"{region_code.strip().lower()}|{depth}|{lat}|{lon}|{zoom}|{query.strip()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"mm-{digest}"


# Zoom levels are a Web Mercator convention: each step doubles the degrees of
# longitude visible on screen, so span_degrees ~= 360 / 2^zoom at the equator.
# This table is a deliberately coarse, monotonic approximation (not a precise
# projection — doing that right also needs latitude, which this call site
# doesn't have cheaply) used only to keep a smaller bounding box mapped to a
# tighter zoom than a larger one, which is what actually matters for distinct
# viewport_subdivision siblings to search visibly different areas.
_ZOOM_BY_MAX_SPAN_DEGREES: tuple[tuple[float, int], ...] = (
    (4.0, 7),
    (1.0, 9),
    (0.25, 11),
    (0.05, 13),
    (0.01, 15),
)
_DEFAULT_ZOOM_FOR_TINY_SPAN = 16


def _center_and_zoom_from_viewport(
    viewport_bounds: dict[str, Any] | None,
) -> tuple[str, str, int] | None:
    """Derive a Gosom ``lat``/``lon``/``zoom`` triple from a cell's bounding
    box, or ``None`` when no real bounds are available.

    Gosom (non-fast-mode) only supports geographic targeting as a map center
    + zoom — see ``gmaps.NewGmapJob`` in the gosom source, which builds
    ``.../maps/search/{query}/@{lat},{lon},{zoom}z}`` — there is no bounding
    box parameter, so this always collapses a box to its midpoint.
    """
    if not viewport_bounds:
        return None
    try:
        north = float(viewport_bounds["north"])
        south = float(viewport_bounds["south"])
        east = float(viewport_bounds["east"])
        west = float(viewport_bounds["west"])
    except (KeyError, TypeError, ValueError):
        return None
    lat = (north + south) / 2
    lon = (east + west) / 2
    span = max(abs(north - south), abs(east - west))
    zoom = _DEFAULT_ZOOM_FOR_TINY_SPAN
    for max_span, candidate_zoom in _ZOOM_BY_MAX_SPAN_DEGREES:
        if span >= max_span:
            zoom = candidate_zoom
            break
    return (f"{lat:.6f}", f"{lon:.6f}", zoom)


def _job_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("ID") or payload.get("id") or payload.get("Id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _dedupe(places: list[PlaceResult]) -> list[PlaceResult]:
    seen: set[str] = set()
    unique: list[PlaceResult] = []
    for place in places:
        if place.google_place_id in seen:
            continue
        seen.add(place.google_place_id)
        unique.append(place)
    return unique


def _cancelled(cancel_check: Callable[[], bool] | None) -> bool:
    if cancel_check is None:
        return False
    try:
        return bool(cancel_check())
    except Exception:  # noqa: BLE001 — a broken cancel check must not abort discovery
        return False
