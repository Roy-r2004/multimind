"""Phase 2: enrich Maps Census facilities via a web-search LLM (no crawling).

For every relevant facility — with or without a website — we ask a web-search
capable model (Perplexity Sonar Pro by default) to find the addictions it treats
and the languages treatment is delivered in. Facilities are processed in small
batches keyed by ``place_id`` so identities never mix. Treatment price is out of
scope.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import MapsCensusRun, MapsPlace, MapsPlaceEnrichmentStatus
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider, get_provider_registry

logger = logging.getLogger(__name__)

ENRICHMENT_TIMEOUT_SECONDS = 120.0
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
    "hashish": "Cannabis (dependency)",
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
    source_url: str = Field(default="", max_length=1000)


VERDICT_CONFIRMED = "confirmed"
VERDICT_CONTRADICTED = "contradicted"
VERDICT_UNKNOWN = "unknown"
_VALID_VERDICTS = {VERDICT_CONFIRMED, VERDICT_CONTRADICTED, VERDICT_UNKNOWN}


class VerificationField(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdict: str = Field(default="", max_length=40)
    reason: str = Field(default="", max_length=400)
    source_url: str = Field(default="", max_length=1000)

    def normalized_verdict(self) -> str:
        candidate = (self.verdict or "").strip().casefold()
        return candidate if candidate in _VALID_VERDICTS else VERDICT_UNKNOWN


class MapsPlaceEnrichmentResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    place_id: str = Field(default="", max_length=64)
    verification: VerificationField = Field(default_factory=VerificationField)
    addictions_treated: list[EvidenceField] = Field(default_factory=list)
    languages_spoken: list[EvidenceField] = Field(default_factory=list)


class MapsPlaceEnrichmentBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[MapsPlaceEnrichmentResult] = Field(default_factory=list)


class EnrichmentError(Exception):
    pass


class MapsPlaceEnrichmentService:
    async def enrich_run(self, db: AsyncSession | None, *, run_id: str) -> dict[str, int]:
        session_factory = self._session_factory(db)
        settings = get_settings()
        if not settings.maps_census_enrichment_enabled:
            async with session_factory() as db:
                run = await db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.enrichment_refresh_completed_at = datetime.now(UTC)
                    await db.commit()
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
                        MapsPlace.enrichment_status.in_(
                            [
                                MapsPlaceEnrichmentStatus.PENDING.value,
                                MapsPlaceEnrichmentStatus.FAILED.value,
                                MapsPlaceEnrichmentStatus.SKIPPED.value,
                            ]
                        ),
                    )
                )
            ).scalars().all()
            pending = pending[: max(1, settings.maps_census_enrichment_max_places_per_run)]
            place_ids = [place.id for place in pending]

        enriched = 0
        batch_size = max(1, settings.maps_census_enrichment_batch_size)
        for offset in range(0, len(place_ids), batch_size):
            chunk = place_ids[offset : offset + batch_size]
            enriched += await self._enrich_batch(
                session_factory,
                place_ids=chunk,
                country_code=country_code,
                country_name=country_name,
            )

        async with session_factory() as final_db:
            run = await final_db.get(MapsCensusRun, run_id)
            if run is not None:
                relevant = (
                    await final_db.execute(
                        select(MapsPlace).where(
                            MapsPlace.run_id == run_id,
                            MapsPlace.is_relevant.is_(True),
                        )
                    )
                ).scalars().all()
                # Verification can demote places, so the relevant/website counters are
                # recomputed here rather than trusted from the classification phase.
                run.places_classified_relevant = len(relevant)
                run.places_with_website = sum(
                    1
                    for place in relevant
                    if (place.official_website or place.raw_website or "").strip()
                )
                run.places_enriched = sum(
                    1
                    for place in relevant
                    if place.enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value
                    and (
                        _non_empty_list(place.addictions_treated)
                        or _non_empty_list(place.languages_spoken)
                    )
                )
                run.enrichment_refresh_attempts = (run.enrichment_refresh_attempts or 0) + 1
                run.enrichment_refresh_completed_at = datetime.now(UTC)
                await final_db.commit()
        return {"enriched": enriched}

    async def _enrich_batch(
        self,
        session_factory,
        *,
        place_ids: list[str],
        country_code: str,
        country_name: str,
    ) -> int:
        if not place_ids:
            return 0

        async with session_factory() as db:
            places = (
                await db.execute(select(MapsPlace).where(MapsPlace.id.in_(place_ids)))
            ).scalars().all()
            by_id = {place.id: place for place in places}
            ordered = [by_id[pid] for pid in place_ids if pid in by_id]
            payloads = [self._facility_payload(place) for place in ordered]
            for place in ordered:
                place.enrichment_status = MapsPlaceEnrichmentStatus.RUNNING.value
                place.enrichment_attempts = (place.enrichment_attempts or 0) + 1
                place.enrichment_error_message = None
            run_id = ordered[0].run_id if ordered else None
            await db.commit()

        if run_id is not None:
            async with session_factory() as heartbeat_db:
                run = await heartbeat_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.heartbeat_at = datetime.now(UTC)
                    await heartbeat_db.commit()

        try:
            results = await self._search_fields(
                payloads,
                country_code=country_code,
                country_name=country_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "maps_place_enrichment_batch_failed count=%s error=%s",
                len(payloads),
                exc,
            )
            async with session_factory() as db:
                places = (
                    await db.execute(select(MapsPlace).where(MapsPlace.id.in_(place_ids)))
                ).scalars().all()
                for place in places:
                    place.enrichment_status = MapsPlaceEnrichmentStatus.FAILED.value
                    place.enrichment_error_message = str(exc)[:2000]
                    place.enrichment_completed_at = datetime.now(UTC)
                await db.commit()
            return 0

        by_place = {result.place_id: result for result in results}
        enriched = 0
        async with session_factory() as db:
            places = (
                await db.execute(select(MapsPlace).where(MapsPlace.id.in_(place_ids)))
            ).scalars().all()
            for place in places:
                result = by_place.get(place.id)
                verdict = (
                    result.verification.normalized_verdict() if result else VERDICT_UNKNOWN
                )
                place.verification_verdict = verdict
                if result is not None:
                    place.verification_reason = (result.verification.reason or "").strip()[:400]
                    place.verification_source_url = (
                        result.verification.source_url or ""
                    ).strip()[:1024] or None

                if verdict == VERDICT_CONTRADICTED:
                    # Web sources say this listing is not an addiction provider — drop it
                    # from the census instead of enriching it.
                    place.is_relevant = False
                    place.addictions_treated = []
                    place.languages_spoken = []
                else:
                    addictions = (
                        _normalize_addictions(result.addictions_treated) if result else []
                    )
                    languages = (
                        _normalize_languages(result.languages_spoken) if result else []
                    )
                    place.addictions_treated = addictions
                    place.languages_spoken = languages
                    if addictions or languages:
                        enriched += 1

                place.enrichment_status = MapsPlaceEnrichmentStatus.COMPLETED.value
                place.enrichment_completed_at = datetime.now(UTC)
                place.enrichment_error_message = None
            await db.commit()
        return enriched

    def _facility_payload(self, place: MapsPlace) -> dict[str, str | list[str] | None]:
        return {
            "place_id": place.id,
            "name": (place.canonical_name or place.raw_name or "").strip(),
            "city": (place.city_name or "").strip() or None,
            "region": (place.region_name or "").strip() or None,
            "address": (place.formatted_address or "").strip() or None,
            "phone": (place.international_phone_number or "").strip() or None,
            "website": (place.official_website or place.raw_website or "").strip() or None,
            "place_types": list(place.place_types or []),
        }

    async def _search_fields(
        self,
        payloads: list[dict],
        *,
        country_code: str,
        country_name: str,
    ) -> list[MapsPlaceEnrichmentResult]:
        settings = get_settings()
        model = get_model(settings.maps_census_enrichment_model)
        provider = get_provider_registry().get_provider(model.provider)
        prompt = get_prompt_engine().render(
            "scraping/maps_place_enricher.j2",
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            addiction_taxonomy_json=json.dumps(list(ADDICTION_TAXONOMY), ensure_ascii=True),
            facilities_json=json.dumps(payloads, ensure_ascii=False),
        )
        response = await asyncio.wait_for(
            provider.complete(
                system=(
                    "You have live web search. Research each addiction-rehab facility"
                    " independently and return strict JSON. Never invent values without a"
                    " supporting web source."
                ),
                user=prompt,
                model=model.provider_model,
                max_tokens=3000,
            ),
            timeout=ENRICHMENT_TIMEOUT_SECONDS,
        )
        raw = LLMProvider.parse_json_response(response.text)
        return MapsPlaceEnrichmentBatch.model_validate(_normalize_batch_payload(raw)).results

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


def _normalize_batch_payload(raw: object) -> dict:
    if isinstance(raw, list):
        return {"results": raw}
    if isinstance(raw, dict):
        items = raw.get("results") or raw.get("facilities") or raw.get("decisions") or []
        return {"results": items if isinstance(items, list) else []}
    return {"results": []}


def _non_empty_list(value: list[str] | None) -> bool:
    return bool(value and any(str(item).strip() for item in value))


def _normalize_languages(fields: list[EvidenceField]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for field in fields:
        value = (field.value or "").strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
    return values


def _normalize_addictions(fields: list[EvidenceField]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for field in fields:
        raw = (field.value or "").strip()
        if not raw:
            continue
        canonical = _canonical_addiction(raw)
        if canonical is None or canonical in seen:
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


maps_place_enrichment_service = MapsPlaceEnrichmentService()
