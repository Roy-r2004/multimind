from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.db.models import (
    MapsClientEligibility,
    MapsFacilityType,
    MapsLifecycleStatus,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
)

ELIGIBLE_FACILITY_TYPES = {
    MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
    MapsFacilityType.INPATIENT_DETOX_CENTER.value,
    MapsFacilityType.THERAPEUTIC_COMMUNITY.value,
}

RELEVANT_LIFECYCLE_STATUSES = {
    MapsLifecycleStatus.PLAUSIBLE.value,
    MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value,
    MapsLifecycleStatus.PROBABLE_ELIGIBLE.value,
    MapsLifecycleStatus.NEEDS_REVIEW.value,
    MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value,
}

CONTRADICTED_LEGACY_LIFECYCLE_STATUSES = {
    MapsLifecycleStatus.CONFIRMED_PUBLIC.value,
    MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value,
    MapsLifecycleStatus.CONFIRMED_CESSATION_ONLY.value,
    MapsLifecycleStatus.CONTRADICTED.value,
    MapsLifecycleStatus.UNRELATED.value,
    MapsLifecycleStatus.DUPLICATE.value,
    MapsLifecycleStatus.PERMANENTLY_CLOSED.value,
}


def _field(place: Any, name: str) -> Any:
    if isinstance(place, Mapping):
        return place.get(name)
    return getattr(place, name, None)


def derive_is_relevant(lifecycle_status: str | None) -> bool:
    return lifecycle_status in RELEVANT_LIFECYCLE_STATUSES


def derive_legacy_verification_verdict(lifecycle_status: str | None) -> str:
    if lifecycle_status == MapsLifecycleStatus.CONFIRMED_ELIGIBLE.value:
        return "confirmed"
    if lifecycle_status in CONTRADICTED_LEGACY_LIFECYCLE_STATUSES:
        return "contradicted"
    return "unknown"


def compute_client_eligibility(place: Any) -> str:
    # A manual admin exclusion is a final, audited human decision — it must
    # never be silently recomputed/overturned by automatic classification
    # re-running on this place (e.g. _finalize_cheap_skips recomputing
    # eligibility once an admin action flips keep_drop_decision to "drop").
    # mark_excluded (maps_admin_service.py) is the only writer of this
    # field, and it always also sets client_eligibility to EXCLUDED in the
    # same transaction — this is the backstop for every other caller of
    # compute_client_eligibility, not just that one.
    if _field(place, "manually_excluded_at") is not None:
        return MapsClientEligibility.EXCLUDED.value
    operating_status = _field(place, "operating_status")
    lifecycle_status = _field(place, "lifecycle_status")
    ownership_status = _field(place, "ownership_status")
    operator_type = _field(place, "operator_type")
    facility_type = _field(place, "facility_type")
    organization_scope = _field(place, "organization_scope")
    addiction_focus_confirmed = _field(place, "addiction_focus_confirmed")
    # Finer-grained residential-setting signals from the SAME classification
    # call that produced facility_type — consulted only to catch an
    # internal CONTRADICTION (the AI's own more specific fields disagree
    # with its own broad facility_type label), never to require them: a
    # facility with no residential_accommodation/care_setting evidence at
    # all must still reach ELIGIBLE exactly as before, via facility_type +
    # ownership + addiction_focus alone. See compute_client_eligibility's
    # ELIGIBLE branch below.
    residential_accommodation = _field(place, "residential_accommodation")
    inpatient_treatment = _field(place, "inpatient_treatment")
    care_setting = _field(place, "care_setting")
    # Geographic result validation (see maps_geo_boundaries.contains_point):
    # only ever set when the place HAD coordinates AND a verified polygon
    # was available to check them against — "no_coordinates" and
    # "no_polygon_available" are deliberately NOT treated as a geographic
    # problem here, so a facility is never penalized for missing data.
    geo_boundary_match = _field(place, "geo_boundary_match")

    if operating_status == "closed" or lifecycle_status == MapsLifecycleStatus.PERMANENTLY_CLOSED.value:
        return MapsClientEligibility.EXCLUDED.value

    if ownership_status == MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value:
        return MapsClientEligibility.EXCLUDED.value

    if operator_type in {
        MapsOperatorType.PUBLIC_HOSPITAL.value,
        MapsOperatorType.GOVERNMENT_AGENCY.value,
    }:
        return MapsClientEligibility.EXCLUDED.value

    # A facility generally categorized as outpatient (a broad, sometimes
    # stale or incomplete Google-Maps-derived label) but with explicit,
    # clear evidence of its own CONFIRMED inpatient addiction-treatment
    # program is not actually outpatient-only — the specific, direct
    # inpatient_treatment signal from the same classification call takes
    # precedence over the general category label, never the other way
    # round. This is the only facility_type carve-out: UNRELATED,
    # CESSATION_SERVICE, and HARM_REDUCTION_ONLY stay unconditionally
    # excluded regardless of any other field — those describe what the
    # facility fundamentally IS, not merely its care setting.
    outpatient_category_with_confirmed_inpatient = (
        facility_type == MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value
        and inpatient_treatment is True
    )
    if facility_type in {
        MapsFacilityType.UNRELATED.value,
        MapsFacilityType.CESSATION_SERVICE.value,
        MapsFacilityType.HARM_REDUCTION_ONLY.value,
    } or (
        facility_type == MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value
        and not outpatient_category_with_confirmed_inpatient
    ):
        return MapsClientEligibility.EXCLUDED.value

    # Addiction treatment no longer has to be a facility's PRIMARY program —
    # a general psychiatric/multidisciplinary facility (GENERAL_MENTAL_HEALTH_CLINIC)
    # is therefore no longer hard-excluded purely on that label. It is only
    # ever treated as an eligible facility type when there is EXPLICIT
    # positive evidence of its own actual residential/inpatient program —
    # absence of that evidence is uncertainty (review), not proof of a
    # qualifying program, so it is handled below rather than folded into
    # ELIGIBLE_FACILITY_TYPES (which still grants ELIGIBLE by default to
    # the three "always inherently residential" types).
    #
    # Confirmed INPATIENT addiction treatment satisfies this requirement on
    # its own, exactly like confirmed residential care — it is not an
    # additional, stricter bar on top of residential_accommodation. Do not
    # require a separate explicit "patients stay overnight" statement once
    # inpatient (or residential, or 24-hour) care is already confirmed:
    # previously, inpatient_treatment was extracted by the AI classifier
    # but discarded before ever reaching this field, and care_setting
    # "inpatient" was not recognized as equivalent to "residential" — both
    # silently forced a facility with clear, credible inpatient-care
    # evidence into REVIEW/EXCLUDED purely for lacking the word
    # "residential" specifically. Outpatient, day-treatment, and partial-
    # hospitalization programs are untouched by this — they are confirmed
    # NOT inpatient/residential by definition, not merely unconfirmed.
    confirmed_residential = (
        residential_accommodation is True
        or inpatient_treatment is True
        or care_setting in {"residential", "inpatient"}
    )
    is_eligible_facility_type = (
        facility_type in ELIGIBLE_FACILITY_TYPES
        or (facility_type == MapsFacilityType.GENERAL_MENTAL_HEALTH_CLINIC.value and confirmed_residential)
        or outpatient_category_with_confirmed_inpatient
    )

    if organization_scope == MapsOrganizationScope.INDIVIDUAL_PRACTICE.value or facility_type in {
        MapsFacilityType.INDIVIDUAL_ADDICTOLOGIST.value,
        MapsFacilityType.THERAPIST_OR_COUNSELOR.value,
    }:
        return MapsClientEligibility.EXCLUDED.value

    # The strict keep/drop gate decides eligibility, as it did for Google
    # Places runs. Every hard negative above (closed, government, public
    # operator, unrelated/cessation/harm-reduction, outpatient-only, solo
    # practitioner) still excludes a kept place; Phase 2 contradictions and
    # out-of-area coordinates send it to a human instead. Missing Phase 2
    # evidence never demotes a kept place.
    if _field(place, "keep_drop_decision") == "keep":
        if geo_boundary_match in {"outside_polygon", "outside_bbox_fallback"}:
            return MapsClientEligibility.REVIEW.value
        if residential_accommodation is False and care_setting in {"outpatient", "virtual"}:
            return MapsClientEligibility.REVIEW.value
        if (
            facility_type == MapsFacilityType.GENERAL_MENTAL_HEALTH_CLINIC.value
            and addiction_focus_confirmed is False
        ):
            return MapsClientEligibility.REVIEW.value
        return MapsClientEligibility.ELIGIBLE.value

    if (
        ownership_status == MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value
        and organization_scope == MapsOrganizationScope.FACILITY.value
        and is_eligible_facility_type
        and addiction_focus_confirmed is True
    ):
        if residential_accommodation is False and care_setting in {"outpatient", "virtual"}:
            # facility_type alone said residential/inpatient/therapeutic-
            # community, but the same classification call's own more
            # specific fields explicitly say there's no overnight stay —
            # a genuine internal contradiction, not just missing data.
            # Never silently trust the one broad label over its own more
            # detailed evidence; a human resolves it. (A facility that
            # ALSO offers medical detox alongside a real residential
            # program is untouched by this — nothing here penalizes
            # medical_detox, only an explicit outpatient/virtual setting.)
            return MapsClientEligibility.REVIEW.value
        if geo_boundary_match in {"outside_polygon", "outside_bbox_fallback"}:
            # Coordinates placing this result outside the run's actual
            # selected geography — either a verified polygon
            # ("outside_polygon") or, when no polygon is cached, the
            # coarse country-level bounding-box fallback
            # ("outside_bbox_fallback" — see _geo_boundary_match). Both
            # downgrade to review the same way: business classification
            # alone must never be enough to call it "eligible" for a
            # geographically-scoped census, but a plain rectangle is even
            # less precise than a polygon near a border, so this is never
            # a hard exclusion for either — a human confirms before it
            # reaches an eligible/client-facing list. Business-
            # classification inputs above are completely untouched by
            # this guard.
            return MapsClientEligibility.REVIEW.value
        return MapsClientEligibility.ELIGIBLE.value

    if (
        ownership_status == MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value
        and addiction_focus_confirmed is True
    ):
        return MapsClientEligibility.REVIEW.value

    if (
        ownership_status == MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value
        and is_eligible_facility_type
    ):
        return MapsClientEligibility.REVIEW.value

    # A psychiatric/multidisciplinary facility with a genuine addiction-focus
    # signal but no yet-confirmed residential/inpatient evidence is
    # uncertain, not disqualified — addiction no longer has to be its
    # primary program, so the only thing actually missing here is proof of
    # an overnight program, which a closer (human or later Phase 2) look
    # can still establish. Never invented as ELIGIBLE without that proof.
    if (
        facility_type == MapsFacilityType.GENERAL_MENTAL_HEALTH_CLINIC.value
        and addiction_focus_confirmed is True
        and ownership_status != MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value
    ):
        return MapsClientEligibility.REVIEW.value

    # Unresolved classification (facility_type still unknown) stays in
    # review so Phase 2 detail enrichment/a human can still research it.
    # Do not treat incomplete evidence as a hard exclusion.
    #
    # This is deliberately NOT conditioned on ownership_status at all
    # (an earlier version only covered ownership in {unknown, probable_
    # non_government}, excluding confirmed_non_government — government
    # ownership is already hard-excluded far above, so confirmed_non_
    # government is the only OTHER value ownership_status can hold here).
    # That gap meant recovering ownership_status from genuine evidence
    # (confirming it, not weakening it) could silently flip a place from
    # REVIEW to EXCLUDED purely because ownership became MORE certain
    # while facility_type/residential evidence stayed exactly as
    # unresolved as before — confirming one fact must never make an
    # unrelated, still-missing fact look like a disqualification. See the
    # evidence-normalization backfill (maps_evidence_backfill.py): a
    # place recovering ownership_status=confirmed_non_government from
    # legitimate organization-wide evidence, with facility_type still
    # genuinely unresolved, must stay REVIEW, not fall through to
    # EXCLUDED.
    if facility_type in {None, "", MapsFacilityType.UNKNOWN.value}:
        return MapsClientEligibility.REVIEW.value

    # Facilities classified as eligible types (residential rehab, inpatient detox,
    # therapeutic community) but with uncertain ownership get review status.
    # This increases coverage for official registries like FINESS where ownership
    # classification may be incomplete or inferred from facility type alone.
    if is_eligible_facility_type and ownership_status in {
        None,
        "",
        MapsOwnershipStatus.OWNERSHIP_UNKNOWN.value,
        MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value,
    }:
        return MapsClientEligibility.REVIEW.value

    return MapsClientEligibility.EXCLUDED.value


def normalize_lifecycle_eligibility_consistency(place: Any) -> bool:
    """Fix known contradictory (lifecycle_status, client_eligibility) combinations.

    A provider/API failure or unresolved classification must never silently end
    up ``excluded`` — that hides real candidates from Needs Review. Returns True
    if the place's client_eligibility was changed.
    """
    lifecycle_status = _field(place, "lifecycle_status")
    client_eligibility = _field(place, "client_eligibility")

    # needs_review facilities are, by definition, still under consideration —
    # "excluded" here is always a stale/incorrect combination.
    if (
        lifecycle_status == MapsLifecycleStatus.NEEDS_REVIEW.value
        and client_eligibility == MapsClientEligibility.EXCLUDED.value
    ):
        if not isinstance(place, Mapping):
            place.client_eligibility = MapsClientEligibility.REVIEW.value
        return True
    return False


__all__ = [
    "ELIGIBLE_FACILITY_TYPES",
    "compute_client_eligibility",
    "derive_is_relevant",
    "derive_legacy_verification_verdict",
    "normalize_lifecycle_eligibility_consistency",
]
