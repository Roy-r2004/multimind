"""Unit tests for capped-cell subdivision strategies (Phase 2 gap #2)."""

from __future__ import annotations

from app.services.scraping.maps_cell_subdivision import (
    MAX_CHILD_CELLS_PER_SUBDIVISION,
    ChildCellSpec,
    subdivide_cell,
)


def test_subdivide_cell_uses_alternate_query_family_terms_first():
    country_profile = {
        "provider_terms": {
            "clinic": ["addiction clinic"],
            "rehab": ["rehab center", "treatment center"],
        }
    }
    children = subdivide_cell(
        region_name="Ile-de-France",
        city_name="Paris",
        query_text="addiction clinic Paris",
        query_family="clinic",
        query_language="en",
        country_profile=country_profile,
        max_children=1,
    )

    assert len(children) == 1
    assert children[0].expansion_reason == "alternate_query_family"
    assert children[0].query_family == "rehab"
    assert "rehab center" in children[0].query_text
    assert "Paris" in children[0].query_text


def test_subdivide_cell_additional_local_term_same_family():
    country_profile = {"provider_terms": {"rehab": ["rehab center", "treatment center", "recovery house"]}}
    children = subdivide_cell(
        region_name="Ile-de-France",
        city_name="Paris",
        query_text="rehab center Paris",
        query_family="rehab",
        country_profile=country_profile,
        max_children=10,
    )

    reasons = [c.expansion_reason for c in children]
    assert "additional_local_term" in reasons
    additional_terms = {c.query_text for c in children if c.expansion_reason == "additional_local_term"}
    assert any("treatment center" in t for t in additional_terms)


def test_subdivide_cell_generic_locality_qualifiers_are_country_agnostic():
    children = subdivide_cell(
        region_name="Some Region",
        city_name="Some City",
        query_text="rehab center Some City",
        max_children=10,
    )

    city_subdivision = [c for c in children if c.expansion_reason == "city_subdivision"]
    assert city_subdivision
    for child in city_subdivision:
        assert child.query_text.startswith("rehab center Some City ")
        # No hardcoded country-specific vocabulary — only generic directional/
        # locality qualifiers.
        assert any(
            child.query_text.endswith(q)
            for q in ("north", "south", "east", "west", "downtown", "suburbs")
        )


def test_subdivide_cell_smaller_city_variant_narrows_to_locality():
    children = subdivide_cell(
        region_name="Ile-de-France",
        city_name="Paris",
        query_text="rehab center Paris",
        max_children=100,
    )

    smaller_city = [c for c in children if c.expansion_reason == "smaller_city_variant"]
    assert len(smaller_city) == 1
    assert smaller_city[0].query_text == "Paris"


def test_subdivide_cell_viewport_subdivision_splits_into_quadrants():
    bounds = {"north": 49.0, "south": 48.0, "east": 3.0, "west": 2.0}
    children = subdivide_cell(
        region_name="Ile-de-France",
        city_name="Paris",
        query_text="rehab center Paris",
        viewport_bounds=bounds,
        max_children=100,
    )

    quadrant_children = [c for c in children if c.expansion_reason == "viewport_subdivision"]
    assert len(quadrant_children) == 4
    # All four quadrants reuse the same query text — the bounds differentiate them.
    assert {c.query_text for c in quadrant_children} == {"rehab center Paris"}
    seen_bounds = [tuple(sorted(c.viewport_bounds.items())) for c in quadrant_children]
    assert len(set(seen_bounds)) == 4
    for child in quadrant_children:
        assert child.viewport_bounds["north"] <= 49.0
        assert child.viewport_bounds["south"] >= 48.0


def test_subdivide_cell_respects_max_children_cap():
    country_profile = {
        "provider_terms": {
            "clinic": ["addiction clinic"],
            "rehab": ["rehab center"],
            "hospital": ["psychiatric hospital"],
        }
    }
    bounds = {"north": 49.0, "south": 48.0, "east": 3.0, "west": 2.0}
    children = subdivide_cell(
        region_name="Ile-de-France",
        city_name="Paris",
        query_text="addiction clinic Paris",
        query_family="clinic",
        country_profile=country_profile,
        viewport_bounds=bounds,
        max_children=3,
    )

    assert len(children) == 3


def test_subdivide_cell_default_cap_matches_module_constant():
    country_profile = {
        "provider_terms": {
            "clinic": ["addiction clinic"],
            "rehab": ["rehab center"],
        }
    }
    bounds = {"north": 49.0, "south": 48.0, "east": 3.0, "west": 2.0}
    children = subdivide_cell(
        region_name="Ile-de-France",
        city_name="Paris",
        query_text="addiction clinic Paris",
        query_family="clinic",
        country_profile=country_profile,
        viewport_bounds=bounds,
    )

    assert len(children) <= MAX_CHILD_CELLS_PER_SUBDIVISION


def test_subdivide_cell_avoids_duplicating_existing_query_texts():
    country_profile = {"provider_terms": {"rehab": ["rehab center"]}}
    children = subdivide_cell(
        region_name="Ile-de-France",
        city_name="Paris",
        query_text="rehab center Paris",
        query_family="clinic",
        country_profile=country_profile,
        existing_query_texts={"rehab center Paris"},
        max_children=10,
    )

    query_texts = {c.query_text.casefold() for c in children}
    assert "rehab center paris" not in query_texts


def test_subdivide_cell_returns_child_cell_spec_instances():
    children = subdivide_cell(
        region_name="Ile-de-France",
        city_name="Paris",
        query_text="rehab center Paris",
        max_children=1,
    )
    assert all(isinstance(c, ChildCellSpec) for c in children)


def test_subdivide_cell_handles_missing_city_name():
    children = subdivide_cell(
        region_name="Ile-de-France",
        city_name=None,
        query_text="rehab center region",
        max_children=10,
    )
    smaller = [c for c in children if c.expansion_reason == "smaller_city_variant"]
    assert len(smaller) == 1
    assert smaller[0].query_text == "Ile-de-France"


def test_subdivide_cell_no_strategies_available_returns_empty():
    """No country profile, no viewport, and a city name that already
    collapses into the smaller-city-variant dedup key: only the generic
    locality qualifiers should fire."""
    children = subdivide_cell(
        region_name="X",
        city_name="X",
        query_text="rehab X",
        existing_query_texts={"x"},
        max_children=10,
    )
    reasons = {c.expansion_reason for c in children}
    assert reasons == {"city_subdivision"}
