"""Generic, reusable, zero-AI mechanism for applying a human-sourced,
auditable correction to ONE specific place's classification fields from
documented evidence the automated pipeline missed or mis-derived.

This is NOT a Finland-specific or facility-specific tool — it takes a
place_id and a list of field corrections (each with its own evidence
quote and source URL) supplied by the caller; nothing about any specific
country, chain, or facility name is hardcoded here. It is the right tool
whenever a human has done the research the AI pipeline didn't (found the
actual ownership-transfer announcement, read the facility's own services
page in detail, etc.) and wants that specific, documented finding applied
to exactly one record — never a bulk operation, never a guess.

Safety properties (shared with maps_evidence_backfill.py — see that
module for the same properties on the bulk pass):
  - Transactional: one ``db.commit()`` at the end; any exception before
    it leaves the database unchanged.
  - Idempotent: a field already equal to the requested new value is not
    a "change" — applying the same correction twice is a no-op the
    second time.
  - Scope-limited: refuses outright to touch a manually-excluded place or
    a confident keep/drop "drop" — a human correction must never be used
    to quietly reopen either of those.
  - Auditable: every field's evidence is REPLACED with the new, cited
    quote/source_url (this is a human correcting the record, unlike the
    bulk backfill which only ever reads existing evidence) and an entry
    is appended (never overwrites a prior one) to
    ``classification_evidence["_manual_correction_audit"]`` recording
    exactly what changed, from what, to what, when, citing which sources,
    and any reviewer note.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.db.models import MapsPlace
from app.services.scraping.maps_evidence_backfill import (
    PlaceBackfillPlan,
    _derive_lifecycle_status,
    _eligibility_snapshot,
    compute_client_eligibility,
    derive_is_relevant,
    derive_legacy_verification_verdict,
)

MANUAL_CORRECTION_SOURCE_TAG = "manual_evidence_correction_v1"


@dataclass
class ManualFieldCorrection:
    """One field's corrected value, with the documented evidence backing
    it — every correction MUST cite a real source_url; this is never a
    vehicle for an uncited guess."""

    field: str
    new_value: Any
    evidence_quote: str
    source_url: str
    source_type: str = "manual_correction"

    def __post_init__(self) -> None:
        if not self.evidence_quote.strip() or not self.source_url.strip():
            raise ValueError(
                f"ManualFieldCorrection for {self.field!r} requires a non-empty "
                "evidence_quote and source_url — a manual correction must always "
                "cite a documented source, never an uncited assertion."
            )


@dataclass
class FieldChange:
    field: str
    old_value: Any
    new_value: Any


def plan_manual_correction(
    place: MapsPlace, corrections: list[ManualFieldCorrection]
) -> PlaceBackfillPlan:
    """Pure: computes what WOULD change and the resulting eligibility on
    a snapshot (see maps_evidence_backfill._eligibility_snapshot) — never
    mutates ``place``. Shared by the dry-run preview and apply, so they
    cannot diverge, exactly like the bulk backfill's
    plan_backfill_for_place."""
    plan = PlaceBackfillPlan(
        place_id=place.id, name=place.canonical_name or place.raw_name or place.id
    )
    plan.eligibility_before = place.client_eligibility

    for correction in corrections:
        old_value = getattr(place, correction.field, None)
        if old_value == correction.new_value:
            continue
        plan.changes.append(FieldChange(correction.field, old_value, correction.new_value))

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


async def preview_manual_correction(
    session_factory, *, place_id: str, corrections: list[ManualFieldCorrection]
) -> PlaceBackfillPlan:
    """Read-only dry run for a single place. No writes."""
    async with session_factory() as db:
        place = await db.get(MapsPlace, place_id)
        if place is None:
            raise ValueError(f"Place not found: {place_id}")
        return plan_manual_correction(place, corrections)


async def apply_manual_correction(
    session_factory,
    *,
    place_id: str,
    corrections: list[ManualFieldCorrection],
    reviewer_note: str = "",
) -> dict[str, Any]:
    """Writes the correction. Transactional, idempotent, scope-limited,
    auditable — see module docstring."""
    async with session_factory() as db:
        place = await db.get(MapsPlace, place_id)
        if place is None:
            raise ValueError(f"Place not found: {place_id}")
        if place.manually_excluded_at is not None:
            raise ValueError(
                f"Refusing to correct {place_id}: it is manually excluded — "
                "a confirmed human decision this tool must never reopen."
            )
        if place.keep_drop_decision == "drop":
            raise ValueError(
                f"Refusing to correct {place_id}: it was confidently dropped by "
                "keep/drop — a manual correction must never reopen that verdict."
            )

        plan = plan_manual_correction(place, corrections)
        if not plan.changes:
            return {"place_id": place_id, "changed": False, "plan": plan}

        for change in plan.changes:
            setattr(place, change.field, change.new_value)
        place.lifecycle_status = plan.lifecycle_status_after
        place.client_eligibility = plan.eligibility_after
        place.is_relevant = plan.is_relevant_after
        place.verification_verdict = plan.verification_verdict_after

        evidence = dict(place.classification_evidence or {})
        changed_field_names = {c.field for c in plan.changes}
        for correction in corrections:
            if correction.field not in changed_field_names:
                continue
            evidence[correction.field] = {
                "value": correction.new_value,
                "evidence_quote": correction.evidence_quote,
                "source_url": correction.source_url,
                "source_type": correction.source_type,
            }
        audit_log = list(evidence.get("_manual_correction_audit") or [])
        audit_log.append(
            {
                "source": MANUAL_CORRECTION_SOURCE_TAG,
                "applied_at": datetime.now(UTC).isoformat(),
                "reviewer_note": reviewer_note,
                "changes": [
                    {"field": c.field, "old": c.old_value, "new": c.new_value} for c in plan.changes
                ],
                "sources": [
                    {"field": c.field, "source_url": c.source_url}
                    for c in corrections
                    if c.field in changed_field_names
                ],
                "eligibility_before": plan.eligibility_before,
                "eligibility_after": plan.eligibility_after,
            }
        )
        evidence["_manual_correction_audit"] = audit_log
        place.classification_evidence = evidence

        await db.commit()

    return {"place_id": place_id, "changed": True, "plan": plan}


__all__ = [
    "MANUAL_CORRECTION_SOURCE_TAG",
    "FieldChange",
    "ManualFieldCorrection",
    "apply_manual_correction",
    "plan_manual_correction",
    "preview_manual_correction",
]
