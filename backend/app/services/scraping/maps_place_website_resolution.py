"""Deterministic official-website resolution for Maps enrichment cascade."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.db.models import MapsPlace
from app.services.scraping.facility_website_enrichment_service import (
    build_official_website_query,
    select_official_website,
    website_needs_enrichment,
)
from app.services.scraping.search_providers import create_search_provider
from app.services.scraping.search_providers.base import SearchProviderRequest

WEBSITE_RELATIONSHIPS = frozenset(
    {
        "official",
        "probable_official",
        "directory",
        "social",
        "government_listing",
        "unrelated",
        "unknown",
    }
)

CRAWLABLE_RELATIONSHIPS = frozenset({"official", "probable_official"})

_SOCIAL_HOSTS = ("facebook.", "instagram.", "linkedin.", "twitter.", "x.com", "tiktok.")
_GOV_HOST_MARKERS = (".gov.", ".gouv.", ".govt.", "minister", "ministry")
_DIRECTORY_MARKERS = (
    "directory",
    "listing",
    "yellowpages",
    "yelp.",
    "tripadvisor.",
    "docfinder",
    "firmenabc.",
)


@dataclass(frozen=True)
class WebsiteResolutionOutcome:
    official_website: str | None
    website_relationship: str
    website_relationship_confidence: float
    website_relationship_evidence: dict[str, Any]
    website_resolution_source: str | None
    website_source: str | None = None


def _normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    value = url.strip()
    if not value:
        return None
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold()


def classify_website_relationship(
    *,
    url: str,
    facility_name: str,
    city: str | None,
    country_name: str,
    phone: str | None = None,
    email_domain: str | None = None,
) -> tuple[str, float, dict[str, Any]]:
    host = _host(url)
    evidence: dict[str, Any] = {"url": url, "host": host}
    if any(marker in host for marker in _SOCIAL_HOSTS):
        return "social", 0.85, {**evidence, "reason": "social_domain"}
    if any(marker in host for marker in _GOV_HOST_MARKERS):
        return "government_listing", 0.8, {**evidence, "reason": "government_domain"}
    if any(marker in host for marker in _DIRECTORY_MARKERS):
        return "directory", 0.75, {**evidence, "reason": "directory_domain"}

    name_tokens = {token for token in re.findall(r"[a-z0-9]+", facility_name.casefold()) if len(token) > 2}
    host_tokens = {token for token in re.findall(r"[a-z0-9]+", host) if len(token) > 2}
    overlap = len(name_tokens & host_tokens) / max(len(name_tokens), 1)
    evidence["name_token_overlap"] = round(overlap, 3)
    score = 0.45 + overlap * 0.45
    if city and city.casefold() in host:
        score += 0.05
        evidence["city_in_host"] = True
    if country_name.casefold().replace(" ", "") in host.replace("-", ""):
        score += 0.03
    if phone:
        digits = re.sub(r"\D", "", phone)[-8:]
        if digits and digits in re.sub(r"\D", "", url):
            score += 0.08
            evidence["phone_match"] = True
    if email_domain and email_domain.casefold().strip(".") in host:
        score += 0.1
        evidence["email_domain_match"] = True

    score = min(score, 0.98)
    if overlap >= 0.55 and score >= 0.75:
        return "official", score, evidence
    if overlap >= 0.35 and score >= 0.6:
        return "probable_official", score, evidence
    if overlap < 0.15:
        return "unrelated", 0.4, {**evidence, "reason": "weak_name_match"}
    return "unknown", score, evidence


def resolve_from_places_website(place: MapsPlace, *, country_name: str) -> WebsiteResolutionOutcome | None:
    url = _normalize_url(place.raw_website)
    if not url or website_needs_enrichment(url):
        return None
    relationship, confidence, evidence = classify_website_relationship(
        url=url,
        facility_name=place.canonical_name or place.raw_name,
        city=place.city_name,
        country_name=country_name,
    )
    if relationship not in CRAWLABLE_RELATIONSHIPS:
        return None
    return WebsiteResolutionOutcome(
        official_website=url,
        website_relationship=relationship,
        website_relationship_confidence=confidence,
        website_relationship_evidence=evidence,
        website_resolution_source="google_places",
        website_source="places",
    )


def resolve_from_existing_official(place: MapsPlace, *, country_name: str) -> WebsiteResolutionOutcome | None:
    url = _normalize_url(place.official_website)
    if not url:
        return None
    relationship, confidence, evidence = classify_website_relationship(
        url=url,
        facility_name=place.canonical_name or place.raw_name,
        city=place.city_name,
        country_name=country_name,
    )
    source = place.website_source or place.website_resolution_source or "existing"
    return WebsiteResolutionOutcome(
        official_website=url,
        website_relationship=relationship,
        website_relationship_confidence=confidence,
        website_relationship_evidence={**evidence, "source": source},
        website_resolution_source="existing_official",
        website_source=place.website_source or "existing",
    )


def resolve_from_stored_candidates(
    place: MapsPlace,
    *,
    country_name: str,
) -> WebsiteResolutionOutcome | None:
    evidence_blob = dict(place.classification_evidence or {})
    candidates = evidence_blob.get("website_candidates") or []
    if not isinstance(candidates, list):
        return None
    best_url: str | None = None
    best_score = 0.0
    best_evidence: dict[str, Any] = {}
    best_relationship = "unknown"
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = _normalize_url(str(item.get("url") or ""))
        if not url:
            continue
        relationship, confidence, evidence = classify_website_relationship(
            url=url,
            facility_name=place.canonical_name or place.raw_name,
            city=place.city_name,
            country_name=country_name,
        )
        if relationship in CRAWLABLE_RELATIONSHIPS and confidence > best_score:
            best_url = url
            best_score = confidence
            best_evidence = evidence
            best_relationship = relationship
    if not best_url:
        return None
    return WebsiteResolutionOutcome(
        official_website=best_url,
        website_relationship=best_relationship,
        website_relationship_confidence=best_score,
        website_relationship_evidence=best_evidence,
        website_resolution_source="stored_candidates",
        website_source="search",
    )


async def resolve_via_targeted_search(
    place: MapsPlace,
    *,
    country_code: str,
    country_name: str,
) -> WebsiteResolutionOutcome | None:
    provider = create_search_provider()
    query = build_official_website_query(
        name=place.canonical_name or place.raw_name,
        city=place.city_name,
        country_name=country_name,
    )
    results = await provider.search(
        SearchProviderRequest(
            query=query,
            country_code=country_code[:2].upper(),
            search_language="en",
            result_limit=8,
        )
    )
    selected = select_official_website(
        facility_name=place.canonical_name or place.raw_name,
        city=place.city_name,
        country_name=country_name,
        results=results,
    )
    if selected is None:
        return None
    relationship, confidence, evidence = classify_website_relationship(
        url=selected.url,
        facility_name=place.canonical_name or place.raw_name,
        city=place.city_name,
        country_name=country_name,
        phone=place.international_phone_number,
    )
    return WebsiteResolutionOutcome(
        official_website=selected.url,
        website_relationship=relationship,
        website_relationship_confidence=max(confidence, selected.score / 100.0),
        website_relationship_evidence={**evidence, "search_title": selected.title},
        website_resolution_source="targeted_search",
        website_source="search",
    )


async def resolve_official_website(
    place: MapsPlace,
    *,
    country_code: str,
    country_name: str,
    enable_search: bool = True,
) -> WebsiteResolutionOutcome:
    for resolver in (
        lambda: resolve_from_existing_official(place, country_name=country_name),
        lambda: resolve_from_places_website(place, country_name=country_name),
        lambda: resolve_from_stored_candidates(place, country_name=country_name),
    ):
        outcome = resolver()
        if outcome is not None and outcome.official_website:
            return outcome

    if enable_search:
        searched = await resolve_via_targeted_search(
            place,
            country_code=country_code,
            country_name=country_name,
        )
        if searched is not None:
            return searched

    return WebsiteResolutionOutcome(
        official_website=None,
        website_relationship="unknown",
        website_relationship_confidence=0.0,
        website_relationship_evidence={"reason": "no_official_website_found"},
        website_resolution_source=None,
        website_source=None,
    )


def apply_website_resolution(place: MapsPlace, outcome: WebsiteResolutionOutcome) -> None:
    if outcome.official_website:
        place.official_website = outcome.official_website
    if outcome.website_source:
        place.website_source = outcome.website_source
    place.website_relationship = outcome.website_relationship
    place.website_relationship_confidence = outcome.website_relationship_confidence
    place.website_relationship_evidence = outcome.website_relationship_evidence
    place.website_resolution_source = outcome.website_resolution_source


__all__ = [
    "CRAWLABLE_RELATIONSHIPS",
    "WebsiteResolutionOutcome",
    "apply_website_resolution",
    "classify_website_relationship",
    "resolve_official_website",
]
