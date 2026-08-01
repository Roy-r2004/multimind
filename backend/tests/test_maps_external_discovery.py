"""Tests for Maps external directory discovery stubs (Phase 3)."""

from __future__ import annotations

import httpx
import pytest

from app.services.scraping.maps_external_discovery import (
    CsvDiscoverySource,
    ExternalDiscoveryContext,
    MapsExternalDiscoveryCoordinator,
    WebpageDiscoverySource,
)


@pytest.mark.asyncio
async def test_webpage_discovery_extracts_same_page_links(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_external_discovery_max_candidates_per_source", 10)

    html = """
    <html><body>
      <a href="https://directory.test/facility-a">A</a>
      <a href="/facility-b">B</a>
      <a href="javascript:void(0)">Skip</a>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    source = WebpageDiscoverySource(
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        )
    )
    candidates = await source.discover_candidates(
        ExternalDiscoveryContext(
            country_code="FR",
            country_name="France",
            source_url="https://directory.test/list",
            source_id="webpage",
        )
    )
    urls = {candidate.source_url for candidate in candidates}
    assert "https://directory.test/facility-a" in urls
    assert "https://directory.test/facility-b" in urls


@pytest.mark.asyncio
async def test_csv_discovery_parses_rows(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_external_discovery_max_candidates_per_source", 10)

    csv_text = "name,city,website\nCentre Alpha,Lyon,https://alpha.test\nCentre Beta,Paris,\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/csv"}, text=csv_text)

    source = CsvDiscoverySource(
        client_factory=lambda **kwargs: httpx.AsyncClient(
            transport=httpx.MockTransport(handler), **kwargs
        )
    )
    candidates = await source.discover_candidates(
        ExternalDiscoveryContext(
            country_code="FR",
            country_name="France",
            source_url="https://data.test/providers.csv",
            source_id="csv",
        )
    )
    assert len(candidates) == 2
    assert candidates[0].name == "Centre Alpha"
    assert candidates[0].website == "https://alpha.test"


@pytest.mark.asyncio
async def test_coordinator_reads_directory_hints_from_profile(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_external_discovery_enabled", True)
    monkeypatch.setattr(get_settings(), "maps_census_external_discovery_max_sources", 2)

    html = '<html><body><a href="https://directory.test/x">X</a></body></html>'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=html)

    coordinator = MapsExternalDiscoveryCoordinator(
        sources=[
            WebpageDiscoverySource(
                client_factory=lambda **kwargs: httpx.AsyncClient(
                    transport=httpx.MockTransport(handler), **kwargs
                )
            )
        ]
    )
    candidates = await coordinator.discover_from_profile(
        country_code="FR",
        country_name="France",
        profile={"directory_hints": ["https://directory.test/list"]},
    )
    assert len(candidates) == 1
    assert candidates[0].source_url == "https://directory.test/x"
