"""Bounded official-website discovery for published rehabilitation facilities."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.models import RehabilitationFacility, RehabilitationFacilityContact
from app.services.scraping.search_providers import create_search_provider
from app.services.scraping.search_providers.base import (
    SearchProvider,
    SearchProviderError,
    SearchProviderRequest,
    SearchProviderResult,
)

# Includes markers from scripts/enrich_austria_demo_websites.py plus general directories.
_BLOCKED_HOST_PARTS = {
    "facebook.",
    "instagram.",
    "linkedin.",
    "tiktok.",
    "twitter.",
    "x.com",
    "youtube.",
    "wikipedia.",
    "maps.google.",
    "google.",
    "bing.",
    "yelp.",
    "tripadvisor.",
    "docfinder",
    "herold",
    "netdoktor",
    "gesundheit.gv.at",
    "sozialministerium",
    "willhaben.",
    "firmenabc.",
    "bmeia.gv.at",
    "directory",
    "listing",
}
_BLOCKED_PATH_TERMS = {
    "article",
    "catalog",
    "directory",
    "document",
    "download",
    "listing",
    "news",
    "profile",
    "registry",
    "search",
    "список",
    "реестр",
}
_DOCUMENT_SUFFIXES = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv")
_NAME_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "by",
    "for",
    "in",
    "of",
    "the",
    "und",
    "von",
    "der",
    "die",
    "das",
}


@dataclass(frozen=True)
class OfficialWebsiteCandidate:
    url: str
    score: int
    source_url: str
    title: str


def build_official_website_query(
    *, name: str, city: str | None, country_name: str
) -> str:
    geography = " ".join(part for part in (city, country_name) if part)
    return f'"{name.strip()}" {geography} official website'.strip()[:240]


def select_official_website(
    *,
    facility_name: str,
    city: str | None,
    country_name: str,
    results: list[SearchProviderResult],
) -> OfficialWebsiteCandidate | None:
    """Select only an unambiguous, strongly name-matching homepage."""
    name_tokens = _tokens(facility_name)
    if not name_tokens:
        return None
    scored: list[OfficialWebsiteCandidate] = []
    for item in results:
        if _is_rejected_result(item):
            continue
        parsed = _safe_url(item.url)
        if parsed is None:
            continue
        title_tokens = _tokens(f"{item.title} {item.snippet}")
        host_tokens = _tokens(parsed.hostname or "")
        title_coverage = len(name_tokens & title_tokens) / len(name_tokens)
        host_coverage = len(name_tokens & host_tokens) / len(name_tokens)
        if title_coverage < 0.65:
            continue
        blob = f"{item.title} {item.snippet}".casefold()
        score = round(title_coverage * 55 + host_coverage * 25)
        if country_name.casefold() in blob:
            score += 8
        if city and city.casefold() in blob:
            score += 5
        if "official" in blob:
            score += 8
        if parsed.path in {"", "/"}:
            score += 5
        score += max(0, 6 - max(item.rank, 1))
        if score < 65:
            continue
        scored.append(
            OfficialWebsiteCandidate(
                url=_homepage_url(parsed),
                score=score,
                source_url=item.url,
                title=item.title[:500],
            )
        )
    if not scored:
        return None
    scored.sort(key=lambda candidate: (-candidate.score, candidate.url))
    if len(scored) > 1 and scored[0].score - scored[1].score < 8:
        return None
    return scored[0]


def website_needs_enrichment(url: str | None) -> bool:
    if not url:
        return True
    parsed = _safe_url(url)
    if parsed is None:
        return True
    probe = SearchProviderResult(rank=1, url=url, title="", snippet="")
    return _is_rejected_result(probe)


class FacilityWebsiteEnrichmentService:
    def __init__(self, provider: SearchProvider | None = None) -> None:
        self.provider = provider

    async def enrich_execution(
        self,
        db: AsyncSession | None,
        *,
        organization_id: str,
        execution_id: str,
        max_facilities: int | None = None,
    ) -> dict[str, int]:
        """Enrich facilities with official websites.

        Opens short-lived DB sessions around reads/writes only. Never holds a
        connection across search awaits — that starved the background heartbeat.
        When ``db`` is provided (tests), short sessions share its bind; production
        call sites should pass ``None`` and use ``AsyncSessionLocal``.
        """
        settings = get_settings()
        limit = max(
            1,
            max_facilities
            if max_facilities is not None
            else settings.facility_website_enrichment_max_facilities_per_execution,
        )
        session_factory = self._session_factory(db)
        from app.services.scraping.execution_service import execution_service

        async with session_factory() as scan_db:
            rows = list(
                (
                    await scan_db.execute(
                        select(RehabilitationFacility)
                        .where(
                            RehabilitationFacility.organization_id == organization_id,
                            RehabilitationFacility.execution_id == execution_id,
                            RehabilitationFacility.is_mock.is_(False),
                        )
                        .order_by(RehabilitationFacility.created_at.asc())
                        .limit(limit)
                    )
                ).scalars()
            )
            pending: list[dict[str, str | None]] = []
            summary = {
                "considered": len(rows),
                "searched": 0,
                "enriched": 0,
                "preserved": 0,
                "ambiguous": 0,
                "failed": 0,
            }
            for facility in rows:
                if not website_needs_enrichment(facility.primary_website):
                    summary["preserved"] += 1
                    continue
                pending.append(
                    {
                        "id": facility.id,
                        "canonical_name": facility.canonical_name,
                        "primary_city": facility.primary_city,
                        "primary_region": facility.primary_region,
                        "country_name": facility.country_name,
                        "country_code": facility.country_code,
                    }
                )
            await scan_db.commit()

        provider = self.provider or create_search_provider()
        for item in pending:
            summary["searched"] += 1
            async with session_factory() as heartbeat_db:
                await execution_service.touch_heartbeat(heartbeat_db, execution_id)
                await heartbeat_db.commit()

            facility_id = str(item["id"])
            name = str(item["canonical_name"] or "")
            city = item["primary_city"] or item["primary_region"]
            country_name = str(item["country_name"] or "")
            country_code = str(item["country_code"] or "")
            try:
                results = await asyncio.wait_for(
                    provider.search(
                        SearchProviderRequest(
                            query=build_official_website_query(
                                name=name,
                                city=city,
                                country_name=country_name,
                            ),
                            country_code=country_code,
                            search_language="en",
                            result_limit=settings.facility_website_enrichment_results_per_facility,
                            metadata={
                                "purpose": "official_facility_website",
                                "facility_id": facility_id,
                            },
                        )
                    ),
                    timeout=settings.facility_website_enrichment_timeout_seconds,
                )
            except (TimeoutError, SearchProviderError):
                summary["failed"] += 1
                continue

            selected = select_official_website(
                facility_name=name,
                city=city,
                country_name=country_name,
                results=results,
            )
            if selected is None:
                summary["ambiguous"] += 1
                continue

            async with session_factory() as write_db:
                facility = await write_db.get(RehabilitationFacility, facility_id)
                if facility is None:
                    summary["failed"] += 1
                    continue
                if not website_needs_enrichment(facility.primary_website):
                    summary["preserved"] += 1
                    await write_db.commit()
                    continue
                facility.primary_website = selected.url
                existing = await write_db.scalar(
                    select(RehabilitationFacilityContact.id).where(
                        RehabilitationFacilityContact.facility_id == facility.id,
                        RehabilitationFacilityContact.contact_type == "website",
                        RehabilitationFacilityContact.value == selected.url,
                    )
                )
                if existing is None:
                    write_db.add(
                        RehabilitationFacilityContact(
                            facility_id=facility.id,
                            location_id=None,
                            contact_type="website",
                            label="Official website (search matched)",
                            value=selected.url,
                            normalized_value=selected.url,
                            is_primary=True,
                            available_24_7=False,
                            verification_status="search_name_match",
                            confidence_score=Decimal(str(min(selected.score / 100, 0.95))),
                            contact_discovery_status="found_unverified",
                            is_mock=False,
                        )
                    )
                await write_db.commit()
            summary["enriched"] += 1
        return summary

    @staticmethod
    def _session_factory(db: AsyncSession | None):
        if db is not None:
            # Prefer the async bind so tests can share an in-memory engine.
            bind = db.bind
            if bind is None:
                bind = db.get_bind()
            return async_sessionmaker(
                bind=bind,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        from app.db.session import AsyncSessionLocal

        return AsyncSessionLocal


def _tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return {
        token
        for token in re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
        if len(token) > 1 and token not in _NAME_STOPWORDS
    }


def _safe_url(value: str):
    try:
        parsed = urlsplit((value or "").strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    return parsed


def _is_rejected_result(item: SearchProviderResult) -> bool:
    parsed = _safe_url(item.url)
    if parsed is None:
        return True
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    blob = f"{host} {path} {item.title} {item.snippet}".casefold()
    if any(part in host for part in _BLOCKED_HOST_PARTS):
        return True
    if path.endswith(_DOCUMENT_SUFFIXES):
        return True
    return any(re.search(rf"(^|[/_\-\s]){re.escape(term)}([/_\-\s]|$)", blob) for term in _BLOCKED_PATH_TERMS)


def _homepage_url(parsed) -> str:
    host = (parsed.hostname or "").lower()
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "/", "", ""))


facility_website_enrichment_service = FacilityWebsiteEnrichmentService()
