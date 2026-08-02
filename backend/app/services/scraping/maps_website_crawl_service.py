"""Phase 3: limited official-site crawl for Maps Census enrichment.

Fetches a bounded number of same-domain pages (homepage plus keyword-matched paths),
caches results by normalized domain, and returns text excerpts for the enricher.
Uses the same SSRF/robots guardrails as Scraping Council source retrieval, but
does not depend on scraping execution models.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import MapsWebsiteCrawlCache

DEFAULT_PATH_KEYWORDS: tuple[str, ...] = (
    "about",
    "service",
    "program",
    "programme",
    "treatment",
    "addiction",
    "rehab",
    "detox",
    "therapy",
    "team",
    "contact",
    "mission",
    "centre",
    "center",
    "soin",
    "traitement",
    "dependance",
)

LOCALHOST_HOSTS = {"localhost", "localhost.localdomain"}
METADATA_HOSTS = {"metadata", "metadata.google.internal"}
METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
}
LINK_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
TAG_RE = re.compile(r"(?s)<[^>]+>")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        return " ".join(self._chunks)


@dataclass(frozen=True)
class CrawledPage:
    url: str
    title: str
    text_excerpt: str
    http_status: int


@dataclass(frozen=True)
class WebsiteCrawlOutcome:
    normalized_domain: str
    pages: list[CrawledPage]
    page_urls: list[str]
    cache_hit: bool
    error: str | None = None

    def combined_excerpt(self, *, max_chars: int) -> str:
        parts: list[str] = []
        remaining = max_chars
        for page in self.pages:
            block = f"URL: {page.url}\nTitle: {page.title}\n{page.text_excerpt}".strip()
            if not block:
                continue
            if len(block) > remaining:
                parts.append(block[:remaining])
                break
            parts.append(block)
            remaining -= len(block) + 2
        return "\n\n".join(parts).strip()


class MapsWebsiteCrawlError(Exception):
    pass


class MapsWebsiteCrawlService:
    def __init__(
        self,
        *,
        client_factory: Callable[..., Any] | None = None,
        resolver: Callable[[str], Awaitable[list[str]]] | None = None,
    ) -> None:
        self._client_factory = client_factory or httpx.AsyncClient
        self._resolver = resolver

    async def crawl_website(
        self,
        db: AsyncSession,
        *,
        website_url: str,
        path_keywords: list[str] | None = None,
        force_refresh: bool = False,
    ) -> WebsiteCrawlOutcome:
        settings = get_settings()
        if not settings.maps_census_website_crawl_enabled:
            return WebsiteCrawlOutcome(
                normalized_domain=_normalize_domain(website_url) or "",
                pages=[],
                page_urls=[],
                cache_hit=False,
                error="crawl_disabled",
            )

        validated = await self._validate_url(website_url)
        domain = validated.host
        if not domain:
            raise MapsWebsiteCrawlError("website URL has no host")

        if not force_refresh:
            cached = await self._load_valid_cache(db, domain)
            if cached is not None:
                pages = [_page_from_cache(item) for item in cached.pages]
                return WebsiteCrawlOutcome(
                    normalized_domain=domain,
                    pages=pages,
                    page_urls=[page.url for page in pages],
                    cache_hit=True,
                )

        keywords = _normalize_keywords(path_keywords)
        pages = await self._fetch_domain_pages(validated.url, domain=domain, path_keywords=keywords)
        await self._store_cache(db, domain=domain, pages=pages)
        return WebsiteCrawlOutcome(
            normalized_domain=domain,
            pages=pages,
            page_urls=[page.url for page in pages],
            cache_hit=False,
        )

    async def _load_valid_cache(
        self, db: AsyncSession, domain: str
    ) -> MapsWebsiteCrawlCache | None:
        now = datetime.now(UTC)
        row = (
            await db.execute(
                select(MapsWebsiteCrawlCache).where(
                    MapsWebsiteCrawlCache.normalized_domain == domain,
                    MapsWebsiteCrawlCache.expires_at > now,
                )
            )
        ).scalar_one_or_none()
        return row

    async def _store_cache(
        self,
        db: AsyncSession,
        *,
        domain: str,
        pages: list[CrawledPage],
    ) -> None:
        settings = get_settings()
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=max(1, settings.maps_census_website_crawl_cache_ttl_hours))
        payload = [
            {
                "url": page.url,
                "title": page.title,
                "text_excerpt": page.text_excerpt,
                "http_status": page.http_status,
            }
            for page in pages
        ]
        existing = (
            await db.execute(
                select(MapsWebsiteCrawlCache).where(
                    MapsWebsiteCrawlCache.normalized_domain == domain
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db.add(
                MapsWebsiteCrawlCache(
                    normalized_domain=domain,
                    pages=payload,
                    fetched_at=now,
                    expires_at=expires_at,
                )
            )
        else:
            existing.pages = payload
            existing.fetched_at = now
            existing.expires_at = expires_at
        await db.commit()

    async def _fetch_domain_pages(
        self,
        start_url: str,
        *,
        domain: str,
        path_keywords: list[str],
    ) -> list[CrawledPage]:
        settings = get_settings()
        max_pages = max(
            1,
            min(
                settings.maps_census_website_crawl_max_pages_per_domain,
                settings.maps_crawl_max_relevant_pages,
            ),
        )
        max_bytes = max(1024, settings.maps_census_website_crawl_max_bytes_per_page)
        max_excerpt = max(
            500,
            min(
                settings.maps_census_website_crawl_max_excerpt_chars,
                settings.maps_crawl_max_chars_per_page,
            ),
        )
        timeout = httpx.Timeout(
            settings.maps_census_website_crawl_timeout_seconds,
            connect=settings.maps_census_website_crawl_connect_timeout_seconds,
        )
        headers = {"User-Agent": settings.maps_census_website_crawl_user_agent}

        async with self._client_factory(timeout=timeout, follow_redirects=False) as client:
            await self._assert_robots_allowed(client, start_url, headers=headers)
            homepage = await self._fetch_page(client, start_url, headers=headers, max_bytes=max_bytes)
            candidate_urls = await asyncio.to_thread(
                _discover_same_domain_links,
                homepage.final_url,
                html=homepage.content,
                domain=domain,
                path_keywords=path_keywords,
                max_candidates=max_pages * 4,
            )
            queue = [url for url in candidate_urls if url.split("#", 1)[0] != homepage.final_url.split("#", 1)[0]]

            homepage_title, homepage_text = await asyncio.to_thread(
                _parse_page_content, homepage.content, max_excerpt
            )
            pages: list[CrawledPage] = [
                CrawledPage(
                    url=homepage.final_url,
                    title=homepage_title,
                    text_excerpt=homepage_text,
                    http_status=homepage.status_code,
                )
            ]
            seen: set[str] = {homepage.final_url.split("#", 1)[0]}
            for url in queue:
                if len(pages) >= max_pages:
                    break
                normalized = url.split("#", 1)[0]
                if normalized in seen:
                    continue
                seen.add(normalized)
                try:
                    await self._assert_robots_allowed(client, normalized, headers=headers)
                    fetched = await self._fetch_page(
                        client, normalized, headers=headers, max_bytes=max_bytes
                    )
                except MapsWebsiteCrawlError:
                    continue
                fetched_title, fetched_text = await asyncio.to_thread(
                    _parse_page_content, fetched.content, max_excerpt
                )
                pages.append(
                    CrawledPage(
                        url=fetched.final_url,
                        title=fetched_title,
                        text_excerpt=fetched_text,
                        http_status=fetched.status_code,
                    )
                )
            return pages

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        headers: dict[str, str],
        max_bytes: int,
    ) -> _FetchedPage:
        settings = get_settings()
        current = await self._validate_url(url)
        redirects = 0
        while True:
            response = await client.get(current.url, headers=headers)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise MapsWebsiteCrawlError("redirect missing location")
                redirects += 1
                if redirects > settings.maps_census_website_crawl_max_redirects:
                    raise MapsWebsiteCrawlError("too many redirects")
                next_url = urljoin(current.url, location)
                current = await self._validate_url(next_url)
                continue
            content = await _read_bounded(response, max_bytes=max_bytes)
            content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if content_type and content_type not in {
                "text/html",
                "text/plain",
                "application/xhtml+xml",
            }:
                raise MapsWebsiteCrawlError(f"unsupported content type: {content_type}")
            return _FetchedPage(
                final_url=str(response.url),
                status_code=response.status_code,
                content=content.decode("utf-8", errors="replace"),
            )

    async def _assert_robots_allowed(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        headers: dict[str, str],
    ) -> None:
        parts = urlsplit(url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        try:
            response = await client.get(robots_url, headers=headers)
        except httpx.HTTPError as exc:
            raise MapsWebsiteCrawlError("robots fetch failed") from exc
        if response.status_code >= 500:
            raise MapsWebsiteCrawlError("robots unavailable")
        if response.status_code in {401, 403}:
            raise MapsWebsiteCrawlError("robots blocked")
        if response.status_code == 404:
            return
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        if not parser.can_fetch(headers["User-Agent"], url):
            raise MapsWebsiteCrawlError("disallowed by robots")

    async def _validate_url(self, url: str) -> _ValidatedUrl:
        parts = urlsplit((url or "").strip())
        if parts.scheme not in {"http", "https"}:
            raise MapsWebsiteCrawlError("unsupported URL scheme")
        host = (parts.hostname or "").strip().lower()
        if not host or host in LOCALHOST_HOSTS or host in METADATA_HOSTS:
            raise MapsWebsiteCrawlError("blocked host")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        allowed_ports = set(get_settings().source_retrieval_allowed_ports or [80, 443])
        if port not in allowed_ports:
            raise MapsWebsiteCrawlError("blocked port")
        for address in await self._resolve_host(host):
            if _is_blocked_ip(address):
                raise MapsWebsiteCrawlError("blocked address")
        normalized = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
        return _ValidatedUrl(url=normalized, host=host)

    async def _resolve_host(self, host: str) -> list[str]:
        if self._resolver is not None:
            return await self._resolver(host)
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return sorted({info[4][0] for info in infos})


@dataclass(frozen=True)
class _ValidatedUrl:
    url: str
    host: str


@dataclass(frozen=True)
class _FetchedPage:
    final_url: str
    status_code: int
    content: str


def _page_from_cache(item: dict[str, Any]) -> CrawledPage:
    return CrawledPage(
        url=str(item.get("url") or ""),
        title=str(item.get("title") or ""),
        text_excerpt=str(item.get("text_excerpt") or ""),
        http_status=int(item.get("http_status") or 0),
    )


def _normalize_domain(url: str) -> str | None:
    host = urlsplit((url or "").strip()).hostname
    return host.lower() if host else None


def _normalize_keywords(path_keywords: list[str] | None) -> list[str]:
    values = [keyword.strip().casefold() for keyword in (path_keywords or []) if keyword.strip()]
    merged = list(dict.fromkeys(values + list(DEFAULT_PATH_KEYWORDS)))
    return merged[:40]


def path_keywords_from_country_profile(profile: dict[str, Any] | None) -> list[str]:
    if not profile:
        return []
    explicit = profile.get("website_path_keywords") or []
    keywords = [str(item).strip() for item in explicit if str(item).strip()]
    provider_terms = profile.get("provider_terms") or {}
    if isinstance(provider_terms, dict):
        for terms in provider_terms.values():
            if not isinstance(terms, list):
                continue
            for term in terms[:3]:
                cleaned = str(term).strip()
                if cleaned:
                    keywords.append(cleaned)
    return keywords[:30]


def _discover_same_domain_links(
    base_url: str,
    *,
    html: str,
    domain: str,
    path_keywords: list[str],
    max_candidates: int,
) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for href in LINK_RE.findall(html[:MAX_PARSE_INPUT_CHARS]):
        absolute = urljoin(base_url, href.strip())
        parts = urlsplit(absolute)
        if parts.scheme not in {"http", "https"}:
            continue
        if (parts.hostname or "").lower() != domain:
            continue
        path = (parts.path or "/").casefold()
        if not any(keyword in path for keyword in path_keywords):
            continue
        normalized = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
        if len(candidates) >= max_candidates:
            break
    return candidates


# Hard cap on HTML handed to synchronous regex/parser work. Even linear regex on
# very large or malformed markup can pin the CPU long enough to matter, and this
# parsing historically ran on the event loop and could stall the whole worker
# (see clinique-tabet.com). Bounding the input keeps every pass predictable.
MAX_PARSE_INPUT_CHARS = 300_000


def _extract_title(html: str) -> str:
    match = TITLE_RE.search(html[:MAX_PARSE_INPUT_CHARS])
    if not match:
        return ""
    return _collapse_whitespace(TAG_RE.sub(" ", match.group(1)))


def _extract_text(html: str, *, max_chars: int) -> str:
    bounded = html[:MAX_PARSE_INPUT_CHARS]
    cleaned = SCRIPT_STYLE_RE.sub(" ", bounded)
    cleaned = TAG_RE.sub(" ", cleaned)
    parser = _TextExtractor()
    try:
        parser.feed(cleaned)
    except Exception:  # noqa: BLE001 - never let malformed markup abort a crawl
        return _collapse_whitespace(cleaned)[:max_chars]
    text = _collapse_whitespace(parser.text() or cleaned)
    return text[:max_chars]


def _parse_page_content(html: str, max_excerpt: int) -> tuple[str, str]:
    """Run all synchronous title/text extraction for a page.

    Bundled so callers can offload it via ``asyncio.to_thread`` and keep the
    event loop responsive even on pathological markup.
    """

    return _extract_title(html), _extract_text(html, max_chars=max_excerpt)


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


async def _read_bounded(response: httpx.Response, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise MapsWebsiteCrawlError("response too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _is_blocked_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    if ip in METADATA_IPS:
        return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved


maps_website_crawl_service = MapsWebsiteCrawlService()
