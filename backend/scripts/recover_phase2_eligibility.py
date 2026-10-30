"""Classification-only recovery for places corrupted by two now-fixed bugs:

1. An unconfigured/failing website-search provider (SERPER_API_KEY) used to
   crash _classify_place before it ever reached the AI call, permanently
   parking the place at enrichment_pipeline_state=needs_review with every
   mandatory classification field still empty.
2. _enforce_field_evidence used to require a source_url (not just a quote)
   for the mandatory classification fields, silently wiping a legitimate
   Maps-metadata-grounded classification whenever no website was crawled.

Both are fixed in maps_place_website_resolution.py and
maps_place_enrichment_service.py. This script only resets the specific
places a given run left stranded in either broken state back to
PREFILTER_PENDING, then re-runs ONLY the mandatory eligibility
classification pass (_run_mandatory_eligibility_classification) over them.

Scope and safety:
- Scoped to one run_id and only to is_relevant places whose keep_drop
  decision already routed them to Phase 2 (keep/review) or that an admin
  already marked eligible.
- Never touches Phase 1 discovery, Gosom, the grid/cell plan, or the
  keep/drop decision itself.
- Never enqueues the OPTIONAL "Enrich with AI" detail cascade (addictions,
  languages, price, phone, email, bed count) — only the mandatory
  ownership/facility-type/residential/organization-scope evidence that
  compute_client_eligibility needs.
- Idempotent: the reset query (_affected_places_query) only matches places
  still stuck at client_eligibility=review with at least one mandatory
  field blank — see that function's docstring for why this, not "all four
  fields blank", is the correct and still-idempotent condition.
- Dry-run by default: prints exactly how many places would be reset and
  reclassified, and does not call the paid classifier, unless --apply is
  passed.

Also re-evaluates geo_boundary_match for every already-discovered place in
the run from its stored coordinates (no Gosom call, no rediscovery) — this
is where the Pompano Beach, FL / Finland false positive actually gets
caught, using whatever boundary data (cached polygon or the static
bounding-box fallback) is available now. Also dry-run by default.

Usage (from backend/):
  python -m scripts.recover_phase2_eligibility <run_id>              # dry run
  python -m scripts.recover_phase2_eligibility <run_id> --apply       # live
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from sqlalchemy import func, or_, select, update

from app.core.config import get_settings
from app.db.models import MapsCensusRun, MapsPlace
from app.db.session import AsyncSessionLocal
from app.services.scraping.maps_enrichment_processing_state import (
    MapsEnrichmentPipelineState,
)
from app.services.scraping.maps_keep_drop_service import KEEP, REVIEW
from app.services.scraping.maps_quota_tracker import MapsQuotaTracker

# The two known-broken states this recovery targets — nothing else.
_BROKEN_PIPELINE_STATES = (
    MapsEnrichmentPipelineState.NEEDS_REVIEW.value,
    MapsEnrichmentPipelineState.CLASSIFICATION_COMPLETED.value,
)


# Each mandatory field has its own "unknown" sentinel string (not a single
# shared literal — e.g. ownership_status's is "ownership_unknown", not
# "unknown") — this must be the same mapping _enforce_field_evidence uses,
# or this filter silently fails to recognize a field as blank.
_UNKNOWN_SENTINELS = {
    "facility_type": {"", "unknown"},
    "ownership_status": {"", "ownership_unknown"},
    "operator_type": {"", "unknown"},
    "organization_scope": {"", "unknown"},
}


def _is_blank(column, unknown_values: set[str]):
    return or_(column.is_(None), column.in_(tuple(unknown_values)))


def _affected_places_query(run_id: str):
    """Places whose evidence was plausibly invalidated by the two fixed
    bugs — not every place that happens to sit in needs_review or
    classification_completed.

    Correction from the first draft: this used to require ALL FOUR
    mandatory fields to be blank. Live data from the Finland run showed
    that was too narrow — 12 of the 19 classification_completed places
    had SOME evidence (ownership_status/organization_scope survived,
    since those come from a different code path) but still had
    facility_type/operator_type blank from the exact same source_url bug,
    and were still stuck at client_eligibility=review as a result. Every
    one of this run's 64 relevant candidates is REVIEW with at least one
    mandatory field blank — none are a "genuinely resolved but ambiguous
    on some other dimension" case that the old filter was trying to
    protect. The real idempotency guard is client_eligibility itself:
    once reclassification resolves a place to ELIGIBLE or EXCLUDED, it is
    no longer REVIEW, so it drops out of this filter on a second run.
    A place that comes back REVIEW again but with all four mandatory
    fields now populated (a genuine, fully-evidenced ambiguous case, e.g.
    residential_accommodation still unconfirmed) also drops out — only a
    REVIEW place with a remaining blank mandatory field is retried, and
    retrying it again costs one bounded AI call, never duplicates a
    completed success.
    """
    from app.db.models import MapsClientEligibility

    any_field_blank = or_(
        _is_blank(MapsPlace.facility_type, _UNKNOWN_SENTINELS["facility_type"]),
        _is_blank(MapsPlace.ownership_status, _UNKNOWN_SENTINELS["ownership_status"]),
        _is_blank(MapsPlace.operator_type, _UNKNOWN_SENTINELS["operator_type"]),
        _is_blank(MapsPlace.organization_scope, _UNKNOWN_SENTINELS["organization_scope"]),
    )
    return select(MapsPlace).where(
        MapsPlace.run_id == run_id,
        MapsPlace.is_relevant.is_(True),
        or_(
            MapsPlace.keep_drop_decision == KEEP,
            MapsPlace.keep_drop_decision == REVIEW,
        ),
        MapsPlace.client_eligibility == MapsClientEligibility.REVIEW.value,
        MapsPlace.enrichment_pipeline_state.in_(_BROKEN_PIPELINE_STATES),
        any_field_blank,
    )


def _estimate_classification_cost(settings, num_candidates: int) -> dict[str, Any]:
    """Rough order-of-magnitude estimate only — actual token counts vary
    per facility (name/address length, crawl excerpt size). Uses the
    configured mandatory-classification model's real OpenRouter pricing
    with representative token counts (~1500 input incl. prompt + facility
    metadata, ~300 output bounded by max_tokens=2048 in
    maps_structured_classification.py)."""
    from app.llm.catalog import get_model
    from app.llm.pricing import get_pricing_service

    model_id = settings.maps_primary_extraction_model
    try:
        model = get_model(model_id)
        pricing = get_pricing_service().get_pricing(model.provider_model)
        per_call = pricing.cost_for_tokens(1500, 300)
    except Exception as exc:  # noqa: BLE001
        return {"model": model_id, "error": f"pricing_unavailable: {exc}"}
    return {
        "model": model_id,
        "estimated_calls": num_candidates,
        "estimated_cost_usd_per_call": round(per_call, 5),
        "estimated_total_cost_usd": round(per_call * num_candidates, 4),
        "note": "order-of-magnitude only — actual tokens vary per facility",
    }


async def recover(run_id: str, *, apply: bool, with_geo: bool = True) -> None:
    async with AsyncSessionLocal() as db:
        run = await db.get(MapsCensusRun, run_id)
        if run is None:
            raise SystemExit(f"Run not found: {run_id}")

        affected = (await db.execute(_affected_places_query(run_id))).scalars().all()
        by_state: dict[str, int] = {}
        for place in affected:
            by_state[place.enrichment_pipeline_state] = (
                by_state.get(place.enrichment_pipeline_state, 0) + 1
            )

        print(f"run_id={run_id} country={run.country_code} state={run.state_code}")
        print(f"places to reset and reclassify: {len(affected)}")
        for state, count in sorted(by_state.items()):
            print(f"  {state}: {count}")

        settings = get_settings()
        cost_estimate = _estimate_classification_cost(settings, len(affected))
        print(f"estimated cost: {cost_estimate}")

        if with_geo:
            from app.services.scraping.maps_census_service import recompute_geo_boundary_for_run

            geo_result = await recompute_geo_boundary_for_run(
                AsyncSessionLocal, run_id=run_id, apply=False
            )
            print(f"geo re-evaluation (dry run, no AI calls): {geo_result}")

        if not affected:
            print("Nothing to reclassify.")
            if not apply:
                print("\nDRY RUN — no changes made. Pass --apply to execute.")
            return

        if not apply:
            print("\nDRY RUN — no changes made. Pass --apply to execute.")
            return

        place_ids = [place.id for place in affected]
        await db.execute(
            update(MapsPlace)
            .where(MapsPlace.id.in_(place_ids))
            .values(
                enrichment_status="pending",
                enrichment_pipeline_state=MapsEnrichmentPipelineState.PREFILTER_PENDING.value,
                enrichment_attempts=0,
                enrichment_error_message=None,
                enrichment_completed_at=None,
            )
        )
        await db.commit()
        print(f"Reset {len(place_ids)} places to prefilter_pending.")

    settings = get_settings()
    tracker = MapsQuotaTracker()
    from app.services.scraping.maps_census_service import maps_census_service

    result = await maps_census_service._run_mandatory_eligibility_classification(
        AsyncSessionLocal,
        run_id=run_id,
        country_code=run.country_code,
        country_name=run.country_name,
        tracker=tracker,
    )
    print("Reclassification result:", result)

    if with_geo:
        from app.services.scraping.maps_census_service import recompute_geo_boundary_for_run

        geo_result = await recompute_geo_boundary_for_run(
            AsyncSessionLocal, run_id=run_id, apply=True
        )
        print("Geo re-evaluation result:", geo_result)

    async with AsyncSessionLocal() as db:
        eligible_count = int(
            await db.scalar(
                select(func.count())
                .select_from(MapsPlace)
                .where(
                    MapsPlace.run_id == run_id,
                    MapsPlace.client_eligibility == "eligible",
                )
            )
            or 0
        )
    print(f"Eligible places in run after recovery: {eligible_count}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually reset places and call the paid classifier. Without this flag, only reports what would happen.",
    )
    parser.add_argument(
        "--no-geo",
        action="store_true",
        help="Skip the geo_boundary_match re-evaluation step (classification recovery only).",
    )
    args = parser.parse_args()
    asyncio.run(recover(args.run_id, apply=args.apply, with_geo=not args.no_geo))


if __name__ == "__main__":
    main()
