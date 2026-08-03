"""LLM-generated city/region x query-term grid for a Maps census run.

There is no static per-country city list in this codebase (``countries.py`` only
has code/name), and a fixed list could not sensibly cover ~190 countries with
sane local-language terms. So the grid is planned once per run by an LLM,
mirroring how ``official_source_seed_planner`` plans registry URLs.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider, get_provider_registry
from app.services.scraping.maps_keyword_strategy import (
    generate_search_queries,
    CORE_FACILITY_TYPES,
    MODIFIERS,
    SUBSTANCE_ADDICTIONS,
    BEHAVIORAL_ADDICTIONS,
)

DEFAULT_MODEL = "gpt-4.1"


class MapsGridCell(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Deliberately no min_length here: a single malformed cell from the LLM must not
    # invalidate the whole batch validation. Blank cells are dropped in _dedupe_cells.
    region_name: str = Field(default="", max_length=160)
    city_name: str | None = Field(default=None, max_length=160)
    query_text: str = Field(default="", max_length=300)
    # Populated when the LLM attributes a cell to one of the country profile's
    # query families (generic/residential/outpatient/detox/association/acronym);
    # left None when unattributable or no profile was available.
    query_family: str | None = Field(default=None, max_length=64)
    query_language: str | None = Field(default=None, max_length=32)


class MapsGridPlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cells: list[MapsGridCell] = Field(default_factory=list)


class MapsGridPlanningError(Exception):
    pass


class MapsGridPlanner:
    async def plan(
        self,
        *,
        country_code: str,
        country_name: str,
        max_cells: int,
        country_profile: dict[str, Any] | None = None,
        focus_region_names: list[str] | None = None,
    ) -> list[MapsGridCell]:
        """Plan a search grid, or — when ``focus_region_names`` is given — an
        expansion batch of *additional* cells for those already-productive
        regions only (used by the adaptive census loop instead of re-planning
        the whole country every time a region wants more coverage)."""
        capped = max(1, int(max_cells or 1))
        model = get_model(DEFAULT_MODEL)
        provider = get_provider_registry().get_provider(model.provider)
        prompt = get_prompt_engine().render(
            "scraping/maps_grid_planner.j2",
            max_cells=capped,
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
            country_profile_json=json.dumps(country_profile or {}, ensure_ascii=False),
            focus_region_names=list(focus_region_names or []),
        )
        try:
            response = await provider.complete(
                system=(
                    "MISSION: Find private/NGO/charity addiction treatment centers (inpatient, 24+ hour care, "
                    "core service is substance/behavioral addiction treatment, not general mental health).\n\n"
                    "Generate Google Places search queries using EXACT keyword combinations:\n\n"
                    "FACILITY KEYWORDS (core service must be addiction treatment, inpatient/residential):\n"
                    f"   {', '.join(CORE_FACILITY_TYPES)}\n\n"
                    "MODIFIERS (funding sources & care settings — prioritize private pay, insurance, NGO, charity):\n"
                    f"   {', '.join(MODIFIERS)}\n\n"
                    "TARGET SUBSTANCE ADDICTIONS (map all results to these — include specific drug names):\n"
                    f"   {', '.join(SUBSTANCE_ADDICTIONS)}\n\n"
                    "TARGET BEHAVIORAL ADDICTIONS (clinically serious, not lifestyle coaching):\n"
                    f"   {', '.join(BEHAVIORAL_ADDICTIONS)}\n\n"
                    "RULES (NON-NEGOTIABLE):\n"
                    "1. Use EXACT keywords (word-for-word, no rewording, no synonyms).\n"
                    "2. Translate ONLY the keywords to country language (exact translations in target language).\n"
                    "3. Combine: [facility keyword] + [modifier OR addiction term] + [city name].\n"
                    "4. Exclude: government-funded, outpatient-only, telehealth, solo practitioner, referral-only, "
                    "general psychiatry (unless addiction-focused), wellness/coaching, PO box/virtual.\n"
                    "5. Do NOT rephrase keywords, do NOT add words, do NOT create alternatives.\n"
                    "6. Generate 15-20 queries max per city.\n"
                    "7. Hidden from users—only results displayed.\n"
                    "8. For multilingual records: translate ALL fields to English, map addiction terms to "
                    "Target Addictions list, retain original-language text as reference."
                ),
                user=prompt,
                model=model.provider_model,
                max_tokens=3500,
            )
            raw = LLMProvider.parse_json_response(response.text)
            known_families = _known_family_keys(country_profile)
            plan = MapsGridPlan.model_validate(
                _normalize_grid_payload(raw, known_families=known_families)
            )
        except Exception as exc:  # noqa: BLE001
            raise MapsGridPlanningError("Maps census grid planning failed.") from exc
        return _dedupe_cells(plan.cells, max_cells=capped)


def _known_family_keys(country_profile: dict[str, Any] | None) -> set[str] | None:
    """Normalized (casefold/strip) set of query-family names known to the profile.

    ``provider_terms`` keys are already normalized by the country profile service,
    but ``query_families`` may retain the LLM's original casing — both sides must
    be casefolded/stripped before cross-referencing them. Returns ``None`` when the
    profile is missing or carries no usable families, so callers can skip filtering
    entirely (e.g. failed-profile fallback).
    """
    if not country_profile:
        return None
    keys: set[str] = set()
    provider_terms = country_profile.get("provider_terms")
    if isinstance(provider_terms, dict):
        keys.update(str(key).strip().casefold() for key in provider_terms if str(key).strip())
    query_families = country_profile.get("query_families")
    if isinstance(query_families, list):
        keys.update(str(name).strip().casefold() for name in query_families if str(name).strip())
    return keys or None


def _normalize_query_family(raw_family: Any, known_families: set[str] | None) -> str | None:
    value = str(raw_family or "").strip().casefold()
    if not value:
        return None
    if known_families is not None and value not in known_families:
        return None
    return value[:64]


def _normalize_grid_payload(
    raw: Any, *, known_families: set[str] | None = None
) -> dict[str, Any]:
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = raw.get("cells") or raw.get("grid") or raw.get("queries") or []
    else:
        items = []
    if not isinstance(items, list):
        items = []
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "region_name": str(item.get("region_name") or item.get("region") or "").strip()[:160],
                "city_name": (str(item.get("city_name") or item.get("city") or "").strip()[:160] or None),
                "query_text": str(item.get("query_text") or item.get("query") or "").strip()[:300],
                "query_family": _normalize_query_family(
                    item.get("query_family") or item.get("family"), known_families
                ),
                "query_language": (
                    str(item.get("query_language") or item.get("language") or "").strip()[:32] or None
                ),
            }
        )
    return {"cells": normalized}


def _dedupe_cells(cells: list[MapsGridCell], *, max_cells: int) -> list[MapsGridCell]:
    result: list[MapsGridCell] = []
    seen: set[str] = set()
    for cell in cells:
        if not cell.region_name or not cell.query_text:
            continue
        key = cell.query_text.strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(cell)
        if len(result) >= max_cells:
            break
    return result


def _fallback_keyword_grid(
    country_code: str, focus_cities: list[str] | None = None, max_cells: int = 120
) -> list[MapsGridCell]:
    """Fallback grid using targeted keyword strategy when LLM fails.

    Uses predefined core facility types + addiction-specific terms to generate
    high-quality search queries. Hidden from users—only results are displayed.
    """
    cells: list[MapsGridCell] = []

    # Use focus cities if provided, else use placeholder cities
    cities = focus_cities or ["City 1", "City 2", "City 3"]

    for city in cities[:10]:  # Use up to 10 cities
        # Generate keyword-based queries for this city
        queries = generate_search_queries(city, country_code=country_code)
        for query_text in queries:
            if len(cells) >= max_cells:
                break
            cells.append(
                MapsGridCell(
                    region_name=city,
                    city_name=city,
                    query_text=query_text,
                    query_family="targeted-keyword",
                    query_language=None,
                )
            )
        if len(cells) >= max_cells:
            break

    return _dedupe_cells(cells, max_cells=max_cells)


maps_grid_planner = MapsGridPlanner()
