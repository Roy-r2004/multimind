"""Safe, idempotent, zero-AI-call backfill for the evidence-to-database
mismatch investigated against the Finland run 0afe4ca7-2dd3-47aa-a2cd-
566e6b19b208 (see maps_evidence_normalization.py's module docstring for the
underlying root causes).

This recovers ONLY values the normalization rules can resolve from
evidence ALREADY stored on each place — it never calls an AI provider,
never touches discovery/keep-drop state, and never widens eligibility
beyond what ``compute_client_eligibility`` already allows for the
resulting fields.

Safety properties, each load-bearing for a production backfill:
  - Transactional: every mutation happens on already-loaded ORM objects in
    memory; the ONE ``await db.commit()`` at the end is the only point
    anything reaches the database. Any exception before it leaves the
    database completely unchanged — there is no partial-apply state.
  - Idempotent: a field already resolved (non-null) is not a candidate
    (``plan_backfill_for_place`` finds nothing to change for it); a
    boolean already downgraded to ``None`` is likewise no longer a
    candidate. Running this twice in a row makes zero further changes
    the second time.
  - Scope-limited: ``manually_excluded_at`` and ``keep_drop_decision ==
    "drop"`` places are excluded by the candidate query itself — a
    confident admin/AI rejection is never reopened by this pass.
  - Auditable: every applied change appends an entry (never overwrites
    one) to ``classification_evidence["_backfill_audit"]`` — the
    original per-field evidence dict is never touched or removed, only
    read from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select

from app.db.models import MapsCensusRun, MapsClientEligibility, MapsOwnershipStatus, MapsPlace
from app.services.scraping.maps_eligibility import (
    compute_client_eligibility,
    derive_is_relevant,
    derive_legacy_verification_verdict,
)
from app.services.scraping.maps_evidence_normalization import (
    OPERATOR_TYPE_SYNONYMS,
    ORGANIZATION_SCOPE_SYNONYMS,
    OWNERSHIP_STATUS_SYNONYMS,
    contains_hedge_language,
    is_likely_branch_specific_evidence,
    normalize_choice_with_synonyms,
    normalize_facility_type_with_keywords,
    normalize_organization_scope_with_synonyms,
    normalize_ownership_or_operator_with_synonyms,
    recover_ownership_status_from_unambiguous_ngo_evidence,
)
from app.services.scraping.maps_keep_drop_service import KEEP, REVIEW
from app.services.scraping.maps_place_enrichment_service import (
    FACILITY_TYPE_VALUES,
    OPERATOR_TYPE_VALUES,
    ORGANIZATION_SCOPE_VALUES,
    OWNERSHIP_STATUS_VALUES,
    _derive_lifecycle_status,
)

BACKFILL_SOURCE_TAG = "evidence_normalization_backfill_v1"

# "ownership_gated" fields require is_name_or_domain_inference_only's
# extra check (see maps_evidence_normalization.py) — a name-suffix/
# domain-only inference ("suffix 'Oy' indicates...") is never enough to
# confirm actual ownership, only a stated fact (an owner, an acquisition)
# is. organization_scope has no such risk — it is never normalized
# "ownership_gated".
_STRING_FIELDS: tuple[tuple[str, set[str], dict[str, str] | None, bool], ...] = (
    ("operator_type", OPERATOR_TYPE_VALUES, OPERATOR_TYPE_SYNONYMS, True),
    ("ownership_status", OWNERSHIP_STATUS_VALUES, OWNERSHIP_STATUS_SYNONYMS, True),
    ("facility_type", FACILITY_TYPE_VALUES, None, False),
    ("organization_scope", ORGANIZATION_SCOPE_VALUES, ORGANIZATION_SCOPE_SYNONYMS, False),
)
_BOOLEAN_FIELDS = ("residential_accommodation", "inpatient_treatment")


@dataclass
class FieldChange:
    field: str
    old_value: Any
    new_value: Any


@dataclass
class PlaceBackfillPlan:
    place_id: str
    name: str
    changes: list[FieldChange] = field(default_factory=list)
    eligibility_before: str | None = None
    eligibility_after: str | None = None
    lifecycle_status_after: str | None = None
    is_relevant_after: bool | None = None
    verification_verdict_after: str | None = None


# Every attribute compute_client_eligibility/_derive_lifecycle_status read
# via getattr — kept as an explicit list (not "whatever MapsPlace has") so
# the dry-run snapshot below is a faithful, auditable copy of exactly what
# those two functions see, nothing more and nothing silently missing.
_ELIGIBILITY_INPUT_FIELDS = (
    "manually_excluded_at",
    "operating_status",
    "lifecycle_status",
    "ownership_status",
    "operator_type",
    "facility_type",
    "organization_scope",
    "addiction_focus_confirmed",
    "residential_accommodation",
    "inpatient_treatment",
    "care_setting",
    "geo_boundary_match",
)


def eligible_candidates_query(run_id: str):
    """Exactly the places this backfill is ever allowed to touch: relevant,
    not manually excluded, not a confident keep/drop rejection, and routed
    to Phase 2 (keep/review, or already admin-marked eligible). Identical
    scope to the read-only preview script and to every other recovery
    script in this codebase — a confident drop or a manual exclusion is
    never a row this function even considers."""
    return select(MapsPlace).where(
        MapsPlace.run_id == run_id,
        MapsPlace.is_relevant.is_(True),
        MapsPlace.manually_excluded_at.is_(None),
        # NOT `.isnot("drop")` — SQLAlchemy compiles that to `IS NOT $2`,
        # and PostgreSQL's `IS NOT` only accepts NULL or a boolean literal
        # on the right-hand side, never an arbitrary string value; asyncpg
        # rejects it outright as a syntax error before it ever runs.
        # `IS DISTINCT FROM` is the correct NULL-safe "not equal to this
        # exact value" operator — unlike plain `!=`, it still correctly
        # treats a NULL keep_drop_decision (never decided) as distinct
        # from "drop", so it is included here exactly as before.
        MapsPlace.keep_drop_decision.is_distinct_from("drop"),
        or_(
            MapsPlace.keep_drop_decision == KEEP,
            MapsPlace.keep_drop_decision == REVIEW,
            MapsPlace.client_eligibility == MapsClientEligibility.ELIGIBLE.value,
        ),
    )


def _eligibility_snapshot(place: MapsPlace) -> Any:
    """A plain, duck-typed copy of only the fields compute_client_
    eligibility/_derive_lifecycle_status actually read — never the real
    ORM object. Applying FieldChanges to this and recomputing on it
    cannot, even transiently, mutate or dirty the real ``place`` row, so
    the dry-run preview is genuinely read-only, not "read-only because we
    remembered not to call commit()"."""
    from types import SimpleNamespace

    return SimpleNamespace(
        **{name: getattr(place, name, None) for name in _ELIGIBILITY_INPUT_FIELDS}
    )


def plan_backfill_for_place(place: MapsPlace, *, country_code: str | None = None) -> PlaceBackfillPlan:
    """Pure function: reads already-loaded fields/evidence, returns what
    WOULD change, including the resulting lifecycle_status/client_
    eligibility/is_relevant/verification_verdict — computed on a snapshot
    (see ``_eligibility_snapshot``), never on ``place`` itself, so calling
    this makes no mutation at all. Used identically by the dry-run preview
    and as the first (and, for the eligibility recomputation, ONLY) step
    of apply, so the two can never again independently compute — and
    silently disagree about — what eligibility a change would produce.

    ``country_code`` (the run's own country, not a per-place field) is
    only used by the narrow, jurisdiction-scoped
    ``recover_ownership_status_from_unambiguous_ngo_evidence`` fallback
    below — every other recovery path in this function is unaffected by
    it.
    """
    plan = PlaceBackfillPlan(place_id=place.id, name=place.canonical_name or place.raw_name or place.id)
    plan.eligibility_before = place.client_eligibility
    evidence_map = place.classification_evidence or {}
    ownership_recovered_from_synonyms = False

    for field_name, allowed, synonyms, ownership_gated in _STRING_FIELDS:
        current = getattr(place, field_name, None)
        if current:
            continue
        ev = evidence_map.get(field_name) or {}
        raw_value = ev.get("value")
        if not raw_value or not isinstance(raw_value, str):
            continue
        if field_name == "facility_type":
            recovered = normalize_facility_type_with_keywords(
                raw_value,
                allowed,
                place=place,
                quote=ev.get("evidence_quote") or "",
                source_url=ev.get("source_url") or "",
            )
        elif field_name == "organization_scope":
            recovered = normalize_organization_scope_with_synonyms(
                raw_value,
                allowed,
                synonyms or {},
                place=place,
                quote=ev.get("evidence_quote") or "",
                source_url=ev.get("source_url") or "",
            )
        elif ownership_gated:
            recovered = normalize_ownership_or_operator_with_synonyms(
                raw_value, allowed, synonyms or {}, quote=ev.get("evidence_quote") or ""
            )
        else:
            recovered = normalize_choice_with_synonyms(raw_value, allowed, synonyms or {})
        if recovered:
            plan.changes.append(FieldChange(field_name, current, recovered))
            if field_name == "ownership_status":
                ownership_recovered_from_synonyms = True

    # Narrow fallback: the Sonar classify path never emitted its own
    # "ownership_status" evidence item before this fix (see
    # resolve_sonar_ownership_status), so for any place that went through
    # Sonar before that fix was deployed, ownership_status was persisted
    # as a hardcoded PROBABLE_NON_GOVERNMENT default — never a genuine
    # per-place determination — while the real operator fact sat inside
    # a DIFFERENT field's evidence quote (e.g. facility_type's) the whole
    # time. Confirmed on Belarus run 5acff6f7 facility 32e01f47: the first
    # dry run after deploying this fix found zero changes, because the
    # gate here originally only ran when ownership_status was completely
    # EMPTY — but this place already had the stale "probable" default
    # sitting in the column, which made it look "already resolved" and
    # permanently skipped it.
    #
    # Only PROBABLE_NON_GOVERNMENT (the system's own least-confident,
    # effectively-a-default value) is eligible to be strengthened this
    # way — CONFIRMED_NON_GOVERNMENT and CONFIRMED_GOVERNMENT are real,
    # already-established determinations and stay fully protected, exactly
    # like every other already-resolved field in this function. Nothing
    # about the actual verification (hedge-language / name-inference-only
    # / branch-specific-identity with require_name_match) is loosened —
    # this only widens WHEN that unchanged guard stack gets consulted.
    current_ownership = getattr(place, "ownership_status", None)
    ownership_upgradeable = current_ownership in (
        None,
        "",
        "ownership_unknown",
        MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value,
    )
    if ownership_upgradeable and not ownership_recovered_from_synonyms:
        recovered_ownership = recover_ownership_status_from_unambiguous_ngo_evidence(
            place, country_code=country_code
        )
        if recovered_ownership and recovered_ownership != current_ownership:
            plan.changes.append(
                FieldChange("ownership_status", current_ownership, recovered_ownership)
            )

    for field_name in _BOOLEAN_FIELDS:
        current = getattr(place, field_name, None)
        if current is not True:
            continue
        ev = evidence_map.get(field_name) or {}
        quote = ev.get("evidence_quote") or ""
        source_url = ev.get("source_url") or ""
        if contains_hedge_language(quote) or not is_likely_branch_specific_evidence(
            place, quote=quote, source_url=source_url
        ):
            plan.changes.append(FieldChange(field_name, True, None))

    if plan.changes:
        snapshot = _eligibility_snapshot(place)
        for change in plan.changes:
            setattr(snapshot, change.field, change.new_value)
        snapshot.lifecycle_status = _derive_lifecycle_status(snapshot)
        snapshot.client_eligibility = compute_client_eligibility(snapshot)
        plan.lifecycle_status_after = snapshot.lifecycle_status
        plan.eligibility_after = snapshot.client_eligibility
        plan.is_relevant_after = derive_is_relevant(snapshot.lifecycle_status)
        plan.verification_verdict_after = derive_legacy_verification_verdict(snapshot.lifecycle_status)
    else:
        plan.eligibility_after = plan.eligibility_before

    return plan


def _audit_repr(value: Any) -> Any:
    return value


async def build_backfill_plan(session_factory, *, run_id: str) -> list[PlaceBackfillPlan]:
    """Read-only: the dry run. Returns one plan per place that WOULD
    change, with no database writes at all."""
    async with session_factory() as db:
        run = await db.get(MapsCensusRun, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        candidates = (await db.execute(eligible_candidates_query(run_id))).scalars().all()
        plans: list[PlaceBackfillPlan] = []
        for place in candidates:
            plan = plan_backfill_for_place(place, country_code=run.country_code)
            if plan.changes:
                plans.append(plan)
        return plans


async def apply_backfill(session_factory, *, run_id: str) -> dict[str, Any]:
    """Writes the backfill. Transactional (see module docstring): all
    mutation happens on ORM objects already loaded in this one session;
    the single ``db.commit()`` below is the only moment anything reaches
    the database, so an exception anywhere before it leaves the database
    byte-for-byte unchanged. Idempotent: re-running finds zero candidates
    for every place already resolved by a prior apply.
    """
    async with session_factory() as db:
        run = await db.get(MapsCensusRun, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        candidates = (await db.execute(eligible_candidates_query(run_id))).scalars().all()

        applied_plans: list[PlaceBackfillPlan] = []
        for place in candidates:
            plan = plan_backfill_for_place(place, country_code=run.country_code)
            if not plan.changes:
                continue

            # Write exactly the fields already computed by
            # plan_backfill_for_place's snapshot — never recomputed a
            # second time on the real object. Two independent
            # recomputations of the same "what would eligibility become"
            # question is exactly how dry-run and apply drifted apart
            # before (the dry run never called compute_client_eligibility
            # at all, and showed eligibility_after=None for every place).
            for change in plan.changes:
                setattr(place, change.field, change.new_value)
            place.lifecycle_status = plan.lifecycle_status_after
            place.client_eligibility = plan.eligibility_after
            place.is_relevant = plan.is_relevant_after
            place.verification_verdict = plan.verification_verdict_after

            audit_log = list((place.classification_evidence or {}).get("_backfill_audit") or [])
            audit_log.append(
                {
                    "source": BACKFILL_SOURCE_TAG,
                    "applied_at": datetime.now(UTC).isoformat(),
                    "changes": [
                        {"field": c.field, "old": _audit_repr(c.old_value), "new": _audit_repr(c.new_value)}
                        for c in plan.changes
                    ],
                    "eligibility_before": plan.eligibility_before,
                    "eligibility_after": plan.eligibility_after,
                }
            )
            updated_evidence = dict(place.classification_evidence or {})
            updated_evidence["_backfill_audit"] = audit_log
            place.classification_evidence = updated_evidence

            applied_plans.append(plan)

        await db.commit()

    return {
        "run_id": run_id,
        "places_changed": len(applied_plans),
        "plans": applied_plans,
    }


__all__ = [
    "BACKFILL_SOURCE_TAG",
    "FieldChange",
    "PlaceBackfillPlan",
    "apply_backfill",
    "build_backfill_plan",
    "eligible_candidates_query",
    "plan_backfill_for_place",
]
