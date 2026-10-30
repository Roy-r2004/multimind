"""Gosom discovery adapter. These tests are mocks only — do not run a scrape."""

from __future__ import annotations

import pytest

from app.core.config import Settings, get_settings
from app.services.scraping.maps_gosom_client import (
    GosomPlacesClient,
    _center_and_zoom_from_viewport,
    discovery_identifier,
    normalize_gosom_row,
)
from app.services.scraping.maps_places_client import (
    GooglePlacesClient,
    PlacesConfigurationError,
    PlacesUnavailableError,
    create_places_client,
)


def _enable_gosom(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "maps_discovery_provider", "gosom")
    monkeypatch.setattr(settings, "gosom_base_url", "http://gosom:8080")
    monkeypatch.setattr(settings, "gosom_language", "en")
    monkeypatch.setattr(settings, "gosom_depth", 0)
    monkeypatch.setattr(settings, "gosom_job_max_time_seconds", 180)
    monkeypatch.setattr(settings, "gosom_poll_interval_seconds", 1)
    monkeypatch.setattr(settings, "maps_census_places_page_size", 20)
    monkeypatch.setattr(settings, "maps_census_max_pages_per_cell", 3)


def test_provider_selection(monkeypatch):
    monkeypatch.setattr(get_settings(), "maps_discovery_provider", "google_places")
    assert isinstance(create_places_client(), GooglePlacesClient)
    monkeypatch.setattr(get_settings(), "maps_discovery_provider", "gosom")
    monkeypatch.setattr(get_settings(), "gosom_base_url", "http://gosom:8080")
    assert isinstance(create_places_client(), GosomPlacesClient)


def test_full_coverage_prompt_is_broader_than_the_places_prompt():
    from app.services.scraping.maps_grid_planner import _planner_system_prompt

    bounded = _planner_system_prompt(full_coverage=False)
    full = _planner_system_prompt(full_coverage=True)
    assert "15-20 queries max" in bounded
    assert "15-20" not in full
    assert "smaller towns" in full
    assert "local language" in full


def test_coverage_mode_rejects_unknown_values():
    with pytest.raises(ValueError, match="MAPS_CENSUS_COVERAGE_MODE"):
        Settings(maps_census_coverage_mode="unlimited")


def test_unsupported_provider_rejected():
    with pytest.raises(ValueError, match="MAPS_DISCOVERY_PROVIDER"):
        Settings(maps_discovery_provider="serpapi")


def test_genuine_place_id_kept_and_other_ids_namespaced():
    assert discovery_identifier({"place_id": "ChIJabc12345"}) == "ChIJabc12345"
    assert discovery_identifier({"place_id": "", "cid": "9988"}) == "gosom:cid:9988"
    assert discovery_identifier({"data_id": "0xabc"}) == "gosom:data:0xabc"
    assert discovery_identifier({"place_id": "0xnotofficial"}) == "gosom:raw:0xnotofficial"
    assert discovery_identifier({"title": "No id"}) is None


def test_normalize_missing_fields_and_no_invented_photo():
    place = normalize_gosom_row(
        {
            "title": "Rehab House",
            "place_id": "ChIJrehabhouse01",
            "address": "1 Main St",
            "category": "Rehabilitation center",
            "latitude": "48.85",
            "longitude": "2.35",
            "phone": "+33 1 23 45 67 89",
            "website": "https://example.org/rehab",
            "thumbnail": "https://lh3.googleusercontent.com/photo",
        }
    )
    assert place is not None
    assert place.raw_name == "Rehab House"
    assert place.formatted_address == "1 Main St"
    assert place.latitude == 48.85
    assert place.longitude == 2.35
    assert place.international_phone_number == "+33 1 23 45 67 89"
    assert place.website == "https://example.org/rehab"
    assert place.place_types == ["Rehabilitation center"]
    assert place.photo_reference is None

    empty = normalize_gosom_row({"title": "Nameless id only", "place_id": ""})
    assert empty is None
    blank_coords = normalize_gosom_row(
        {"title": "No geo", "cid": "1", "latitude": "", "longitude": "", "phone": "", "website": ""}
    )
    assert blank_coords is not None
    assert blank_coords.latitude is None
    assert blank_coords.longitude is None
    assert blank_coords.website is None
    assert blank_coords.international_phone_number is None


class _Response:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = b"{}" if payload is not None else b""

    def json(self):
        return self._payload


class _StreamResponse:
    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def aiter_text(self):
        yield self._body


class _FakeClient:
    def __init__(self, calls: list, script: list) -> None:
        self.calls = calls
        self.script = script

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def request(self, method, url, json=None):
        self.calls.append((method, url, json))
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    def stream(self, method, url):
        self.calls.append((method, url, None))
        return self.script.pop(0)


def _install_http(monkeypatch, calls: list, script: list) -> None:
    import httpx

    def factory(*_args, **_kwargs):
        return _FakeClient(calls, script)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


@pytest.mark.asyncio
async def test_job_translation_success_dedup_and_depth(monkeypatch):
    _enable_gosom(monkeypatch)
    calls: list = []
    csv_body = (
        "title,address,website,phone,latitude,longitude,cid,place_id,category,data_id,link\n"
        "Alpha,1 St,https://a.example,+1 555,40.0,-70.0,111,ChIJalpha0001,Rehab,,\n"
        "Alpha again,1 St,https://a.example,+1 555,40.0,-70.0,111,ChIJalpha0001,Rehab,,\n"
        "Beta,2 St,,,,,222,,,0xdata,\n"
    )
    script = [
        _Response(200, []),
        _Response(201, {"ID": "job-1"}),
        _Response(200, {"ID": "job-1", "Status": "working"}),
        _Response(200, {"ID": "job-1", "Status": "ok"}),
        _StreamResponse(200, csv_body),
        _Response(200, None),
    ]
    _install_http(monkeypatch, calls, script)
    monkeypatch.setattr("app.services.scraping.maps_gosom_client.asyncio.sleep", _async_noop)

    outcome = await GosomPlacesClient().search_text_paginated(
        query="rehab Paris",
        region_code="FR",
        page_size=20,
        max_pages=3,
    )
    assert [place.google_place_id for place in outcome.places] == [
        "ChIJalpha0001",
        "gosom:cid:222",
    ]
    assert outcome.duplicates_found == 1
    assert outcome.pages_fetched == 1
    assert outcome.result_cap_reached is False
    post = next(call for call in calls if call[0] == "POST")
    assert post[1] == "http://gosom:8080/api/v1/jobs"
    assert post[2]["keywords"] == ["rehab Paris"]
    assert post[2]["depth"] == 3
    assert post[2]["email"] is False
    assert post[2]["extra_reviews"] is False
    assert post[2]["fast_mode"] is False
    # No viewport_bounds given: falls back to the old no-geo-bias behavior.
    assert post[2]["lat"] == ""
    assert post[2]["lon"] == ""
    assert post[2]["zoom"] == 15
    assert all("places.googleapis.com" not in call[1] for call in calls)


def test_center_and_zoom_from_viewport_is_monotonic_and_centered():
    """Pure-function unit test for the bbox -> (lat, lon, zoom) mapping that
    makes Gosom's real @lat,lon,zoom targeting (confirmed from gosom v1.18.1
    source, gmaps/job.go) actually distinguish narrower boxes from wider
    ones."""
    assert _center_and_zoom_from_viewport(None) is None
    assert _center_and_zoom_from_viewport({}) is None
    assert _center_and_zoom_from_viewport({"north": 1, "south": 0}) is None  # missing east/west

    wide = _center_and_zoom_from_viewport({"north": 49.0, "south": 45.0, "east": 3.0, "west": -1.0})
    narrow = _center_and_zoom_from_viewport({"north": 48.86, "south": 48.84, "east": 2.36, "west": 2.34})
    assert wide is not None and narrow is not None
    wide_lat, wide_lon, wide_zoom = wide
    narrow_lat, narrow_lon, narrow_zoom = narrow
    assert wide_lat == "47.000000"
    assert wide_lon == "1.000000"
    assert narrow_lat == "48.850000"
    assert narrow_lon == "2.350000"
    # The whole point: a smaller box must get a tighter (higher) zoom than a
    # bigger one, so two differently-sized boxes are visibly different
    # searches to Gosom, not the same view at two different labels.
    assert narrow_zoom > wide_zoom


@pytest.mark.asyncio
async def test_distinct_viewport_bounds_produce_distinct_jobs(monkeypatch):
    """Two cells with the identical query text but different quadrant
    viewport_bounds (exactly the viewport_subdivision case) must never
    collide on the same Gosom job — each gets its own lat/lon/zoom and its
    own job name, so neither can silently reuse the other's (geographically
    different) results."""
    _enable_gosom(monkeypatch)
    quadrant_a = {"north": 48.86, "south": 48.85, "east": 2.36, "west": 2.35}
    quadrant_b = {"north": 48.85, "south": 48.84, "east": 2.35, "west": 2.34}

    calls_a: list = []
    script_a = [
        _Response(200, []),
        _Response(201, {"ID": "job-a"}),
        _Response(200, {"Status": "failed"}),
        _Response(200, None),
    ]
    _install_http(monkeypatch, calls_a, script_a)
    with pytest.raises(PlacesUnavailableError):
        await GosomPlacesClient().search_text_paginated(
            query="rehab paris", region_code="FR", viewport_bounds=quadrant_a
        )

    calls_b: list = []
    script_b = [
        _Response(200, []),
        _Response(201, {"ID": "job-b"}),
        _Response(200, {"Status": "failed"}),
        _Response(200, None),
    ]
    _install_http(monkeypatch, calls_b, script_b)
    with pytest.raises(PlacesUnavailableError):
        await GosomPlacesClient().search_text_paginated(
            query="rehab paris", region_code="FR", viewport_bounds=quadrant_b
        )

    post_a = next(call for call in calls_a if call[0] == "POST")
    post_b = next(call for call in calls_b if call[0] == "POST")
    assert post_a[2]["lat"] != post_b[2]["lat"]
    assert post_a[2]["name"] != post_b[2]["name"]


@pytest.mark.asyncio
async def test_heartbeat_cb_called_during_poll(monkeypatch):
    """A single Gosom job can legitimately take minutes to finish — the
    orchestrator's heartbeat_cb must be invoked on every poll tick so a
    long-but-healthy scrape never looks dead to the stale-run watchdog."""
    _enable_gosom(monkeypatch)
    calls: list = []
    script = [
        _Response(200, []),
        _Response(201, {"ID": "job-slow"}),
        _Response(200, {"Status": "working"}),
        _Response(200, {"Status": "working"}),
        _Response(200, {"Status": "ok", "ID": "job-slow"}),
        _StreamResponse(200, "title,place_id\nA,ChIJaaaa0001\n"),
        _Response(200, None),
    ]
    _install_http(monkeypatch, calls, script)
    monkeypatch.setattr("app.services.scraping.maps_gosom_client.asyncio.sleep", _async_noop)

    heartbeats = {"count": 0}

    async def _heartbeat_cb() -> None:
        heartbeats["count"] += 1

    outcome = await GosomPlacesClient().search_text_paginated(
        query="rehab paris", region_code="FR", heartbeat_cb=_heartbeat_cb
    )
    assert outcome.places[0].google_place_id == "ChIJaaaa0001"
    assert heartbeats["count"] >= 3  # once per poll tick (working, working, ok)


@pytest.mark.asyncio
async def test_failed_job_is_not_an_empty_success(monkeypatch):
    _enable_gosom(monkeypatch)
    calls: list = []
    script = [
        _Response(200, [{"ID": "job-9", "Name": "ignored", "Status": "failed"}]),
        _Response(201, {"id": "job-new"}),
        _Response(200, {"Status": "failed"}),
        _Response(200, None),
    ]
    _install_http(monkeypatch, calls, script)
    with pytest.raises(PlacesUnavailableError, match="failed"):
        await GosomPlacesClient().search_text_paginated(query="rehab Lyon", region_code="FR")


@pytest.mark.asyncio
async def test_timeout_does_not_return_empty_places(monkeypatch):
    _enable_gosom(monkeypatch)
    calls: list = []
    script = [
        _Response(200, []),
        _Response(201, {"ID": "job-slow"}),
        _Response(200, {"Status": "working"}),
        _Response(200, None),
    ]
    _install_http(monkeypatch, calls, script)

    times = iter([0.0, 10_000.0])

    class _Loop:
        def time(self):
            return next(times)

    monkeypatch.setattr(
        "app.services.scraping.maps_gosom_client.asyncio.get_running_loop", lambda: _Loop()
    )
    monkeypatch.setattr("app.services.scraping.maps_gosom_client.asyncio.sleep", _async_noop)
    from app.services.scraping.maps_places_client import PlacesTimeoutError

    with pytest.raises(PlacesTimeoutError):
        await GosomPlacesClient().search_text_paginated(query="rehab Nice", region_code="FR")
    assert calls[-1][0] == "DELETE"


@pytest.mark.asyncio
async def test_cancel_deletes_job(monkeypatch):
    _enable_gosom(monkeypatch)
    calls: list = []
    script = [
        _Response(200, []),
        _Response(201, {"ID": "job-cancel"}),
        _Response(200, None),
    ]
    _install_http(monkeypatch, calls, script)
    state = {"checks": 0}

    def _cancel_after_submit() -> bool:
        state["checks"] += 1
        return state["checks"] > 1

    with pytest.raises(Exception, match="cancelled"):
        await GosomPlacesClient().search_text_paginated(
            query="rehab Nantes",
            region_code="FR",
            cancel_check=_cancel_after_submit,
        )
    assert any(call[0] == "DELETE" for call in calls)


@pytest.mark.asyncio
async def test_resume_token_reuses_job_without_creating_another(monkeypatch):
    _enable_gosom(monkeypatch)
    calls: list = []
    csv_body = "title,place_id,address,category,latitude,longitude,phone,website\nKept,ChIJkept00001,,,,,\n"
    script = [
        _Response(200, {"Status": "ok", "ID": "job-existing"}),
        _StreamResponse(200, csv_body),
        _Response(200, None),
    ]
    _install_http(monkeypatch, calls, script)
    outcome = await GosomPlacesClient().search_text_paginated(
        query="rehab Bordeaux",
        region_code="FR",
        resume_page_token="gosom-job:job-existing",
    )
    assert outcome.places[0].google_place_id == "ChIJkept00001"
    assert not any(call[0] == "POST" for call in calls)


@pytest.mark.asyncio
async def test_gosom_unconfigured():
    client = GosomPlacesClient()
    client._base_url = ""
    with pytest.raises(PlacesConfigurationError):
        await client.search_text_paginated(query="rehab", region_code="FR")


@pytest.mark.asyncio
async def test_abandoned_job_is_never_reused_by_name(monkeypatch):
    """Root-cause regression: gosom's own dispatcher can leave a job's
    Status stuck at "working" forever even after its scrapemate run
    genuinely finished (confirmed in production logs for job
    a0c1d7f4-1550-46d2-8ce7-c09fc8167567, Belarus run
    5acff6f7-21f8-4427-88a1-38d0a394ce83: "scrapemate exited" with a place
    found, but no terminal job status ever followed — matches
    github.com/gosom/google-maps-scraper#328). _find_reusable_job's
    reuse-by-name logic must never resurrect that exact job for a retry —
    polling it again just repeats the same 480s timeout forever instead of
    making a fresh attempt."""
    _enable_gosom(monkeypatch)
    client = GosomPlacesClient()
    client._abandoned_job_ids.add("job-zombie")

    calls: list = []
    script = [
        _Response(200, [{"ID": "job-zombie", "Name": "mm-zombie", "Status": "working"}]),
    ]
    _install_http(monkeypatch, calls, script)

    reused = await client._find_reusable_job("mm-zombie")
    assert reused is None


@pytest.mark.asyncio
async def test_retry_after_timeout_creates_a_fresh_job_not_the_zombie(monkeypatch):
    """End-to-end version of the same regression: a cell retried with the
    identical query (same deterministic job name) after its first job
    timed out must create a brand-new gosom job rather than reattaching to
    the just-abandoned one, even though gosom's own job list still lists
    the zombie under the same name with a non-terminal status."""
    from app.services.scraping.maps_gosom_client import _stable_job_name

    _enable_gosom(monkeypatch)
    client = GosomPlacesClient()
    name = _stable_job_name(query="rehab Minsk", region_code="BY", depth=3)

    calls: list = []
    script = [
        _Response(200, []),  # no reusable job yet
        _Response(201, {"ID": "job-zombie"}),
        _Response(200, {"Status": "working"}),  # never reaches a terminal status
        _Response(200, None),  # DELETE on timeout
    ]
    _install_http(monkeypatch, calls, script)
    times = iter([0.0, 10_000.0])

    class _Loop:
        def time(self):
            return next(times)

    monkeypatch.setattr(
        "app.services.scraping.maps_gosom_client.asyncio.get_running_loop", lambda: _Loop()
    )
    monkeypatch.setattr("app.services.scraping.maps_gosom_client.asyncio.sleep", _async_noop)
    from app.services.scraping.maps_places_client import PlacesTimeoutError

    with pytest.raises(PlacesTimeoutError):
        await client.search_text_paginated(query="rehab Minsk", region_code="BY")
    assert client._abandoned_job_ids == {"job-zombie"}

    # Restore the real clock for the retry call below — it must complete
    # normally, not hit the same (exhausted) fake deadline iterator.
    monkeypatch.undo()
    _enable_gosom(monkeypatch)
    monkeypatch.setattr("app.services.scraping.maps_gosom_client.asyncio.sleep", _async_noop)

    # Retry with the identical query: gosom's job list still shows the
    # zombie (its dispatcher never actually marked it terminal), but it
    # must be skipped in favor of creating a fresh job.
    calls_retry: list = []
    script_retry = [
        _Response(200, [{"ID": "job-zombie", "Name": name, "Status": "working"}]),
        _Response(201, {"ID": "job-fresh"}),
        _Response(200, {"Status": "ok", "ID": "job-fresh"}),
        _StreamResponse(200, "title,place_id\nFresh,ChIJfresh0001\n"),
        _Response(200, None),  # DELETE on success
    ]
    _install_http(monkeypatch, calls_retry, script_retry)

    outcome = await client.search_text_paginated(query="rehab Minsk", region_code="BY")
    assert outcome.places[0].google_place_id == "ChIJfresh0001"
    posts = [call for call in calls_retry if call[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][2]["name"] == name


async def _async_noop(*_args, **_kwargs):
    return None
