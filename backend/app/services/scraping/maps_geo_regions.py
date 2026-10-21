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
table of well-established, publicly published land-area bounding boxes
(the kind printed in any standard atlas or gazetteer — not computed,
geocoded, or invented) for the specific U.S. states this product has
actually run against so far. ``resolve_geo_anchor`` returns ``None`` for
anything not in the table — callers must treat that as "no geographic
resolution available" and fall back to the existing text-only search, never
claim geographic coverage they don't have.

Adding a state is a one-line, reviewable addition of its standard bounding
box — no code changes, no new dependency, no network call.
"""

from __future__ import annotations

# Standard land-area bounding boxes (degrees), the extents commonly published
# for each state (e.g. the ones summarized on government/GIS reference
# pages), padded by ~0.05-0.1 degrees to comfortably include border areas
# without claiming false precision. These are reference geography, not
# measurements taken by this codebase.
_US_STATE_BOUNDING_BOXES: dict[str, dict[str, float]] = {
    "AL": {"north": 35.08, "south": 30.14, "east": -84.85, "west": -88.53},
}

_US_STATE_NAME_TO_CODE: dict[str, str] = {
    "alabama": "AL",
}


def resolve_geo_anchor(
    *, country_code: str | None, state_code: str | None, state_name: str | None
) -> dict[str, float] | None:
    """Return a trusted bounding box for the run's country/state, or
    ``None`` when none is known. Only ``country_code == "US"`` is supported
    today — this does not attempt to anchor any other country, by design,
    rather than guess at a bounding box for one.
    """
    if (country_code or "").strip().upper() != "US":
        return None

    code = (state_code or "").strip().upper()
    if code in _US_STATE_BOUNDING_BOXES:
        return dict(_US_STATE_BOUNDING_BOXES[code])

    name_key = (state_name or "").strip().casefold()
    mapped_code = _US_STATE_NAME_TO_CODE.get(name_key)
    if mapped_code is not None:
        return dict(_US_STATE_BOUNDING_BOXES[mapped_code])

    return None
