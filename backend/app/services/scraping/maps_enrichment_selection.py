"""Select which Maps places enter the expensive enrichment cascade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select

from app.db.models import (
    MapsClientEligibility,
    MapsLifecycleStatus,
    MapsPlace,
    MapsPlaceEnrichmentStatus,
)

CONFIDENT_SKIP_LIFECYCLE = frozenset(
    {
        MapsLifecycleStatus.UNRELATED.value,
        MapsLifecycleStatus.CONFIRMED_PUBLIC.value,
        MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value,
        MapsLifecycleStatus.CONFIRMED_CESSATION_ONLY.value,
        MapsLifecycleStatus.CONTRADICTED.value,
        MapsLifecycleStatus.DUPLICATE.value,
        MapsLifecycleStatus.PERMANENTLY_CLOSED.value,
    }
)

UNRESOLVED_LIFECYCLE = frozenset(
    {
        MapsLifecycleStatus.NEEDS_REVIEW.value,
        MapsLifecycleStatus.PLAUSIBLE.value,
        MapsLifecycleStatus.DISCOVERED.value,
        MapsLifecycleStatus.PROBABLE_ELIGIBLE.value,
        MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value,
    }
)


@dataclass
class EnrichmentSelectionReport:
    selected_count: int = 0
    skipped_count: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    selection_sql: str = ""


def skip_reason_for_place(place: Any) -> str | None:
    if not getattr(place, "is_relevant", None):
        return "not_relevant"

    lifecycle = getattr(place, "lifecycle_status", None) or ""
    if lifecycle in CONFIDENT_SKIP_LIFECYCLE:
        return f"confident_lifecycle:{lifecycle}"

    eligibility = getattr(place, "client_eligibility", None) or ""
    if eligibility == MapsClientEligibility.EXCLUDED.value and lifecycle == MapsLifecycleStatus.UNRELATED.value:
        return "excluded_unrelated"

    if lifecycle not in UNRESOLVED_LIFECYCLE:
        return f"lifecycle:{lifecycle}"

    if lifecycle == MapsLifecycleStatus.NEEDS_REVIEW.value:
        if eligibility not in {
            MapsClientEligibility.REVIEW.value,
            MapsClientEligibility.EXCLUDED.value,
        }:
            return f"eligibility:{eligibility}"
    elif lifecycle == MapsLifecycleStatus.PLAUSIBLE.value:
        pass
    elif lifecycle == MapsLifecycleStatus.DISCOVERED.value:
        # Safety fallback: Places stuck in DISCOVERED should NOT skip enrichment —
        # they never got classified. Route to NEEDS_REVIEW for manual review but allow enrichment.
        # This handles edge cases where classification phase failed silently or was never run.
        return None

    enrichment_status = getattr(place, "enrichment_status", None) or ""
    if enrichment_status == MapsPlaceEnrichmentStatus.COMPLETED.value:
        return "enrichment_already_completed"

    return None


def should_select_for_expensive_pipeline(place: Any) -> bool:
    return skip_reason_for_place(place) is None


def is_detail_enrichment_candidate(place: Any) -> bool:
    """Places that completed Phase 1 classification and need addictions/languages.

    Accepts eligible / review / needs_review candidates. Excludes public,
    individual, unrelated, cessation-only, and other confident skip buckets.

    Safety fallback: If lifecycle_status is DISCOVERED (classification never ran),
    still allow enrichment for relevant places so they get a chance at detail enrichment.
    """
    if not getattr(place, "is_relevant", None):
        return False

    lifecycle = getattr(place, "lifecycle_status", None) or ""
    if lifecycle in CONFIDENT_SKIP_LIFECYCLE:
        return False

    # Safety fallback for places stuck in DISCOVERED (classification failed)
    if lifecycle == MapsLifecycleStatus.DISCOVERED.value:
        return True

    if lifecycle not in {
        MapsLifecycleStatus.NEEDS_REVIEW.value,
        MapsLifecycleStatus.PLAUSIBLE.value,
        MapsLifecycleStatus.PROBABLE_ELIGIBLE.value,
        MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value,
    }:
        return False

    eligibility = getattr(place, "client_eligibility", None) or ""
    if eligibility in {
        MapsClientEligibility.ELIGIBLE.value,
        MapsClientEligibility.REVIEW.value,
    }:
        return True

    # Stale rows may still have needs_review + excluded after incomplete
    # classification. Route them to Phase 2 unless facility type is a hard exclude.
    if lifecycle == MapsLifecycleStatus.NEEDS_REVIEW.value:
        facility_type = getattr(place, "facility_type", None) or ""
        if facility_type in {
            "unrelated",
            "cessation_service",
            "harm_reduction_only",
            "individual_addictologist",
            "therapist_or_counselor",
        }:
            return False
        return True

    return False


def build_expensive_pipeline_query(run_id: str):
    """SQLAlchemy query for places that should enter the expensive cascade."""
    return (
        select(MapsPlace)
        .where(
            MapsPlace.run_id == run_id,
            MapsPlace.is_relevant.is_(True),
            MapsPlace.lifecycle_status.notin_(tuple(CONFIDENT_SKIP_LIFECYCLE)),
            or_(
                MapsPlace.lifecycle_status == MapsLifecycleStatus.NEEDS_REVIEW.value,
                MapsPlace.lifecycle_status == MapsLifecycleStatus.PLAUSIBLE.value,
            ),
            MapsPlace.enrichment_status.in_(
                [
                    MapsPlaceEnrichmentStatus.PENDING.value,
                    MapsPlaceEnrichmentStatus.FAILED.value,
                    MapsPlaceEnrichmentStatus.SKIPPED.value,
                ]
            ),
        )
        .order_by(MapsPlace.id)
    )


def selection_query_sql() -> str:
    return (
        "SELECT * FROM maps_places WHERE run_id = :run_id "
        "AND is_relevant IS TRUE "
        "AND lifecycle_status NOT IN "
        "('unrelated','confirmed_public','confirmed_individual_practitioner',"
        "'confirmed_cessation_only','contradicted','duplicate','permanently_closed') "
        "AND lifecycle_status IN ('needs_review','plausible') "
        "AND enrichment_status IN ('pending','failed','skipped') "
        "ORDER BY id"
    )


async def build_selection_report(session, *, run_id: str) -> EnrichmentSelectionReport:
    all_places = (
        await session.execute(select(MapsPlace).where(MapsPlace.run_id == run_id))
    ).scalars().all()
    report = EnrichmentSelectionReport(selection_sql=selection_query_sql())
    for place in all_places:
        reason = skip_reason_for_place(place)
        if reason is None:
            report.selected_count += 1
        else:
            report.skipped_count += 1
            report.skip_reasons[reason] = report.skip_reasons.get(reason, 0) + 1
    return report


async def count_selection(session, *, run_id: str) -> tuple[int, int]:
    report = await build_selection_report(session, run_id=run_id)
    return report.selected_count, report.skipped_count


__all__ = [
    "EnrichmentSelectionReport",
    "build_expensive_pipeline_query",
    "build_selection_report",
    "count_selection",
    "is_detail_enrichment_candidate",
    "selection_query_sql",
    "should_select_for_expensive_pipeline",
    "skip_reason_for_place",
]
