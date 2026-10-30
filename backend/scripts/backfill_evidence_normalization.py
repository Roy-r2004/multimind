"""Safe, idempotent, zero-AI-call evidence-normalization backfill.

Dry-run by default — prints the EXACT per-facility changes that would be
made (field, old value, new value, eligibility before/after) without
writing anything. Pass --apply to actually write them, in one
transaction, in the same session that computed the plan.

See app.services.scraping.maps_evidence_backfill for the full safety
properties (transactional, idempotent, scope-limited, auditable) and
app.services.scraping.maps_evidence_normalization for the root causes
this recovers from (enum-normalization gap, organization-wide evidence
leaking into branch-specific fields).

This script:
  - Never calls an AI provider (no import of any classification/keep-drop
    provider at all).
  - Never touches Gosom, Phase 1 discovery, or the keep/drop decision.
  - Never touches a manually-excluded place or a confident keep/drop
    "drop" — those are excluded by the candidate query itself, not by a
    runtime check in this script.

Usage (from backend/):
  python -m scripts.backfill_evidence_normalization <run_id>           # dry run
  python -m scripts.backfill_evidence_normalization <run_id> --apply    # write
"""

from __future__ import annotations

import argparse
import asyncio

from app.db.session import AsyncSessionLocal
from app.services.scraping.maps_evidence_backfill import (
    PlaceBackfillPlan,
    apply_backfill,
    build_backfill_plan,
)


def _print_plan(plans: list[PlaceBackfillPlan], *, applied: bool) -> None:
    verb = "CHANGED" if applied else "WOULD CHANGE"
    for plan in plans:
        print(f"\n{plan.name} (id={plan.place_id})")
        for change in plan.changes:
            print(f"  [{verb}] {change.field}: {change.old_value!r} -> {change.new_value!r}")
        if plan.eligibility_before != plan.eligibility_after:
            print(f"  eligibility: {plan.eligibility_before!r} -> {plan.eligibility_after!r}")
        else:
            print(f"  eligibility: unchanged ({plan.eligibility_before!r})")


async def main_async(run_id: str, *, apply: bool) -> None:
    if not apply:
        plans = await build_backfill_plan(AsyncSessionLocal, run_id=run_id)
        print(f"run_id={run_id}")
        print(f"places with at least one recoverable/downgradeable field: {len(plans)}")
        total_field_changes = sum(len(p.changes) for p in plans)
        print(f"total individual field changes: {total_field_changes}")
        _print_plan(plans, applied=False)
        print(
            "\nDRY RUN — no changes made, no AI calls made. "
            "Pass --apply to execute this exact plan transactionally."
        )
        return

    result = await apply_backfill(AsyncSessionLocal, run_id=run_id)
    print(f"run_id={result['run_id']}")
    print(f"places changed: {result['places_changed']}")
    _print_plan(result["plans"], applied=True)
    print("\nApplied and committed in one transaction. No AI calls were made.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the backfill. Without this flag, only reports what would change.",
    )
    args = parser.parse_args()
    asyncio.run(main_async(args.run_id, apply=args.apply))


if __name__ == "__main__":
    main()
