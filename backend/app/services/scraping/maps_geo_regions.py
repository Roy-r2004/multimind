"""Trusted, static geographic anchors for Maps census runs.

Nothing in the discovery pipeline geocodes anything — ``maps_grid_planner``
is pure LLM text planning and never produces coordinates. Gosom (non-fast
mode) only narrows a search geographically via a map center + zoom (see
``maps_gosom_client._center_and_zoom_from_viewport``), built from a cell's
``viewport_bounds``. Without an initial bounding box to seed that chain, no
cell in a run can ever carry one, and ``viewport_subdivision`` — the one
subdivision strategy that actually narrows geography once lat/lon/zoom are
wired through — has nothing to subdivide.

This module is the deliberately small, honest answer to that gap: a static
table of well-established, publicly published land-area bounding boxes (the
kind printed in any standard atlas or gazetteer — not computed, geocoded, or
invented) for a growing set of administrative regions this product has
actually run against or been asked to support. ``resolve_geo_anchor``
returns ``None`` for anything not in the table — callers must treat that as
"no geographic resolution available" and fall back to the existing
text-only search, never claim geographic coverage they don't have.

Keyed by country first (``_STATE_BOUNDING_BOXES_BY_COUNTRY["US"]["AL"]``,
not a single flat US-only table) so adding a country's own
administrative-division boundaries later — a different country has a
different structure (provinces, Länder, prefectures, ...) — is a sibling
top-level entry, not a redesign. Only "US" has state/province-level data
today; this module makes no claim about any other country's subdivisions.
``_COUNTRY_BOUNDING_BOXES`` is the separate, whole-country-level table
(e.g. Finland) used only when a run has no state/province selected and no
dataset-backed boundary is cached locally — see resolve_geo_anchor_detailed.
Adding one more state/region or country is a one-line, reviewable addition
of its standard bounding box — no code changes, no new dependency, no
network call. A country needing real sub-national boundaries still needs
its own reviewed table the same way the US one was built — see the session
report that introduced this comment for the recommended path to that
(an authoritative, licensed boundary dataset, not ad hoc recall).
"""

from __future__ import annotations

from typing import Any

from app.services.scraping import maps_geo_boundaries

# Standard land-area bounding boxes (degrees), the extents commonly published
# for each region (e.g. the ones summarized on government/GIS reference
# pages), padded by ~0.05-0.1 degrees to comfortably include border areas
# without claiming false precision. These are reference geography recalled
# from general knowledge of well-established, standard, publicly-documented
# facts (the same kind of fact as a country's capital) — not measured,
# geocoded, or computed by this codebase, and not yet cross-checked this
# session against an authoritative dataset (see the session report).
# Flag any correction needed before relying on these in a real campaign.
_STATE_BOUNDING_BOXES_BY_COUNTRY: dict[str, dict[str, dict[str, float]]] = {
    "US": {
        "AL": {"north": 35.08, "south": 30.14, "east": -84.85, "west": -88.53},
        "MI": {"north": 48.31, "south": 41.65, "east": -82.12, "west": -90.52},
        "CO": {"north": 41.05, "south": 36.95, "east": -101.95, "west": -109.15},
        "FL": {"north": 31.10, "south": 24.40, "east": -79.90, "west": -87.70},
    },
}

# Standard land-area bounding boxes (degrees) for whole countries, the same
# kind of publicly-published atlas extent as the state table above, padded
# by ~0.1 degrees. Used only as a last-resort fallback by
# resolve_geo_anchor_detailed when no dataset-backed ADM0 boundary is cached
# locally (see maps_geo_boundaries.load_cached_boundary) — without this, a
# country with no cached boundary file gets geo_boundary_match="no_
# polygon_available" for every result, so even a result hundreds of miles
# outside the country (e.g. a Pompano Beach, FL facility surfacing in a
# Finland run: 0afe4ca7-2dd3-47aa-a2cd-566e6b19b208) is never caught by
# compute_client_eligibility's "outside_polygon" downgrade. A plain bounding
# box cannot replace a real polygon (coastlines, exclaves, etc.), so it is
# still deliberately coarse — it only ever catches gross false positives
# like a different continent, never fine border disputes.
_COUNTRY_BOUNDING_BOXES: dict[str, dict[str, float]] = {
    "FI": {"north": 70.2, "south": 59.3, "east": 31.7, "west": 20.3},
}

_STATE_NAME_TO_CODE_BY_COUNTRY: dict[str, dict[str, str]] = {
    "US": {
        "alabama": "AL",
        "michigan": "MI",
        "colorado": "CO",
        "florida": "FL",
    },
}


def resolve_geo_anchor(
    *, country_code: str | None, state_code: str | None, state_name: str | None
) -> dict[str, float] | None:
    """Return a trusted bounding box for the run's country/state, or
    ``None`` when none is known.

    Only entries actually present in ``_STATE_BOUNDING_BOXES_BY_COUNTRY``
    resolve to a box — today that is a handful of US states. Every other
    country/state combination (including a US state not yet in the table,
    a US country-only search with no state selected, or any non-US
    country) returns ``None`` by design, rather than guess at a box for it
    or fall back to a broader scope than the one requested.
    """
    country_key = (country_code or "").strip().upper()
    code = (state_code or "").strip().upper()
    name = (state_name or "").strip()

    if not code and not name:
        country_box = _COUNTRY_BOUNDING_BOXES.get(country_key)
        return dict(country_box) if country_box is not None else None

    country_boxes = _STATE_BOUNDING_BOXES_BY_COUNTRY.get(country_key)
    if not country_boxes:
        return None

    if code in country_boxes:
        return dict(country_boxes[code])

    name_key = name.casefold()
    name_to_code = _STATE_NAME_TO_CODE_BY_COUNTRY.get(country_key, {})
    mapped_code = name_to_code.get(name_key)
    if mapped_code is not None and mapped_code in country_boxes:
        return dict(country_boxes[mapped_code])

    return None


def resolve_geo_anchor_detailed(
    *, country_code: str | None, state_code: str | None, state_name: str | None
) -> dict[str, Any]:
    """Full resolution result for diagnostics (Task 7) and for callers that
    want to know *how* a bbox was resolved, not just the bbox itself.

    Tries the cached, dataset-backed boundary (``maps_geo_boundaries``)
    first; falls back to this module's static table; returns an
    unresolved result (``bounds=None``) when neither has coverage. Never
    raises — a cache-read or geometry error is treated the same as "not
    available" so a bad/corrupt cache file can never take down a run.
    """
    try:
        record = maps_geo_boundaries.resolve_region_boundary(
            country_code=country_code, state_code=state_code, state_name=state_name
        )
    except Exception:  # noqa: BLE001
        record = None

    if record is not None:
        try:
            bbox = maps_geo_boundaries.bounding_box(record)
        except Exception:  # noqa: BLE001
            bbox = None
        if bbox is not None:
            return {
                "bounds": bbox,
                "source": record.source,
                "source_license": record.license,
                "source_boundary_id": record.boundary_id,
                "polygon_available": True,
            }

    static_bbox = resolve_geo_anchor(
        country_code=country_code, state_code=state_code, state_name=state_name
    )
    if static_bbox is not None:
        return {
            "bounds": static_bbox,
            "source": "static_reference_table",
            "source_license": None,
            "source_boundary_id": None,
            "polygon_available": False,
        }

    return {
        "bounds": None,
        "source": None,
        "source_license": None,
        "source_boundary_id": None,
        "polygon_available": False,
    }


def known_regions_for_country(country_code: str | None) -> list[str]:
    """State/province codes with a trusted bounding box for this country —
    for diagnostics/reporting (e.g. "is this country/state combination ever
    going to get geographic cells"), not used in the discovery path itself.
    """
    country_key = (country_code or "").strip().upper()
    return sorted(_STATE_BOUNDING_BOXES_BY_COUNTRY.get(country_key, {}).keys())
