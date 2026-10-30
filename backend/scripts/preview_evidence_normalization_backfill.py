"""Read-only preview of the evidence-normalization backfill.

Root cause (see maps_evidence_normalization.py's module docstring): the
structured classifier and primary extraction prompts never enumerated the
actual enum codes, so the AI often answered in natural language ("Private
company", "Private", "nonprofit") instead of the snake_case codes, and the
strict exact-match normalizer silently discarded it to NULL — even though
the underlying evidence was genuinely valid. A second bug let organization-
wide evidence (about a DIFFERENT branch of the same chain) wrongly confirm
residential_accommodation/inpatient_treatment for an unrelated location.

This script makes ZERO writes and ZERO AI calls. For every relevant place
in a run it reports, per field:
  - "already_set": the column already has a value — untouched either way.
  - "recoverable": the column is NULL/empty but stored evidence, run
    through the SAME normalization now used going forward, resolves to a
    specific value with no further AI call required.
  - "unresolvable": stored evidence exists but does not clear the bar
    (too generic, no synonym match, or a boolean whose evidence fails the
    branch-specificity/hedge-language check) — stays unresolved, not
    guessed.
  - "no_evidence": nothing stored for that field at all.

Also explicitly reports which places are excluded from any future backfill
and why: a manual admin exclusion (manually_excluded_at) or a confident
keep/drop rejection (keep_drop_decision == "drop") — these must never be
touched by a later backfill, preview or apply.

Usage (from backend/):
  python -m scripts.preview_evidence_normalization_backfill <run_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter

from sqlalchemy import select

from app.db.models import MapsCensusRun, MapsPlace
from app.db.session import AsyncSessionLocal
from app.services.scraping.maps_evidence_normalization import (
    OPERATOR_TYPE_SYNONYMS,
    ORGANIZATION_SCOPE_SYNONYMS,
    OWNERSHIP_STATUS_SYNONYMS,
    contains_hedge_language,
    is_likely_branch_specific_evidence,
    normalize_choice_with_synonyms,
    normalize_facility_type_with_keywords,
)
from app.services.scraping.maps_place_enrichment_service import (
    FACILITY_TYPE_VALUES,
    OPERATOR_TYPE_VALUES,
    ORGANIZATION_SCOPE_VALUES,
    OWNERSHIP_STATUS_VALUES,
)

_STRING_FIELDS: tuple[tuple[str, set[str], dict[str, str] | None], ...] = (
    ("operator_type", OPERATOR_TYPE_VALUES, OPERATOR_TYPE_SYNONYMS),
    ("ownership_status", OWNERSHIP_STATUS_VALUES, OWNERSHIP_STATUS_SYNONYMS),
    ("facility_type", FACILITY_TYPE_VALUES, None),  # keyword-based, not synonym-dict
    ("organization_scope", ORGANIZATION_SCOPE_VALUES, ORGANIZATION_SCOPE_SYNONYMS),
)

_BOOLEAN_FIELDS = ("residential_accommodation", "inpatient_treatment")


def _preview_string_field(
    place: MapsPlace, field_name: str, allowed: set[str], synonyms: dict[str, str] | None
) -> dict:
    current = getattr(place, field_name, None)
    evidence = (place.classification_evidence or {}).get(field_name) or {}
    raw_value = evidence.get("value")

    if current:
        return {"status": "already_set", "current": current}
    if not raw_value or not isinstance(raw_value, str):
        return {"status": "no_evidence"}

    if field_name == "facility_type":
        recovered = normalize_facility_type_with_keywords(
            raw_value,
            allowed,
            place=place,
            quote=evidence.get("evidence_quote") or "",
            source_url=evidence.get("source_url") or "",
        )
    else:
        recovered = normalize_choice_with_synonyms(raw_value, allowed, synonyms or {})

    if recovered:
        return {"status": "recoverable", "raw_value": raw_value, "recovered_value": recovered}
    return {"status": "unresolvable", "raw_value": raw_value}


def _preview_boolean_field(place: MapsPlace, field_name: str) -> dict:
    current = getattr(place, field_name, None)
    evidence = (place.classification_evidence or {}).get(field_name) or {}
    quote = evidence.get("evidence_quote") or ""
    source_url = evidence.get("source_url") or ""

    if current is not True:
        return {"status": "already_set", "current": current}

    if contains_hedge_language(quote):
        return {
            "status": "would_downgrade_hedged",
            "current": True,
            "evidence_quote": quote,
        }
    if not is_likely_branch_specific_evidence(place, quote=quote, source_url=source_url):
        return {
            "status": "would_downgrade_org_wide",
            "current": True,
            "evidence_quote": quote,
            "source_url": source_url,
        }
    return {"status": "confirmed_branch_specific", "current": True}


async def preview(run_id: str) -> None:
    async with AsyncSessionLocal() as db:
        run = await db.get(MapsCensusRun, run_id)
        if run is None:
            raise SystemExit(f"Run not found: {run_id}")

        all_places = (
            (await db.execute(select(MapsPlace).where(MapsPlace.run_id == run_id)))
            .scalars()
            .all()
        )

        excluded_manual = [p for p in all_places if p.manually_excluded_at is not None]
        excluded_dropped = [
            p
            for p in all_places
            if p.manually_excluded_at is None and p.keep_drop_decision == "drop"
        ]
        excluded_ids = {p.id for p in excluded_manual} | {p.id for p in excluded_dropped}

        candidates = [
            p
            for p in all_places
            if p.is_relevant
            and p.id not in excluded_ids
            and (p.keep_drop_decision in {"keep", "review"} or p.client_eligibility == "eligible")
        ]

        print(f"run_id={run_id} country={run.country_code} state={run.state_code}")
        print(f"total places: {len(all_places)}")
        print(
            f"excluded from backfill — manual exclusion: {len(excluded_manual)}, "
            f"confident drop: {len(excluded_dropped)} (never touched by any future apply)"
        )
        print(f"backfill candidates (relevant, keep/review, not excluded): {len(candidates)}\n")

        string_counts: dict[str, Counter] = {f[0]: Counter() for f in _STRING_FIELDS}
        boolean_counts: dict[str, Counter] = {f: Counter() for f in _BOOLEAN_FIELDS}
        recoverable_examples: list[dict] = []
        downgrade_examples: list[dict] = []

        for place in candidates:
            for field_name, allowed, synonyms in _STRING_FIELDS:
                result = _preview_string_field(place, field_name, allowed, synonyms)
                string_counts[field_name][result["status"]] += 1
                if result["status"] == "recoverable":
                    recoverable_examples.append(
                        {
                            "place": place.canonical_name or place.raw_name,
                            "field": field_name,
                            **result,
                        }
                    )
            for field_name in _BOOLEAN_FIELDS:
                result = _preview_boolean_field(place, field_name)
                boolean_counts[field_name][result["status"]] += 1
                if result["status"].startswith("would_downgrade"):
                    downgrade_examples.append(
                        {
                            "place": place.canonical_name or place.raw_name,
                            "city": place.city_name,
                            "field": field_name,
                            **result,
                        }
                    )

        print("=== String fields (operator_type, ownership_status, facility_type, organization_scope) ===")
        for field_name, _, _ in _STRING_FIELDS:
            counts = dict(string_counts[field_name])
            print(f"  {field_name}: {counts}")

        print("\n=== Boolean fields (residential_accommodation, inpatient_treatment) ===")
        for field_name in _BOOLEAN_FIELDS:
            counts = dict(boolean_counts[field_name])
            print(f"  {field_name}: {counts}")

        total_recoverable = sum(c["recoverable"] for c in string_counts.values())
        total_downgrades = sum(
            c.get("would_downgrade_hedged", 0) + c.get("would_downgrade_org_wide", 0)
            for c in boolean_counts.values()
        )
        print(f"\nTotal string-field values recoverable with zero AI calls: {total_recoverable}")
        print(f"Total boolean values that would be downgraded (unsupported True): {total_downgrades}")

        print("\n--- Recoverable field examples (first 20) ---")
        for ex in recoverable_examples[:20]:
            print(f"  {ex['place']!r} {ex['field']}: {ex['raw_value']!r} -> {ex['recovered_value']!r}")

        print("\n--- Would-downgrade examples (first 20) ---")
        for ex in downgrade_examples[:20]:
            print(
                f"  {ex['place']!r} (city={ex['city']}) {ex['field']}: "
                f"status={ex['status']} quote={ex.get('evidence_quote', '')[:80]!r}"
            )

        print(
            "\nThis was a READ-ONLY preview. No rows were changed, no AI calls were made. "
            "No --apply mode is implemented in this script — a backfill apply is a separate, "
            "explicitly-approved step."
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    args = parser.parse_args()
    asyncio.run(preview(args.run_id))


if __name__ == "__main__":
    main()
