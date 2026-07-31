"""LLM-generated city/region x query-term grid for a Maps census run.

There is no static per-country city list in this codebase (``countries.py`` only
has code/name), and a fixed list could not sensibly cover ~190 countries with
sane local-language terms. So the grid is planned once per run by an LLM,
mirroring how ``official_source_seed_planner`` plans registry URLs.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider, get_provider_registry

DEFAULT_MODEL = "gpt-4.1"


class MapsGridCell(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Deliberately no min_length here: a single malformed cell from the LLM must not
    # invalidate the whole batch validation. Blank cells are dropped in _dedupe_cells.
    region_name: str = Field(default="", max_length=160)
    city_name: str | None = Field(default=None, max_length=160)
    query_text: str = Field(default="", max_length=300)


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
    ) -> list[MapsGridCell]:
        capped = max(1, int(max_cells or 1))
        model = get_model(DEFAULT_MODEL)
        provider = get_provider_registry().get_provider(model.provider)
        prompt = get_prompt_engine().render(
            "scraping/maps_grid_planner.j2",
            max_cells=capped,
            country_code=(country_code or "XX")[:2].upper(),
            country_name=(country_name or "Unknown")[:120],
        )
        try:
            response = await provider.complete(
                system=(
                    "You return strict JSON describing a Google Places search grid "
                    "for a country's non-government inpatient addiction rehab facility census."
                ),
                user=prompt,
                model=model.provider_model,
                max_tokens=3500,
            )
            raw = LLMProvider.parse_json_response(response.text)
            plan = MapsGridPlan.model_validate(_normalize_grid_payload(raw))
        except Exception as exc:  # noqa: BLE001
            raise MapsGridPlanningError("Maps census grid planning failed.") from exc
        return _dedupe_cells(plan.cells, max_cells=capped)


def _normalize_grid_payload(raw: Any) -> dict[str, Any]:
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


maps_grid_planner = MapsGridPlanner()
