"""Phase 2: crawl facility websites and extract export columns for Maps Census."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import MapsCensusRun, MapsPlace, MapsPlaceEnrichmentStatus
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider, get_provider_registry
from app.services.scraping.contact_page_discovery_service import discover_contact_pages
from app.services.scraping.document_text_preparation_service import (
    document_text_preparation_service,
)

logger = logging.getLogger(__name__)

ENRICHMENT_TIMEOUT_SECONDS = 90.0
FETCH_TIMEOUT_SECONDS = 25.0
USER_AGENT = "MultiMind-MapsCensus/1.0 (+https://multimind.ai/maps-census)"
MAX_QUOTE_CHARACTERS = 240

SUBSTANCE_ADDICTIONS = (
    "Alcohol",
    "Cocaine",
    "Crack",
    "Methamphetamine",
    "Heroin",
    "Prescription Opioids",
    "Benzodiazepines",
    "Cannabis (dependency)",
    "Synthetic Cannabinoids",
    "Synthetic Stimulants",
    "Inhalants",
    "Stimulant Medications",
    "Ketamine",
    "Kratom",
    "MDMA/Ecstasy",
    "GHB",
    "Anabolic Steroids",
    "Novel Psychoactive Substances",
)

BEHAVIORAL_ADDICTIONS = (
    "Gambling",
    "Sex/Pornography",
    "Gaming/Internet",
    "Food/Binge Eating (clinical)",
    "Love/Relationship",
    "Shopping/Spending",
    "Exercise/Body Dysmorphia",
    "Workaholism",
    "Social Media",
    "Cryptocurrency Trading",
)

ADDICTION_TAXONOMY: tuple[str, ...] = SUBSTANCE_ADDICTIONS + BEHAVIORAL_ADDICTIONS

ADDICTION_ALIASES: dict[str, str] = {
    "opioid": "Prescription Opioids",
    "opioids": "Prescription Opioids",
    "fentanyl": "Prescription Opioids",
    "oxycodone": "Prescription Opioids",
    "vicodin": "Prescription Opioids",
    "benzo": "Benzodiazepines",
    "benzos": "Benzodiazepines",
    "marijuana": "Cannabis (dependency)",
    "cannabis": "Cannabis (dependency)",
    "meth": "Methamphetamine",
    "ecstasy": "MDMA/Ecstasy",
    "mdma": "MDMA/Ecstasy",
    "porn": "Sex/Pornography",
    "sex addiction": "Sex/Pornography",
    "internet addiction": "Gaming/Internet",
    "gaming addiction": "Gaming/Internet",
    "crypto": "Cryptocurrency Trading",
    "cryptocurrency": "Cryptocurrency Trading",
}


class EvidenceField(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: str = Field(default="", max_length=300)
    evidence_quote: str = Field(default="", max_length=MAX_QUOTE_CHARACTERS)


class MapsPlaceEnrichmentOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    addictions_treated: list[EvidenceField] = Field(default_factory=list)
    languages_spoken: list[EvidenceField] = Field(default_factory=list)
    treatment_price: EvidenceField | None = None


@dataclass(frozen=True)
class FetchedPage:
    url: str
    html: str
    final_url: str


class MapsPlaceEnrichmentService:
    async def enrich_run(self, db: AsyncSession | None, *, run_id: str) -> dict[str, int]:
        session_factory = self._session_factory(db)
        settings = get_settings()
        if not settings.maps_census_enrichment_enabled:
            return {"enriched": 0}

        async with session_factory() as scan_db:
            run = await scan_db.get(MapsCensusRun, run_id)
            if run is None:
                return {"enriched": 0}
            country_code = run.country_code
            country_name = run.country_name
            pending = (
                await scan_db.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.is_relevant.is_(True),
                        MapsPlace.official_website.is_not(None),
                        MapsPlace.enrichment_status.in_(
                            [
                                MapsPlaceEnrichmentStatus.PENDING.value,
                                MapsPlaceEnrichmentStatus.FAILED.value,
                            ]
                        ),
                    )
                )
            ).scalars().all()
            pending = pending[: max(1, settings.maps_census_enrichment_max_places_per_run)]
            place_ids = [place.id for place in pending]
            await self._mark_skipped_without_website(session_factory, run_id=run_id)

        enriched = 0
        for place_id in place_ids:
            success = await self._enrich_one(
                session_factory,
                place_id=place_id,
                country_code=country_code,
                country_name=country_name,
            )
            if success:
                enriched += 1

        async with session_factory() as final_db:
            run = await final_db.get(MapsCensusRun, run_id)
            if run is not None:
                places = (
                    await final_db.execute(
                        select(MapsPlace).where(
                            MapsPlace.run_id == run_id,
                            MapsPlace.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value,
                            MapsPlace.addictions_treated.is_not(None),
                        )
                    )
                ).scalars().all()
                run.places_enriched = sum(
                    1 for place in places if _non_empty_list(place.addictions_treated)
                )
                run.enrichment_refresh_attempts += 1
                run.enrichment_refresh_completed_at = datetime.now(UTC)
                await final_db.commit()
        return {"enriched": enriched}

    async def _enrich_one(
        self,
        session_factory,
        *,
        place_id: str,
        country_code: str,
        country_name: str,
    ) -> bool:
        settings = get_settings()
        async with session_factory() as db:
            place = await db.get(MapsPlace, place_id)
            if place is None or not place.official_website:
                return False
            place.enrichment_status = MapsPlaceEnrichmentStatus.RUNNING.value
            place.enrichment_attempts += 1
            place.enrichment_error_message = None
            await db.commit()
            website = place.official_website
            facility_name = place.canonical_name or place.raw_name
            facility_address = place.formatted_address or ""

        try:
            pages = await self._crawl_website(
                website,
                max_pages=max(1, settings.maps_census_enrichment_max_pages_per_place),
            )
            if not pages:
                raise EnrichmentError("no_pages_fetched")
            combined_text = self._combine_page_text(pages)
            output = await self._extract_fields(
                combined_text,
                country_code=country_code,
                country_name=country_name,
                facility_name=facility_name,
                facility_address=facility_address,
            )
            addictions = _normalize_addictions(output.addictions_treated, combined_text)
            languages = _normalize_evidence_list(output.languages_spoken, combined_text)
            price = _normalize_price(output.treatment_price, combined_text)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "maps_place_enrichment_failed place_id=%s error=%s",
                place_id,
                exc,
            )
            async with session_factory() as db:
                place = await db.get(MapsPlace, place_id)
                if place is not None:
                    place.enrichment_status = MapsPlaceEnrichmentStatus.FAILED.value
                    place.enrichment_error_message = str(exc)[:2000]
                    place.enrichment_completed_at = datetime.now(UTC)
                    await db.commit()
            return False

        async with session_factory() as db:
            place = await db.get(MapsPlace, place_id)
            if place is None:
                return False
            place.addictions_treated = addictions
            place.languages_spoken = languages
            place.treatment_price = price
            place.enrichment_pages_crawled = [page.final_url for page in pages]
            place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
            place.enrichment_completed_at = datetime.now(UTC)
            place.enrichment_error_message = None
            await db.commit()
        return bool(addictions)

    async def _crawl_website(self, website: str, *, max_pages: int) -> list[FetchedPage]:
        start_url = _normalize_http_url(website)
        if not start_url:
            return []
        pages: list[FetchedPage] = []
        homepage = await _fetch_html(start_url)
        if homepage is None:
            return []
        pages.append(homepage)
        if max_pages <= 1:
            return pages
        ranked = discover_contact_pages(
            base_url=homepage.final_url,
            html=homepage.html,
            max_links=max_pages - 1,
        )
        for link in ranked:
            if len(pages) >= max_pages:
                break
            fetched = await _fetch_html(link.url)
            if fetched is not None:
                pages.append(fetched)
        return pages

    def _combine_page_text(self, pages: list[FetchedPage]) -> str:
        settings = get_settings()
        max_chars = settings.facility_extraction_max_document_characters
        chunks: list[str] = []
        for page in pages:
            doc = SimpleNamespace(
                content_text=page.html,
                content_type="text/html",
                final_url=page.final_url,
                metadata_json={},
            )
            try:
                prepared = document_text_preparation_service.prepare_text_from_document(doc)
            except Exception:
                continue
            header = f"\n\n--- PAGE: {page.final_url} ---\n\n"
            chunks.append(header + prepared.text)
        combined = "".join(chunks).strip()
        if len(combined) > max_chars:
            combined = combined[:max_chars].rstrip()
        if not combined.strip():
            raise EnrichmentError("empty_prepared_text")
        return combined

    async def _extract_fields(
        self,
        website_text: str,
        *,
        country_code: str,
        country_name: str,
        facility_name: str,
        facility_address: str,
    ) -> MapsPlaceEnrichmentOutput:
        settings = get_settings()
        model = get_model(settings.maps_census_enrichment_model)
        provider = get_provider_registry().get_provider(model.provider)
        prompt = get_prompt_engine().render(
            "scraping/maps_place_enricher.j2",
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            facility_name=facility_name[:512],
            facility_address=facility_address[:512],
            addiction_taxonomy_json=json.dumps(list(ADDICTION_TAXONOMY), ensure_ascii=True),
            max_quote_characters=MAX_QUOTE_CHARACTERS,
            website_text=website_text,
        )
        response = await asyncio.wait_for(
            provider.complete(
                system=(
                    "You return strict JSON field extractions for one known addiction rehab"
                    " facility website. Never invent values without evidence quotes."
                ),
                user=prompt,
                model=model.provider_model,
                max_tokens=2500,
            ),
            timeout=ENRICHMENT_TIMEOUT_SECONDS,
        )
        raw = LLMProvider.parse_json_response(response.text)
        return MapsPlaceEnrichmentOutput.model_validate(raw)

    async def _mark_skipped_without_website(self, session_factory, *, run_id: str) -> None:
        async with session_factory() as db:
            places = (
                await db.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id,
                        MapsPlace.is_relevant.is_(True),
                        MapsPlace.official_website.is_(None),
                        MapsPlace.enrichment_status == MapsPlaceEnrichmentStatus.PENDING.value,
                    )
                )
            ).scalars().all()
            for place in places:
                place.enrichment_status = MapsPlaceEnrichmentStatus.SKIPPED.value
                place.enrichment_completed_at = datetime.now(UTC)
            if places:
                await db.commit()

    @staticmethod
    def _session_factory(db: AsyncSession | None):
        if db is not None:
            bind = db.bind
            if bind is None:
                bind = db.get_bind()
            from sqlalchemy.ext.asyncio import async_sessionmaker

            return async_sessionmaker(bind=bind, class_=AsyncSession, expire_on_commit=False)
        from app.db.session import AsyncSessionLocal

        return AsyncSessionLocal


class EnrichmentError(Exception):
    pass


def _non_empty_list(value: list[str] | None) -> bool:
    return bool(value and any(item.strip() for item in value))


def _normalize_http_url(website: str) -> str | None:
    raw = (website or "").strip()
    if not raw:
        return None
    if not raw.lower().startswith(("http://", "https://")):
        raw = f"https://{raw}"
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return raw


async def _fetch_html(url: str) -> FetchedPage | None:
    settings = get_settings()
    max_bytes = max(32_768, settings.maps_census_enrichment_max_bytes_per_page)
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8"}
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=FETCH_TIMEOUT_SECONDS,
        ) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").lower()
            if "html" not in content_type and "text/" not in content_type:
                return None
            body = response.content[:max_bytes]
            html = body.decode(response.encoding or "utf-8", errors="replace")
            final_url = str(response.url)
            return FetchedPage(url=url, html=html, final_url=final_url)
    except Exception:
        return None


def _quote_in_text(quote: str, text: str) -> bool:
    cleaned = " ".join((quote or "").split())
    if not cleaned:
        return False
    haystack = " ".join(text.split())
    if cleaned in haystack:
        return True
    return cleaned.casefold() in haystack.casefold()


def _normalize_evidence_list(fields: list[EvidenceField], text: str) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for field in fields:
        value = (field.value or "").strip()
        if not value:
            continue
        if field.evidence_quote and not _quote_in_text(field.evidence_quote, text):
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    return values


def _normalize_addictions(fields: list[EvidenceField], text: str) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for field in fields:
        raw = (field.value or "").strip()
        if not raw:
            continue
        if field.evidence_quote and not _quote_in_text(field.evidence_quote, text):
            continue
        canonical = _canonical_addiction(raw)
        if canonical is None:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        values.append(canonical)
    return values


def _canonical_addiction(raw: str) -> str | None:
    cleaned = " ".join(raw.split())
    if cleaned in ADDICTION_TAXONOMY:
        return cleaned
    folded = cleaned.casefold()
    for label in ADDICTION_TAXONOMY:
        if label.casefold() == folded:
            return label
    for alias, label in ADDICTION_ALIASES.items():
        if alias in folded:
            return label
    return None


def _normalize_price(field: EvidenceField | None, text: str) -> str | None:
    if field is None:
        return None
    value = (field.value or "").strip()
    if not value:
        return None
    if field.evidence_quote and not _quote_in_text(field.evidence_quote, text):
        return None
    if re.search(r"contact|request|call|inquir", value, flags=re.I):
        return None
    return value[:512]


maps_place_enrichment_service = MapsPlaceEnrichmentService()
