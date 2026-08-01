"""Capped-cell subdivision — pure helpers that turn one Google-Places cell that
hit its pagination/result cap into several smaller child cells so discovery
can continue past what a single query + bounded pagination pass can return.

Deliberately country-agnostic: every strategy below works off the run's own
``country_profile`` (query families / provider terms discovered live for that
country) and generic geography heuristics, never a hardcoded country's terms.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# One quadrant per subdivision keeps the child-cell fan-out bounded even when
# every strategy below fires for the same capped cell.
MAX_CHILD_CELLS_PER_SUBDIVISION = 6

_GENERIC_LOCALITY_QUALIFIERS: tuple[str, ...] = (
    "north",
    "south",
    "east",
    "west",
    "downtown",
    "suburbs",
)


@dataclass(frozen=True)
class ChildCellSpec:
    region_name: str
    city_name: str | None
    query_text: str
    query_family: str | None = None
    query_language: str | None = None
    expansion_reason: str = "capped"
    viewport_bounds: dict[str, Any] | None = None


def _quadrants(bounds: dict[str, Any]) -> list[dict[str, float]]:
    try:
        north = float(bounds["north"])
        south = float(bounds["south"])
        east = float(bounds["east"])
        west = float(bounds["west"])
    except (KeyError, TypeError, ValueError):
        return []
    mid_lat = (north + south) / 2
    mid_lng = (east + west) / 2
    return [
        {"north": north, "south": mid_lat, "east": mid_lng, "west": west},
        {"north": north, "south": mid_lat, "east": east, "west": mid_lng},
        {"north": mid_lat, "south": south, "east": mid_lng, "west": west},
        {"north": mid_lat, "south": south, "east": east, "west": mid_lng},
    ]


def subdivide_cell(
    *,
    region_name: str,
    city_name: str | None,
    query_text: str,
    query_family: str | None = None,
    query_language: str | None = None,
    country_profile: dict[str, Any] | None = None,
    viewport_bounds: dict[str, Any] | None = None,
    existing_query_texts: set[str] | None = None,
    max_children: int = MAX_CHILD_CELLS_PER_SUBDIVISION,
) -> list[ChildCellSpec]:
    """Build child-cell specs for a capped cell.

    Strategies, tried in order until ``max_children`` is reached:
      1. alternate_query_family — other query families from the country
         profile that this cell's query didn't already use.
      2. additional_local_term — another term from the *same* family/locale.
      3. city_subdivision — generic directional/locality qualifiers appended
         to the original query (no country-specific vocabulary).
      4. smaller_city_variant — narrows to just the city (or region) name so
         a following pass can pair it with fresh terms.
      5. viewport_subdivision — when a bounding box is known, split it into
         four quadrants and re-issue the same query per quadrant.
    """
    locality = (city_name or region_name or "").strip()
    seen = {t.strip().casefold() for t in (existing_query_texts or set())}
    seen.add(query_text.strip().casefold())
    children: list[ChildCellSpec] = []

    def _add(
        text: str,
        *,
        reason: str,
        family: str | None = query_family,
        language: str | None = query_language,
        bounds: dict[str, Any] | None = None,
    ) -> bool:
        if len(children) >= max_children:
            return False
        cleaned = text.strip()[:300]
        if not cleaned:
            return True
        # Viewport quadrants intentionally reuse the same query text — the
        # bounding box (not the text) is what makes each child distinct, so
        # the dedup key folds it in instead of collapsing all four quadrants.
        key = cleaned.casefold() if bounds is None else (cleaned.casefold(), tuple(sorted(bounds.items())))
        if key in seen:
            return True
        seen.add(key)
        children.append(
            ChildCellSpec(
                region_name=region_name,
                city_name=city_name,
                query_text=cleaned,
                query_family=family,
                query_language=language,
                expansion_reason=reason,
                viewport_bounds=bounds,
            )
        )
        return True

    provider_terms = (country_profile or {}).get("provider_terms")
    provider_terms = provider_terms if isinstance(provider_terms, dict) else {}
    current_family_key = (query_family or "").strip().casefold()

    # Strategy 1: alternate query-family terms.
    for family, terms in provider_terms.items():
        if len(children) >= max_children:
            break
        if str(family).strip().casefold() == current_family_key:
            continue
        if not isinstance(terms, list) or not terms:
            continue
        term = str(terms[0]).strip()
        if not term:
            continue
        candidate = f"{term} {locality}".strip() if locality else term
        if not _add(candidate, reason="alternate_query_family", family=str(family).strip()[:64]):
            break

    # Strategy 2: additional local terms from the same family.
    if len(children) < max_children and current_family_key:
        same_family_terms = provider_terms.get(query_family) or []
        if isinstance(same_family_terms, list):
            for term in same_family_terms[1:3]:
                term_str = str(term).strip()
                if not term_str:
                    continue
                candidate = f"{term_str} {locality}".strip() if locality else term_str
                if not _add(candidate, reason="additional_local_term"):
                    break

    # Strategy 3: generic city/locality subdivision qualifiers.
    for qualifier in _GENERIC_LOCALITY_QUALIFIERS:
        if len(children) >= max_children:
            break
        candidate = f"{query_text} {qualifier}".strip()
        if not _add(candidate, reason="city_subdivision"):
            break

    # Strategy 4: smaller-city variant — narrow the query to just the locality
    # name so a later planning pass can pair it with a fresh term.
    if locality and len(children) < max_children:
        _add(locality, reason="smaller_city_variant")

    # Strategy 5: viewport quadrant subdivision.
    if viewport_bounds and len(children) < max_children:
        for quadrant in _quadrants(viewport_bounds):
            if len(children) >= max_children:
                break
            _add(
                query_text,
                reason="viewport_subdivision",
                bounds=quadrant,
            )

    return children[:max_children]
