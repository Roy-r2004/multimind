"""Sonar web-search classification for unresolved Maps places (classify-only, no addictions)."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import get_settings
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import get_provider_registry
from app.services.scraping.maps_enrichment_fetch import EnrichmentFetchError, cap_payload_excerpt
from app.services.scraping.maps_enrichment_response_parser import EnrichmentParseStats
from app.services.scraping.maps_place_enrichment_service import MapsPlaceEnrichmentResult

logger = logging.getLogger(__name__)


def needs_sonar_classification(place: Any) -> bool:
    """True when structured classification left important fields unresolved."""
    from app.services.scraping.maps_enrichment_selection import CONFIDENT_SKIP_LIFECYCLE

    lifecycle = getattr(place, "lifecycle_status", None) or ""
    if lifecycle in CONFIDENT_SKIP_LIFECYCLE:
        return False

    facility_type = getattr(place, "facility_type", None) or ""
    ownership = getattr(place, "ownership_status", None) or ""
    addiction_focus = getattr(place, "addiction_focus_confirmed", None)
    confidence = getattr(place, "classification_confidence", None)
    threshold = get_settings().maps_primary_extraction_confidence_threshold

    if not facility_type or facility_type == "unknown":
        return True
    if not ownership or ownership == "ownership_unknown":
        return True
    if addiction_focus is None:
        return True
    if confidence is not None and confidence < threshold:
        return True
    return False


async def fetch_sonar_classify_one(
    *,
    country_code: str,
    country_name: str,
    payload: dict[str, Any],
    parse_stats: EnrichmentParseStats,
) -> MapsPlaceEnrichmentResult:
    settings = get_settings()
    model = get_model(settings.maps_census_enrichment_model)
    provider = get_provider_registry().get_provider(model.provider)
    capped = cap_payload_excerpt(
        payload,
        max_chars=max(1, int(settings.maps_census_enrichment_max_crawl_excerpt_chars)),
    )
    prompt = get_prompt_engine().render(
        "scraping/maps_sonar_classifier.j2",
        country_code=(country_code or "XX")[:2].upper(),
        country_name=(country_name or "Unknown")[:120],
        facility_json=json.dumps(capped, ensure_ascii=False),
    )
    try:
        response = await provider.complete(
            system=(
                "You have live web search. Classify one addiction-treatment facility. "
                "Classification only — no addictions or languages. Return JSON only."
            ),
            user=prompt,
            model=model.provider_model,
            max_tokens=4096,
        )
    except Exception as exc:
        raise EnrichmentFetchError(str(exc)) from exc

    from app.services.scraping.maps_enrichment_response_parser import (
        parse_and_validate_enrichment_response,
        record_parse_failure,
    )

    text = response.text or ""
    try:
        batch = parse_and_validate_enrichment_response(text)
    except Exception as exc:
        record_parse_failure(parse_stats, error=exc, raw_text=text, attempt="sonar_classify")
        raise EnrichmentFetchError(str(exc), raw_excerpt=text[:500]) from exc

    if not batch.results:
        raise EnrichmentFetchError("sonar classify returned no results")
    result = batch.results[0]
    if not result.place_id and payload.get("place_id"):
        result.place_id = str(payload["place_id"])
    result.addictions_treated = []
    result.languages_spoken = []
    return result


__all__ = ["fetch_sonar_classify_one", "needs_sonar_classification"]
