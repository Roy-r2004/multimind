"""Generic CLI for app.services.scraping.maps_manual_evidence_correction —
apply a single, documented, source-cited correction to ONE place record.

This script itself contains no facility- or country-specific logic; the
actual field/value/source data for a given correction is supplied via
--correction JSON arguments (or edit CORRECTIONS below for a one-off run).
Dry-run (the default) only ever reads; --apply writes, transactionally,
exactly once.

Usage (from backend/):
  python -m scripts.correct_place_evidence <place_id> --note "..." \\
      --correction '{"field": "facility_type", "new_value": "residential_addiction_rehab", \\
                      "evidence_quote": "...", "source_url": "https://..."}' \\
      [--correction '{...}' ...] \\
      [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.db.session import AsyncSessionLocal
from app.services.scraping.maps_evidence_backfill import PlaceBackfillPlan
from app.services.scraping.maps_manual_evidence_correction import (
    ManualFieldCorrection,
    apply_manual_correction,
    preview_manual_correction,
)


def _print_plan(plan: PlaceBackfillPlan, *, applied: bool) -> None:
    verb = "CHANGED" if applied else "WOULD CHANGE"
    print(f"\n{plan.name} (id={plan.place_id})")
    if not plan.changes:
        print("  No changes — every requested value already matches the stored value.")
        return
    for change in plan.changes:
        print(f"  [{verb}] {change.field}: {change.old_value!r} -> {change.new_value!r}")
    if plan.eligibility_before != plan.eligibility_after:
        print(f"  eligibility: {plan.eligibility_before!r} -> {plan.eligibility_after!r}")
    else:
        print(f"  eligibility: unchanged ({plan.eligibility_before!r})")


async def main_async(place_id: str, corrections: list[ManualFieldCorrection], *, apply: bool, note: str) -> None:
    if not apply:
        plan = await preview_manual_correction(AsyncSessionLocal, place_id=place_id, corrections=corrections)
        _print_plan(plan, applied=False)
        print("\nDRY RUN — no changes made, no AI calls made. Pass --apply to execute.")
        return

    result = await apply_manual_correction(
        AsyncSessionLocal, place_id=place_id, corrections=corrections, reviewer_note=note
    )
    _print_plan(result["plan"], applied=True)
    print("\nApplied and committed in one transaction. No AI calls were made.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("place_id")
    parser.add_argument(
        "--correction",
        action="append",
        default=[],
        help='JSON object: {"field": ..., "new_value": ..., "evidence_quote": ..., "source_url": ...}',
    )
    parser.add_argument("--note", default="", help="Reviewer note recorded in the audit log.")
    parser.add_argument("--apply", action="store_true", help="Write the correction. Default is dry-run only.")
    args = parser.parse_args()

    corrections = [ManualFieldCorrection(**json.loads(raw)) for raw in args.correction]
    if not corrections:
        raise SystemExit("At least one --correction is required.")

    asyncio.run(main_async(args.place_id, corrections, apply=args.apply, note=args.note))


if __name__ == "__main__":
    main()
