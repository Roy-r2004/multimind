"""Multi-location brand disaggregation: when a kept facility's own website
reveals other physical inpatient/residential locations operated by the same
organization in the same country, extract each as its own candidate and route
it through the same gate everything else goes through — the strict keep/drop
pass. A sibling is never auto-kept because its parent facility was.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
)
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider

logger = logging.getLogger(__name__)

SIBLING_EXTRACTION_SOURCE = "sibling_extraction"

# Cheap, free pre-filter — only pay for the LLM call when the crawled text
# actually hints at more than one physical location. Deliberately broad
# (false positives just cost one extra call that returns an empty list;
# false negatives silently miss real chains, which is the failure mode
# this whole feature exists to close).
_MULTI_LOCATION_SIGNAL_PATTERN = re.compile(
    r"\b("
    r"our\s+locations?|our\s+centers?|our\s+centres?|find\s+a\s+location|"
    r"where\s+we\s+are|locations?\s+nationwide|network\s+of\s+treatment|"
    r"our\s+facilities|treatment\s+centers?|treatment\s+centres?|branches?"
    r")\b",
    re.IGNORECASE,
)

# location_type values that never represent an inpatient/residential candidate —
# filtered out before a MapsPlace row is ever created for them.
_NON_CANDIDATE_LOCATION_TYPES = frozenset(
    {"headquarters", "administrative_office", "outpatient_only"}
)


def contains_multi_location_signal(text: str | None) -> bool:
    if not text:
        return False
    return bool(_MULTI_LOCATION_SIGNAL_PATTERN.search(text))


class SiblingLocationEvidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str = Field(default="website", max_length=20)
    text: str = Field(default="", max_length=500)
    url: str | None = Field(default=None, max_length=1024)


class SiblingLocationCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    facility_name: str = Field(default="", max_length=512)
    full_physical_address: str | None = Field(default=None, max_length=512)
    city: str | None = Field(default=None, max_length=160)
    region: str | None = Field(default=None, max_length=160)
    location_specific_url: str | None = Field(default=None, max_length=512)
    phone_number: str | None = Field(default=None, max_length=64)
    location_type: str = Field(default="unknown", max_length=32)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[SiblingLocationEvidence] = Field(default_factory=list)

    def is_candidate_location(self) -> bool:
        if not self.facility_name.strip():
            return False
        if self.location_type in _NON_CANDIDATE_LOCATION_TYPES:
            return False
        # Need at least a city or a full address to be worth a keep/drop call —
        # otherwise there's nothing for the crawler/classifier to act on.
        return bool((self.full_physical_address or "").strip() or (self.city or "").strip())


class SiblingLocationBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[SiblingLocationCandidate] = Field(default_factory=list)


async def extract_sibling_locations(
    provider: Any,
    *,
    model_slug: str,
    facility_payload: dict[str, Any],
    crawl_excerpt: str | None,
    country_code: str,
    country_name: str,
) -> list[SiblingLocationCandidate]:
    """One LLM call: does this facility's own organization run other physical
    locations in-country that we haven't already discovered? Returns an empty
    list for the ordinary case (single-location org, or nothing new found)."""
    payload = dict(facility_payload)
    if crawl_excerpt:
        payload["website_crawl_excerpt"] = crawl_excerpt
    prompt = get_prompt_engine().render(
        "scraping/maps_sibling_location_extraction.j2",
        country_code=(country_code or "XX")[:2].upper(),
        country_name=(country_name or "Unknown")[:120],
        facility_json=json.dumps(payload, ensure_ascii=False),
        crawl_excerpt=crawl_excerpt or "",
    )
    response = await provider.complete(
        system=(
            "You have live web search. Find other physical locations of the same "
            "organization, if any exist. Return strict JSON only."
        ),
        user=prompt,
        model=model_slug,
        max_tokens=1536,
    )
    raw = LLMProvider.parse_json_response(response.text or "")
    batch = SiblingLocationBatch.model_validate(raw)
    return [c for c in batch.results if c.is_candidate_location()]


def _normalize_for_dedup(name: str, city: str | None) -> str:
    slug = re.sub(r"[^a-z0-9]+", " ", (name or "").casefold()).strip()
    city_slug = re.sub(r"[^a-z0-9]+", " ", (city or "").casefold()).strip()
    return f"{slug}|{city_slug}"


async def _is_duplicate_of_existing_place(
    session: AsyncSession, *, run_id: str, candidate: SiblingLocationCandidate
) -> bool:
    existing = (
        (await session.execute(select(MapsPlace).where(MapsPlace.run_id == run_id)))
        .scalars()
        .all()
    )
    candidate_key = _normalize_for_dedup(candidate.facility_name, candidate.city)
    for place in existing:
        existing_key = _normalize_for_dedup(place.canonical_name or place.raw_name, place.city_name)
        if candidate_key == existing_key:
            return True
        # Same city + one name contains the other (e.g. "Recovery Group" vs
        # "Recovery Group Manchester") — treat as the same physical listing
        # rather than risk a duplicate keep/drop call on the identical place.
        if (
            candidate.city
            and place.city_name
            and candidate.city.strip().casefold() == place.city_name.strip().casefold()
        ):
            a, b = candidate.facility_name.strip().casefold(), (place.canonical_name or "").strip().casefold()
            if a and b and (a in b or b in a):
                return True
    return False


async def create_sibling_candidates(
    session_factory,
    *,
    run_id: str,
    parent_place: MapsPlace,
    candidates: list[SiblingLocationCandidate],
) -> int:
    """Insert one MapsPlace per genuinely-new sibling, in the same "ready for
    keep/drop" state reopen_for_keep_drop already uses elsewhere — real
    eligibility (private/non-government/inpatient) still gets decided by the
    strict gate, exactly like every other candidate. Returns the count created."""
    if not candidates:
        return 0
    created = 0
    async with session_factory() as session:
        for candidate in candidates:
            if await _is_duplicate_of_existing_place(session, run_id=run_id, candidate=candidate):
                continue
            digest = hashlib.sha1(
                f"{parent_place.id}:{candidate.facility_name}:{candidate.city or ''}".encode()
            ).hexdigest()[:24]
            synthetic_id = f"sibling:{digest}"
            already = (
                await session.execute(
                    select(MapsPlace).where(
                        MapsPlace.run_id == run_id, MapsPlace.google_place_id == synthetic_id
                    )
                )
            ).scalar_one_or_none()
            if already is not None:
                continue
            evidence_reason = candidate.evidence[0].text[:300] if candidate.evidence else None
            place = MapsPlace(
                run_id=run_id,
                google_place_id=synthetic_id,
                raw_name=candidate.facility_name[:512],
                canonical_name=candidate.facility_name[:512],
                place_types=[],
                formatted_address=(candidate.full_physical_address or None),
                city_name=(candidate.city or None),
                region_name=(candidate.region or None),
                international_phone_number=(candidate.phone_number or None),
                raw_website=(candidate.location_specific_url or None),
                is_relevant=True,
                relevance_reason=(
                    evidence_reason
                    or f"Sibling location of {parent_place.canonical_name or parent_place.raw_name}"
                ),
                confidence_score=candidate.confidence,
                discovered_via_query=f"sibling_of:{(parent_place.canonical_name or parent_place.raw_name or '')[:250]}",
                lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
                client_eligibility=MapsClientEligibility.REVIEW.value,
                enrichment_status=MapsPlaceEnrichmentStatus.PENDING.value,
                discovery_sources=[SIBLING_EXTRACTION_SOURCE],
                source_record_ids=[parent_place.id],
            )
            session.add(place)
            created += 1
        if created:
            await session.commit()
    return created


async def reopen_run_for_new_candidates(session_factory, *, run_id: str, created_count: int) -> None:
    """A run already marked completed can have genuinely new, undecided work
    the moment a sibling extraction finds something — flip it back to running
    (rather than leaving it silently "completed" with pending candidates, the
    same inconsistency that corrupted Andorra) and re-enqueue keep/drop so the
    new rows actually get judged."""
    if created_count <= 0:
        return
    async with session_factory() as session:
        run = await session.get(MapsCensusRun, run_id)
        if run is None:
            return
        if run.status in {MapsCensusStatus.COMPLETED, MapsCensusStatus.COMPLETED_WITH_WARNINGS}:
            run.status = MapsCensusStatus.RUNNING
        run.places_found = (run.places_found or 0) + created_count
        await session.commit()

    from app.services.scraping.maps_census_service import maps_census_service
    from app.services.scraping.maps_keep_drop_service import run_maps_keep_drop_job

    await maps_census_service._enqueue_job(
        "run_maps_keep_drop_job",
        run_id,
        inline_runner=lambda: run_maps_keep_drop_job({}, run_id),
    )
