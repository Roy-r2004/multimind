"""Regression tests for the Finland Phase 2 "0 eligible" investigation
(run_id 0afe4ca7-2dd3-47aa-a2cd-566e6b19b208).

Three independent root causes were found, each fixed here:

1. ``resolve_official_website`` let an unconfigured/failing search provider
   (``SERPER_API_KEY`` unset in the running environment) propagate an
   uncaught exception out of ``_classify_place``. That burned both retry
   attempts on every place without an existing website and permanently
   parked it at ``needs_review`` / ``enrichment_pipeline_state=needs_review``
   with every classification field still empty — never even reaching the AI
   classification call. This was the fate of 45 of the run's 64 relevant
   places.

2. ``_enforce_field_evidence`` required BOTH a quote AND a ``source_url``
   for the mandatory classification fields (facility_type, ownership_status,
   operator_type, organization_scope) before keeping a non-"unknown" value.
   Legitimate evidence for these fields routinely comes from the Google
   Maps listing itself (name, place_types, address) with no crawled website
   to cite a URL from, so a correct, quote-backed classification was
   silently wiped back to empty whenever no website was crawled. This
   explains the surviving 19 "classification_completed" places keeping
   ownership_status (grounded in metadata the model could quote without a
   URL) far more often than facility_type.

3. Finland had no dataset-backed ADM0 boundary cached locally, so every
   place in the run got ``geo_boundary_match="no_polygon_available"`` —
   never "outside_polygon" — so a Pompano Beach, FL result was never
   downgraded despite being nowhere near Finland. A small, reviewable
   country-level bounding-box fallback (the same kind of static-table
   mechanism already used for a few US states) closes that gap.
"""

from __future__ import annotations

import pytest

from app.services.scraping import maps_geo_regions
from app.services.scraping.maps_place_enrichment_service import (
    ClassificationEvidenceField,
    MapsPlaceEnrichmentResult,
    _enforce_field_evidence,
)
from app.services.scraping.maps_place_website_resolution import resolve_official_website
from app.services.scraping.search_providers.base import (
    SearchProviderConfigurationError,
    SearchProviderRateLimitedError,
)


class _FakePlace:
    """Minimal stand-in for MapsPlace, avoiding a DB round trip."""

    id = "place-1"
    official_website = None
    raw_website = None
    website_source_candidates = None
    classification_evidence = None
    canonical_name = "Quiet Residential Rehab"
    raw_name = "Quiet Residential Rehab"
    city_name = "Helsinki"
    international_phone_number = None


# --- Fix 1: a failing/unconfigured search provider must never crash
# classification — it degrades to "no website found", exactly like a
# search that genuinely found nothing. ---


@pytest.mark.asyncio
async def test_resolve_official_website_degrades_when_search_provider_unconfigured(monkeypatch):
    def _raise_configuration_error():
        raise SearchProviderConfigurationError("SERPER_API_KEY is not configured")

    monkeypatch.setattr(
        "app.services.scraping.maps_place_website_resolution.create_search_provider",
        _raise_configuration_error,
    )

    outcome = await resolve_official_website(
        _FakePlace(), country_code="FI", country_name="Finland", enable_search=True
    )

    assert outcome.official_website is None
    assert outcome.website_relationship_evidence.get("reason") == "no_official_website_found"


@pytest.mark.asyncio
async def test_resolve_official_website_degrades_on_any_search_provider_error(monkeypatch):
    """Not just the configuration-missing case — auth failures, rate
    limiting, timeouts, and provider-unavailable errors are all equally
    "search could not run right now", never a reason to fail the place."""

    def _raise_rate_limited():
        raise SearchProviderRateLimitedError("rate limited")

    monkeypatch.setattr(
        "app.services.scraping.maps_place_website_resolution.create_search_provider",
        _raise_rate_limited,
    )

    outcome = await resolve_official_website(
        _FakePlace(), country_code="FI", country_name="Finland", enable_search=True
    )
    assert outcome.official_website is None


# --- Fix 2: mandatory classification fields keep a quote-backed value even
# with no source_url (Maps-metadata-grounded evidence), but still drop a
# value with no quote at all (the real anti-hallucination guard). ---


def _result_with_evidence(**field_overrides) -> MapsPlaceEnrichmentResult:
    base = dict(
        place_id="p1",
        operator_type="nonprofit",
        ownership_status="confirmed_non_government",
        facility_type="residential_addiction_rehab",
        organization_scope="facility",
    )
    base.update(field_overrides)
    return MapsPlaceEnrichmentResult(**base)


def test_facility_type_survives_with_quote_but_no_source_url():
    result = _result_with_evidence(
        classification_evidence={
            "facility_type": ClassificationEvidenceField(
                value="residential_addiction_rehab",
                evidence_quote="Listed as a residential rehabilitation center on Google Maps.",
                source_url="",
            ),
            "ownership_status": ClassificationEvidenceField(
                value="confirmed_non_government",
                evidence_quote="Registered as a nonprofit association (ry).",
                source_url="",
            ),
            "operator_type": ClassificationEvidenceField(
                value="nonprofit",
                evidence_quote="Registered as a nonprofit association (ry).",
                source_url="",
            ),
            "organization_scope": ClassificationEvidenceField(
                value="facility",
                evidence_quote="Single facility location, not an individual practice.",
                source_url="",
            ),
        }
    )
    _enforce_field_evidence(result)
    assert result.facility_type == "residential_addiction_rehab"
    assert result.ownership_status == "confirmed_non_government"
    assert result.operator_type == "nonprofit"
    assert result.organization_scope == "facility"


def test_facility_type_dropped_when_quote_is_missing_entirely():
    """The real anti-hallucination guard: no evidence record at all, or an
    empty quote, must still strip the value — this must keep working."""
    result = _result_with_evidence(classification_evidence={})
    _enforce_field_evidence(result)
    assert result.facility_type == ""
    assert result.ownership_status == ""

    result_empty_quote = _result_with_evidence(
        classification_evidence={
            "facility_type": ClassificationEvidenceField(
                value="residential_addiction_rehab", evidence_quote="", source_url=""
            ),
        }
    )
    _enforce_field_evidence(result_empty_quote)
    assert result_empty_quote.facility_type == ""


def test_unknown_values_never_require_evidence():
    result = _result_with_evidence(
        facility_type="unknown",
        ownership_status="ownership_unknown",
        operator_type="unknown",
        organization_scope="unknown",
        classification_evidence={},
    )
    _enforce_field_evidence(result)
    assert result.facility_type == "unknown"
    assert result.ownership_status == "ownership_unknown"


# --- Fix 3: a country with no cached dataset-backed boundary still gets a
# coarse bounding-box check instead of silently skipping geography
# entirely. ---


def test_finland_country_level_bounding_box_fallback_resolves():
    box = maps_geo_regions.resolve_geo_anchor(country_code="FI", state_code=None, state_name=None)
    assert box is not None
    assert box["south"] < 60.5 < box["north"]


def test_country_without_any_table_entry_still_resolves_to_none():
    box = maps_geo_regions.resolve_geo_anchor(country_code="ZZ", state_code=None, state_name=None)
    assert box is None


def test_resolve_geo_anchor_detailed_reports_static_source_for_country_fallback(monkeypatch):
    from app.services.scraping import maps_geo_boundaries

    monkeypatch.setattr(
        maps_geo_boundaries, "resolve_region_boundary", lambda **_kwargs: None
    )
    detail = maps_geo_regions.resolve_geo_anchor_detailed(
        country_code="FI", state_code=None, state_name=None
    )
    assert detail["bounds"] is not None
    assert detail["polygon_available"] is False
    assert detail["source"] == "static_reference_table"


def test_pompano_beach_florida_falls_outside_finland_country_bbox():
    """The exact false-positive this run exhibited: given real coordinates
    for Pompano Beach, FL, a simple bounding-box check (even without a full
    polygon) correctly flags it as outside Finland."""
    box = maps_geo_regions.resolve_geo_anchor(country_code="FI", state_code=None, state_name=None)
    pompano_lat, pompano_lon = 26.2379, -80.1248
    inside = (
        box["south"] <= pompano_lat <= box["north"]
        and box["west"] <= pompano_lon <= box["east"]
    )
    assert inside is False


def test_bbox_fallback_outcome_is_labeled_distinctly_from_a_real_polygon():
    """Correction after the live Finland dry run: the bbox fallback must
    never reuse "inside_polygon"/"outside_polygon" — those imply a
    verified national-border polygon was checked, which a plain rectangle
    is not. compute_client_eligibility must downgrade on either label."""
    from app.services.scraping.maps_census_service import _geo_boundary_match

    outside = _geo_boundary_match(
        None, latitude=26.2379, longitude=-80.1248, country_code="FI"
    )
    inside = _geo_boundary_match(None, latitude=60.17, longitude=24.94, country_code="FI")
    assert outside == "outside_bbox_fallback"
    assert inside == "inside_bbox_fallback"
    assert outside not in {"outside_polygon", "inside_polygon"}


def test_outside_bbox_fallback_downgrades_eligibility_same_as_outside_polygon():
    from app.services.scraping.maps_eligibility import compute_client_eligibility

    from types import SimpleNamespace

    base = SimpleNamespace(
        ownership_status="confirmed_non_government",
        organization_scope="facility",
        facility_type="residential_addiction_rehab",
        addiction_focus_confirmed=True,
        operating_status="open",
    )
    base.geo_boundary_match = "outside_bbox_fallback"
    assert compute_client_eligibility(base) == "review"
    base.geo_boundary_match = "outside_polygon"
    assert compute_client_eligibility(base) == "review"
    base.geo_boundary_match = "inside_bbox_fallback"
    assert compute_client_eligibility(base) == "eligible"
