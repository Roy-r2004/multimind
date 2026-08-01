"""Phase 2: country discovery profile stage — run once per Maps census run,
before grid planning.

Asks a web-search-capable model (Perplexity Sonar Pro by default, same pattern
as ``maps_place_enrichment_service``) to discover the *local* addiction-treatment
provider search vocabulary for a country: major administrative regions,
languages, and query-family search terms (generic / residential / outpatient /
detox / association / acronym). This is discovery terminology only — it never
classifies ownership, funding, or predicts a facility count, and it never
hardcodes country-specific vocabulary; every country is treated identically by
this module.

The resulting profile is persisted onto ``MapsCensusRun.country_profile`` and
consumed by the grid planner in a later task. Profiling failure never fails the
whole census — the caller (``maps_census_service.run_census``) continues with
``country_profile=None`` when this stage fails.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import MapsCensusRun, MapsCountryProfileStatus
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider, get_provider_registry

logger = logging.getLogger(__name__)

DEFAULT_COUNTRY_PROFILE_TIMEOUT_SECONDS = 90.0
MAX_TERM_LENGTH = 200
MAX_REGION_NAME_LENGTH = 160


class MapsCountryProfileRegion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(default="", max_length=MAX_REGION_NAME_LENGTH)
    importance: str | None = Field(default=None, max_length=MAX_REGION_NAME_LENGTH)


class MapsCountryDiscoveryProfile(BaseModel):
    """Live-web discovery terminology for one country's addiction-treatment
    provider landscape — inputs for the Maps grid planner, never a facility
    count or ownership verdict."""

    model_config = ConfigDict(extra="ignore")

    administrative_regions: list[MapsCountryProfileRegion] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    provider_terms: dict[str, list[str]] = Field(default_factory=dict)
    query_families: list[str] = Field(default_factory=list)
    common_acronyms: list[str] = Field(default_factory=list)
    directory_hints: list[str] = Field(default_factory=list)
    notes: str = Field(default="", max_length=2000)


class MapsCountryProfileError(Exception):
    pass


class MapsCountryProfileService:
    async def build_profile_for_run(
        self, db: AsyncSession | None, *, run_id: str
    ) -> dict[str, Any] | None:
        """Discover and persist a country profile for ``run_id``.

        Mirrors the short-lived-session discipline of ``maps_place_enrichment_service``
        so the network call to Sonar never holds a DB connection open. Never raises —
        a failure is recorded on the run (``country_profile_status=failed``) and
        ``None`` is returned so the caller can continue planning regardless.
        """
        session_factory = self._session_factory(db)

        async with session_factory() as start_db:
            run = await start_db.get(MapsCensusRun, run_id)
            if run is None:
                return None
            run.country_profile_status = MapsCountryProfileStatus.PENDING.value
            run.country_profile_error = None
            await start_db.commit()
            country_code = run.country_code
            country_name = run.country_name

        try:
            profile = await self._discover_profile(
                country_code=country_code, country_name=country_name
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "maps_country_profile_failed run_id=%s country=%s error=%s",
                run_id,
                country_code,
                exc,
            )
            async with session_factory() as fail_db:
                run = await fail_db.get(MapsCensusRun, run_id)
                if run is not None:
                    run.country_profile_status = MapsCountryProfileStatus.FAILED.value
                    run.country_profile_error = str(exc)[:2000]
                    await fail_db.commit()
            return None

        profile_dict = profile.model_dump(mode="json", exclude_none=True)
        async with session_factory() as write_db:
            run = await write_db.get(MapsCensusRun, run_id)
            if run is not None:
                run.country_profile = profile_dict
                run.country_profile_status = MapsCountryProfileStatus.COMPLETED.value
                run.country_profile_error = None
                await write_db.commit()
        return profile_dict

    async def _discover_profile(
        self, *, country_code: str, country_name: str
    ) -> MapsCountryDiscoveryProfile:
        settings = get_settings()
        model = get_model(settings.maps_census_country_profile_model)
        provider = get_provider_registry().get_provider(model.provider)
        prompt = get_prompt_engine().render(
            "scraping/maps_country_profile.j2",
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
        )
        timeout_seconds = (
            settings.maps_census_country_profile_timeout_seconds
            or DEFAULT_COUNTRY_PROFILE_TIMEOUT_SECONDS
        )
        response = await asyncio.wait_for(
            provider.complete(
                system=(
                    "You have live web search. Discover local addiction-treatment"
                    " provider discovery terminology for the requested country and"
                    " return strict JSON only. Never invent a facility count."
                ),
                user=prompt,
                model=model.provider_model,
                max_tokens=2000,
            ),
            timeout=timeout_seconds,
        )
        raw = LLMProvider.parse_json_response(response.text)
        return MapsCountryDiscoveryProfile.model_validate(_normalize_profile_payload(raw))

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


def _normalize_profile_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    provider_terms = _normalize_provider_terms(
        raw.get("provider_terms") or raw.get("search_terms") or raw.get("terms") or {}
    )
    query_families = _normalize_string_list(
        raw.get("query_families") or raw.get("families") or list(provider_terms.keys())
    )

    return {
        "administrative_regions": _normalize_regions(
            raw.get("administrative_regions") or raw.get("regions") or []
        ),
        "languages": _normalize_string_list(raw.get("languages") or raw.get("language_codes") or []),
        "provider_terms": provider_terms,
        "query_families": query_families,
        "common_acronyms": _normalize_string_list(raw.get("common_acronyms") or []),
        "directory_hints": _normalize_string_list(raw.get("directory_hints") or []),
        "notes": str(raw.get("notes") or "")[:2000],
    }


def _normalize_regions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            name = item.strip()
            if name:
                normalized.append({"name": name[:MAX_REGION_NAME_LENGTH]})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("region") or "").strip()
        if not name:
            continue
        entry: dict[str, Any] = {"name": name[:MAX_REGION_NAME_LENGTH]}
        importance = item.get("importance") or item.get("priority") or item.get("tier")
        if importance:
            entry["importance"] = str(importance).strip()[:MAX_REGION_NAME_LENGTH]
        normalized.append(entry)
    return normalized


def _normalize_provider_terms(raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, list[str]] = {}
    for key, value in raw.items():
        family = str(key).strip().casefold().replace(" ", "_").replace("-", "_")
        if not family:
            continue
        terms = _normalize_string_list(value)
        if terms:
            normalized[family] = terms
    return normalized


def _normalize_string_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    values: list[str] = []
    seen: set[str] = set()
    for item in raw:
        value = str(item).strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        values.append(value[:MAX_TERM_LENGTH])
    return values


maps_country_profile_service = MapsCountryProfileService()
