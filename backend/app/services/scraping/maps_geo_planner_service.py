"""Optional, opt-in AI search-planning layer for Maps census geographic
targeting (disabled by default — ``settings.maps_geo_ai_planner_enabled``).

This is deliberately the *only* thing an LLM is allowed to influence about
geographic targeting: search priorities, local-language terms, and a
coarse strategy sequence *within* a region the deterministic resolver
(``maps_geo_regions``/``maps_geo_boundaries``) has already verified. The
response schema below has no latitude/longitude field at all — not "a
field we validate," but a field that does not exist — so even a model
that hallucinates coordinates has nowhere for them to land; any unexpected
key in the raw response is silently dropped (``extra="ignore"``) rather
than merged into the plan.

Called at most once per run (never per cell/subdivision), mirroring the
pattern in ``maps_country_profile_service``: build a short-lived session,
call the model, persist the result, never let a planner failure fail the
run. Results are cached on disk keyed by (country, state, model) so a
second run over the same geography/config never re-pays for a planning
call — see ``_cache_path``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import get_settings
from app.llm.catalog import get_model
from app.llm.providers import get_provider_registry

logger = logging.getLogger(__name__)

MAX_AREA_NAME_LENGTH = 160
MAX_TERM_LENGTH = 120
MAX_AREAS = 15
MAX_TERMS = 25


class MapsGeoPlanningSuggestion(BaseModel):
    """AI-suggested search priorities within an already-resolved,
    deterministically-verified region. Deliberately has no coordinate
    field of any kind — this is a planning hint, never a source of
    geographic truth."""

    model_config = ConfigDict(extra="ignore")

    priority_areas: list[str] = Field(default_factory=list)
    rural_or_remote_notes: str = Field(default="", max_length=1000)
    local_language_terms: list[str] = Field(default_factory=list)
    additional_search_terms: list[str] = Field(default_factory=list)
    strategy_notes: str = Field(default="", max_length=1000)


class MapsGeoPlanningResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    suggestion: MapsGeoPlanningSuggestion | None = None
    used_ai: bool = False
    cached: bool = False
    model: str | None = None
    skipped_reason: str | None = None


def _cache_dir() -> Path:
    settings = get_settings()
    base = Path(settings.maps_geo_boundary_cache_dir)
    if not base.is_absolute():
        backend_root = Path(__file__).resolve().parents[3]
        base = backend_root / base
    return base.parent / "geo_ai_planning_cache"


def _cache_key(
    *, country_code: str | None, state_code: str | None, model: str
) -> str:
    raw = f"{(country_code or '').upper()}|{(state_code or '').upper()}|{model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _cache_path(**kwargs: Any) -> Path:
    return _cache_dir() / f"{_cache_key(**kwargs)}.json"


async def plan_geo_strategy(
    *,
    country_code: str | None,
    country_name: str | None,
    state_code: str | None,
    state_name: str | None,
    resolved_region_label: str | None,
) -> MapsGeoPlanningResult:
    """Return an AI planning suggestion for this run's geography, or a
    result explaining why none was produced.

    Never raises. ``resolved_region_label`` should describe only the
    already-verified region (e.g. "Ontario, Canada") — this function does
    not accept or forward any coordinate/polygon data to the model, and
    the AI is explicitly told it may not expand beyond that region.
    """
    settings = get_settings()
    if not settings.maps_geo_ai_planner_enabled:
        return MapsGeoPlanningResult(used_ai=False, skipped_reason="ai_planner_disabled")

    model_name = settings.maps_geo_ai_planner_model
    cache_path = _cache_path(country_code=country_code, state_code=state_code, model=model_name)
    cached = _read_cache(cache_path)
    if cached is not None:
        return MapsGeoPlanningResult(
            suggestion=cached, used_ai=True, cached=True, model=model_name
        )

    try:
        suggestion = await _call_planner(
            model_name=model_name,
            country_code=country_code,
            country_name=country_name,
            state_code=state_code,
            state_name=state_name,
            resolved_region_label=resolved_region_label,
            timeout_seconds=settings.maps_geo_ai_planner_timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "maps_geo_ai_planner_failed country=%s state=%s error=%s",
            country_code,
            state_code,
            exc,
        )
        return MapsGeoPlanningResult(
            used_ai=False, skipped_reason=f"ai_planner_error: {exc}"[:500]
        )

    _write_cache(cache_path, suggestion)
    return MapsGeoPlanningResult(
        suggestion=suggestion, used_ai=True, cached=False, model=model_name
    )


async def _call_planner(
    *,
    model_name: str,
    country_code: str | None,
    country_name: str | None,
    state_code: str | None,
    state_name: str | None,
    resolved_region_label: str | None,
    timeout_seconds: float,
) -> MapsGeoPlanningSuggestion:
    model = get_model(model_name)
    provider = get_provider_registry().get_provider(model.provider)

    region = resolved_region_label or f"{state_name or ''} {country_name or country_code or ''}".strip()
    user_prompt = (
        "A geographic search region has ALREADY been verified by a deterministic "
        f"boundary resolver: {region!r} "
        f"(country_code={country_code!r}, state_code={state_code!r}). "
        "Suggest search PRIORITIES only — never propose expanding outside this "
        "region, and never include latitude/longitude or any coordinates in your "
        "answer (you have no authority over boundaries or coordinates). Return "
        "strict JSON with keys: priority_areas (list of place names worth "
        "searching first, inside the region), rural_or_remote_notes (string), "
        "local_language_terms (list of strings), additional_search_terms (list "
        "of strings for addiction-treatment provider discovery), strategy_notes "
        "(string)."
    )
    response = await asyncio.wait_for(
        provider.complete(
            system=(
                "You are a search-planning assistant. You never determine "
                "geographic boundaries or coordinates; you only suggest search "
                "priorities within a region someone else has already verified. "
                "Return strict JSON only."
            ),
            user=user_prompt,
            model=model.provider_model,
            max_tokens=1200,
        ),
        timeout=timeout_seconds,
    )
    raw = _parse_json(response.text)
    return MapsGeoPlanningSuggestion.model_validate(_normalize_suggestion(raw))


def _parse_json(text: str) -> dict[str, Any]:
    import re

    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("AI geo planner response was not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("AI geo planner response was not a JSON object")
    return parsed


def _normalize_string_list(raw: Any, *, limit: int) -> list[str]:
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
        if len(values) >= limit:
            break
    return values


def _normalize_suggestion(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "priority_areas": _normalize_string_list(raw.get("priority_areas"), limit=MAX_AREAS),
        "rural_or_remote_notes": str(raw.get("rural_or_remote_notes") or "")[:1000],
        "local_language_terms": _normalize_string_list(
            raw.get("local_language_terms"), limit=MAX_TERMS
        ),
        "additional_search_terms": _normalize_string_list(
            raw.get("additional_search_terms"), limit=MAX_TERMS
        ),
        "strategy_notes": str(raw.get("strategy_notes") or "")[:1000],
    }


def _read_cache(path: Path) -> MapsGeoPlanningSuggestion | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return MapsGeoPlanningSuggestion.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None


def _write_cache(path: Path, suggestion: MapsGeoPlanningSuggestion) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(suggestion.model_dump_json(), encoding="utf-8")
    except OSError:
        logger.warning("maps_geo_ai_planner_cache_write_failed path=%s", path)
