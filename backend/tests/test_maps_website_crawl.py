"""Tests for Maps website crawl service (Phase 3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db.models import MapsWebsiteCrawlCache
from app.services.scraping.maps_website_crawl_service import (
    MAX_PARSE_INPUT_CHARS,
    MapsWebsiteCrawlService,
    _extract_text,
    _extract_title,
    _parse_page_content,
    path_keywords_from_country_profile,
)


async def _async_resolve(addresses: list[str]) -> list[str]:
    return addresses


def test_extract_text_caps_input_and_survives_malformed_markup():
    # A page far larger than the parse cap must be truncated before regex/parsing
    # so one pathological document cannot pin the CPU (regression: clinique-tabet.com
    # froze the worker because parsing ran unbounded on the event loop).
    giant = "<p>real content</p>" + ("<div>" * (MAX_PARSE_INPUT_CHARS))
    text = _extract_text(giant, max_chars=500)
    assert len(text) <= 500
    assert "real content" in text


def test_extract_text_redos_bait_finishes_quickly():
    # Old SCRIPT_STYLE_RE with many unclosed <script...> tags could ReDoS and pin
    # the GIL even inside asyncio.to_thread, freezing the whole worker.
    import time

    evil = (("<script" + ("a" * 30) + ">") * 800) + "<p>visible centre</p>"
    started = time.perf_counter()
    text = _extract_text(evil, max_chars=200)
    elapsed = time.perf_counter() - started
    assert elapsed < 1.0, f"parser took {elapsed:.2f}s on ReDoS bait"
    assert "visible centre" in text


def test_extract_title_bounds_input():
    padding = "x" * (MAX_PARSE_INPUT_CHARS + 5000)
    html = f"<html><head><title>Centre</title></head><body>{padding}</body></html>"
    assert _extract_title(html) == "Centre"


def test_parse_page_content_returns_title_and_text():
    html = "<html><head><title>Clinic</title></head><body><p>Treatment center</p></body></html>"
    title, text = _parse_page_content(html, 200)
    assert title == "Clinic"
    assert "Treatment center" in text


def _html(title: str, body: str, links: list[str] | None = None) -> str:
    link_tags = "".join(f'<a href="{href}">{href}</a>' for href in (links or []))
    return f"<html><head><title>{title}</title></head><body>{body}{link_tags}</body></html>"


@pytest.mark.asyncio
async def test_crawl_fetches_homepage_and_keyword_paths(db, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_website_crawl_max_pages_per_domain", 3)

    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=_html(
                    "Home",
                    "Addiction treatment centre",
                    links=["/about-us", "https://other.test/ignore"],
                ),
            )
        if request.url.path == "/about-us":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text=_html("About", "Residential rehab and detox programs"),
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    service = MapsWebsiteCrawlService(
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
        resolver=lambda _host: _async_resolve(["93.184.216.34"]),
    )

    outcome = await service.crawl_website(
        db,
        website_url="https://example.test/",
        path_keywords=["about"],
    )

    assert outcome.cache_hit is False
    assert outcome.normalized_domain == "example.test"
    assert len(outcome.pages) >= 2
    assert any("Residential rehab" in page.text_excerpt for page in outcome.pages)
    assert "https://example.test/about-us" in requests

    cached = (
        await db.execute(
            select(MapsWebsiteCrawlCache).where(
                MapsWebsiteCrawlCache.normalized_domain == "example.test"
            )
        )
    ).scalar_one()
    assert len(cached.pages) >= 2


@pytest.mark.asyncio
async def test_crawl_uses_cache_on_second_request(db, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_website_crawl_max_pages_per_domain", 1)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["count"] += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=_html("Home", "Cached page body"),
        )

    transport = httpx.MockTransport(handler)
    service = MapsWebsiteCrawlService(
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
        resolver=lambda _host: _async_resolve(["93.184.216.34"]),
    )

    first = await service.crawl_website(db, website_url="https://cache.test/")
    second = await service.crawl_website(db, website_url="https://cache.test/")

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_crawl_respects_max_pages_per_domain(db, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_website_crawl_max_pages_per_domain", 2)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=_html(
                request.url.path,
                f"Body for {request.url.path}",
                links=[
                    "/about",
                    "/services",
                    "/programs",
                ],
            ),
        )

    transport = httpx.MockTransport(handler)
    service = MapsWebsiteCrawlService(
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
        resolver=lambda _host: _async_resolve(["93.184.216.34"]),
    )

    outcome = await service.crawl_website(
        db,
        website_url="https://limit.test/",
        path_keywords=["about", "services", "programs"],
    )
    assert len(outcome.pages) == 2


def test_path_keywords_from_country_profile_merges_explicit_and_provider_terms():
    profile = {
        "website_path_keywords": ["soins"],
        "provider_terms": {
            "residential": ["centre de desintoxication"],
            "outpatient": ["consultation addiction"],
        },
    }
    keywords = path_keywords_from_country_profile(profile)
    assert "soins" in keywords
    assert "centre de desintoxication" in keywords


@pytest.mark.asyncio
async def test_expired_cache_is_refreshed(db, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "maps_census_website_crawl_max_pages_per_domain", 1)
    now = datetime.now(UTC)
    db.add(
        MapsWebsiteCrawlCache(
            normalized_domain="stale.test",
            pages=[{"url": "https://stale.test/", "title": "Old", "text_excerpt": "old", "http_status": 200}],
            fetched_at=now - timedelta(days=10),
            expires_at=now - timedelta(hours=1),
        )
    )
    await db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=_html("Fresh", "Fresh crawl body"),
        )

    transport = httpx.MockTransport(handler)
    service = MapsWebsiteCrawlService(
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
        resolver=lambda _host: _async_resolve(["93.184.216.34"]),
    )
    outcome = await service.crawl_website(db, website_url="https://stale.test/")
    assert outcome.cache_hit is False
    assert any("Fresh crawl body" in page.text_excerpt for page in outcome.pages)
