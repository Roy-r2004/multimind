"""Tests for maps_sonar_citation_verification — never trust a Sonar
citation blindly; verify it through the existing bounded crawler and
confirm it's actually about the right facility (including non-Latin
names and phone numbers) before any evidence from it is used."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.scraping.maps_sonar_citation_verification import (
    verify_citation_refers_to_place,
)
from app.services.scraping.maps_website_crawl_service import (
    CrawledPage,
    MapsWebsiteCrawlError,
    WebsiteCrawlOutcome,
    maps_website_crawl_service,
)


def _place(**kwargs):
    base = dict(
        id="p1",
        canonical_name="Доктор Сайков",
        raw_name="Доктор Сайков",
        international_phone_number="+375 29 123-45-67",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_rejects_non_http_url(db):
    assert await verify_citation_refers_to_place(db, place=_place(), source_url="") is False
    assert (
        await verify_citation_refers_to_place(db, place=_place(), source_url="not-a-url")
        is False
    )


@pytest.mark.asyncio
async def test_confirms_via_transliterated_cyrillic_name(db, monkeypatch):
    """The facility name is Cyrillic; the crawled page is the (Latin-script)
    page content that actually mentions it in translit — confirms this
    reuses the same Cyrillic-aware folding as the website-relationship
    fix, not a Latin-only check."""

    async def fake_crawl(_db, *, website_url, **_kwargs):
        return WebsiteCrawlOutcome(
            normalized_domain="saikov.by",
            pages=[
                CrawledPage(
                    url=website_url,
                    title="Doktor Saikov — narcology clinic",
                    text_excerpt="Private clinic Doktor Saikov offers addiction treatment.",
                    http_status=200,
                )
            ],
            page_urls=[website_url],
            cache_hit=False,
        )

    monkeypatch.setattr(maps_website_crawl_service, "crawl_website", fake_crawl)

    result = await verify_citation_refers_to_place(
        db, place=_place(), source_url="https://saikov.by/about"
    )
    assert result is True


@pytest.mark.asyncio
async def test_confirms_via_phone_number_when_name_absent(db, monkeypatch):
    async def fake_crawl(_db, *, website_url, **_kwargs):
        return WebsiteCrawlOutcome(
            normalized_domain="example.by",
            pages=[
                CrawledPage(
                    url=website_url,
                    title="Contact",
                    text_excerpt="Call us: +375 29 123-45-67 for an appointment.",
                    http_status=200,
                )
            ],
            page_urls=[website_url],
            cache_hit=False,
        )

    monkeypatch.setattr(maps_website_crawl_service, "crawl_website", fake_crawl)

    result = await verify_citation_refers_to_place(
        db, place=_place(canonical_name="Unrelated Branding"), source_url="https://example.by/contact"
    )
    assert result is True


@pytest.mark.asyncio
async def test_rejects_when_content_does_not_mention_the_facility(db, monkeypatch):
    async def fake_crawl(_db, *, website_url, **_kwargs):
        return WebsiteCrawlOutcome(
            normalized_domain="unrelated.example",
            pages=[
                CrawledPage(
                    url=website_url,
                    title="A completely different clinic",
                    text_excerpt="This page is about Some Other Clinic entirely.",
                    http_status=200,
                )
            ],
            page_urls=[website_url],
            cache_hit=False,
        )

    monkeypatch.setattr(maps_website_crawl_service, "crawl_website", fake_crawl)

    result = await verify_citation_refers_to_place(
        db, place=_place(), source_url="https://unrelated.example/"
    )
    assert result is False


@pytest.mark.asyncio
async def test_rejects_when_citation_is_unreachable(db, monkeypatch):
    async def fake_crawl(_db, *, website_url, **_kwargs):
        raise MapsWebsiteCrawlError("connection refused")

    monkeypatch.setattr(maps_website_crawl_service, "crawl_website", fake_crawl)

    result = await verify_citation_refers_to_place(
        db, place=_place(), source_url="https://dead-domain.example/"
    )
    assert result is False


@pytest.mark.asyncio
async def test_rejects_on_crawl_error_field_without_raising(db, monkeypatch):
    async def fake_crawl(_db, *, website_url, **_kwargs):
        return WebsiteCrawlOutcome(
            normalized_domain="blocked.example",
            pages=[],
            page_urls=[],
            cache_hit=False,
            error="blocked_ip",
        )

    monkeypatch.setattr(maps_website_crawl_service, "crawl_website", fake_crawl)

    result = await verify_citation_refers_to_place(
        db, place=_place(), source_url="https://blocked.example/"
    )
    assert result is False


@pytest.mark.asyncio
async def test_never_raises_on_unexpected_exception(db, monkeypatch):
    async def fake_crawl(_db, *, website_url, **_kwargs):
        raise ValueError("unexpected")

    monkeypatch.setattr(maps_website_crawl_service, "crawl_website", fake_crawl)

    result = await verify_citation_refers_to_place(
        db, place=_place(), source_url="https://example.com/"
    )
    assert result is False
