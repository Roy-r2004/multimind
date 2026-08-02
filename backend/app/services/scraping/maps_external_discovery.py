"""Phase 3: optional external directory discovery for Maps census runs.

Provides a generic ``MapsExternalDiscoverySource`` interface plus stub
implementations for webpage link extraction and CSV URL lists. No country-specific
connectors — callers pass URLs from the run's country profile ``directory_hints``.
"""

from __future__ import annotations

import csv
import io
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import get_settings

LINK_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)


@dataclass(frozen=True)
class ExternalDiscoveryCandidate:
    name: str
    source_url: str
    record_id: str | None = None
    city: str | None = None
    website: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ExternalDiscoveryContext:
    country_code: str
    country_name: str
    source_url: str
    source_id: str


class MapsExternalDiscoverySource(ABC):
    source_id: str

    @abstractmethod
    async def discover_candidates(
        self,
        context: ExternalDiscoveryContext,
    ) -> list[ExternalDiscoveryCandidate]:
        raise NotImplementedError


class WebpageDiscoverySource(MapsExternalDiscoverySource):
    source_id = "webpage"

    def __init__(self, *, client_factory=None) -> None:
        self._client_factory = client_factory or httpx.AsyncClient

    async def discover_candidates(
        self,
        context: ExternalDiscoveryContext,
    ) -> list[ExternalDiscoveryCandidate]:
        settings = get_settings()
        timeout = httpx.Timeout(settings.maps_census_website_crawl_timeout_seconds)
        headers = {"User-Agent": settings.maps_census_website_crawl_user_agent}
        async with self._client_factory(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(context.source_url, headers=headers)
            response.raise_for_status()
            html = response.text
        candidates: list[ExternalDiscoveryCandidate] = []
        seen: set[str] = set()
        for href in LINK_RE.findall(html):
            absolute = _absolute_url(context.source_url, href)
            if absolute is None or absolute in seen:
                continue
            seen.add(absolute)
            label = _label_from_url(absolute)
            candidates.append(
                ExternalDiscoveryCandidate(
                    name=label,
                    source_url=absolute,
                    record_id=f"{self.source_id}:{absolute}",
                    metadata={"directory_source": context.source_url},
                )
            )
            if len(candidates) >= settings.maps_census_external_discovery_max_candidates_per_source:
                break
        return candidates


class CsvDiscoverySource(MapsExternalDiscoverySource):
    source_id = "csv"

    def __init__(self, *, client_factory=None) -> None:
        self._client_factory = client_factory or httpx.AsyncClient

    async def discover_candidates(
        self,
        context: ExternalDiscoveryContext,
    ) -> list[ExternalDiscoveryCandidate]:
        settings = get_settings()
        timeout = httpx.Timeout(settings.maps_census_website_crawl_timeout_seconds)
        headers = {"User-Agent": settings.maps_census_website_crawl_user_agent}
        async with self._client_factory(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(context.source_url, headers=headers)
            response.raise_for_status()
            text = response.text
        reader = csv.DictReader(io.StringIO(text))
        candidates: list[ExternalDiscoveryCandidate] = []
        for index, row in enumerate(reader):
            name = (row.get("name") or row.get("facility_name") or row.get("title") or "").strip()
            if not name:
                continue
            website = (row.get("website") or row.get("url") or "").strip() or None
            city = (row.get("city") or row.get("locality") or "").strip() or None
            candidates.append(
                ExternalDiscoveryCandidate(
                    name=name,
                    source_url=context.source_url,
                    record_id=f"{self.source_id}:{index}",
                    city=city,
                    website=website,
                    metadata={"row": index, "directory_source": context.source_url},
                )
            )
            if len(candidates) >= settings.maps_census_external_discovery_max_candidates_per_source:
                break
        return candidates


class MapsExternalDiscoveryCoordinator:
    def __init__(self, sources: list[MapsExternalDiscoverySource] | None = None) -> None:
        self._sources = sources or [WebpageDiscoverySource(), CsvDiscoverySource()]

    def source_for_url(self, url: str) -> MapsExternalDiscoverySource:
        path = urlsplit(url).path.casefold()
        if path.endswith(".csv"):
            for source in self._sources:
                if source.source_id == "csv":
                    return source
        for source in self._sources:
            if source.source_id == "webpage":
                return source
        return self._sources[0]

    async def discover_from_profile(
        self,
        *,
        country_code: str,
        country_name: str,
        profile: dict[str, Any] | None,
    ) -> list[ExternalDiscoveryCandidate]:
        settings = get_settings()
        if not settings.maps_census_external_discovery_enabled:
            return []
        hints = []
        if profile:
            hints = [
                str(item).strip()
                for item in (profile.get("directory_hints") or [])
                if str(item).strip()
            ]
        hints = hints[: max(1, settings.maps_census_external_discovery_max_sources)]
        discovered: list[ExternalDiscoveryCandidate] = []
        for hint in hints:
            source = self.source_for_url(hint)
            context = ExternalDiscoveryContext(
                country_code=country_code,
                country_name=country_name,
                source_url=hint,
                source_id=source.source_id,
            )
            discovered.extend(await source.discover_candidates(context))
        return discovered


def _absolute_url(base_url: str, href: str) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith("#") or href.lower().startswith("javascript:"):
        return None
    if href.startswith("http://") or href.startswith("https://"):
        return href.split("#", 1)[0]
    if href.startswith("/"):
        parts = urlsplit(base_url)
        return f"{parts.scheme}://{parts.netloc}{href.split('#', 1)[0]}"
    return None


def _label_from_url(url: str) -> str:
    path = urlsplit(url).path.rstrip("/").split("/")[-1]
    cleaned = path.replace("-", " ").replace("_", " ").strip()
    return cleaned or url


maps_external_discovery_coordinator = MapsExternalDiscoveryCoordinator()
