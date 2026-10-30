"""Regression tests for Sonar classify parse + Phase 2 routing fixes."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.db.models import MapsClientEligibility, MapsLifecycleStatus
from app.services.scraping.maps_eligibility import compute_client_eligibility
from app.services.scraping.maps_enrichment_selection import is_detail_enrichment_candidate
from app.services.scraping.maps_place_enrichment_service import (
    _apply_structured_fields,
    _normalize_batch_payload,
)
from app.db.models import MapsOwnershipStatus
from app.services.scraping.maps_sonar_classify import (
    SonarClassifyEvidence,
    map_sonar_classify_to_enrichment,
    needs_sonar_classification,
    parse_sonar_classify_response,
    resolve_sonar_ownership_status,
    verify_and_prune_unverified_evidence,
    SonarClassifyResult,
)


def test_normalize_accepts_single_facility_object():
    raw = {
        "facility_type": "outpatient_addiction_center",
        "operator_type": "nonprofit",
        "ownership_status": "probable_non_government",
        "confidence": 0.8,
    }
    assert len(_normalize_batch_payload(raw)["results"]) == 1


def test_parse_sonar_classify_single_object():
    text = json.dumps(
        {
            "classification_bucket": "eligible_candidate",
            "facility_type": "outpatient_addiction_center",
            "operator_type": "association",
            "addiction_treatment_mission": "confirmed",
            "confidence": 0.82,
            "reason": "Association treating toxicomanie",
            "evidence": [
                {"quote": "lutte contre la toxicomanie", "source_url": "https://example.org/about"}
            ],
        }
    )
    result = parse_sonar_classify_response(text)
    assert result.classification_bucket == "eligible_candidate"
    mapped = map_sonar_classify_to_enrichment(result, place_id="p1")
    assert mapped.facility_type == "outpatient_addiction_center"
    assert mapped.addiction_focus_confirmed is True
    assert mapped.addictions_treated == []


def test_parse_sonar_classify_ignores_markdown_fence():
    text = """```json
{"classification_bucket":"public","facility_type":null,"operator_type":"public_hospital",
 "addiction_treatment_mission":"unknown","confidence":0.9,"reason":"CHU",
 "evidence":[{"quote":"Centre Hospitalier Universitaire","source_url":"https://example.org"}]}
```"""
    result = parse_sonar_classify_response(text)
    assert result.classification_bucket == "public"


def test_unresolved_unknown_facility_is_review_not_excluded():
    place = SimpleNamespace(
        operating_status="open",
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        ownership_status="ownership_unknown",
        operator_type="unknown",
        facility_type="unknown",
        organization_scope="unknown",
        addiction_focus_confirmed=None,
    )
    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value


def test_needs_review_enters_phase2_public_does_not():
    needs_review = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.REVIEW.value,
        facility_type="unknown",
    )
    needs_review_excluded_stale = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        facility_type="unknown",
    )
    public = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.CONFIRMED_PUBLIC.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        facility_type="general_mental_health_clinic",
    )
    individual = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        facility_type="individual_addictologist",
    )
    unrelated = SimpleNamespace(
        is_relevant=True,
        lifecycle_status=MapsLifecycleStatus.UNRELATED.value,
        client_eligibility=MapsClientEligibility.EXCLUDED.value,
        facility_type="unrelated",
    )
    assert is_detail_enrichment_candidate(needs_review) is True
    assert is_detail_enrichment_candidate(needs_review_excluded_stale) is True
    assert is_detail_enrichment_candidate(public) is False
    assert is_detail_enrichment_candidate(individual) is False
    assert is_detail_enrichment_candidate(unrelated) is False


def test_map_eligible_candidate_bucket():
    result = SonarClassifyResult(
        classification_bucket="eligible_candidate",
        facility_type="residential_addiction_rehab",
        operator_type="nonprofit",
        addiction_treatment_mission="confirmed",
        confidence=0.9,
        reason="residential rehab",
        evidence=[],
    )
    mapped = map_sonar_classify_to_enrichment(result, place_id="x")
    assert mapped.ownership_status == "probable_non_government"
    assert mapped.addiction_focus_confirmed is True


def _resolved_place(**overrides):
    base = dict(
        id="p1",
        canonical_name="Facility",
        raw_name="Facility",
        city_name="Minsk",
        lifecycle_status=MapsLifecycleStatus.NEEDS_REVIEW.value,
        facility_type="outpatient_addiction_center",
        ownership_status="probable_non_government",
        addiction_focus_confirmed=True,
        classification_confidence=0.9,
        inpatient_treatment=None,
        residential_accommodation=None,
        operating_status="open",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_needs_sonar_classification_fires_when_only_residential_evidence_missing():
    """Root-cause regression (Belarus run 5acff6f7-21f8-4427-88a1-38d0a394ce83):
    facility_type/ownership/addiction_focus can all be confidently resolved
    from Maps metadata alone while inpatient_treatment/residential_
    accommodation — the fields that actually decide eligibility — stay
    unresolved. The fallback must still fire in that case."""
    place = _resolved_place()
    assert needs_sonar_classification(place) is True


def test_needs_sonar_classification_does_not_fire_for_confirmed_outpatient_only():
    """A genuinely settled outpatient-only verdict (both fields explicitly
    False, not merely missing) must not be re-litigated by this fallback."""
    place = _resolved_place(inpatient_treatment=False, residential_accommodation=False)
    assert needs_sonar_classification(place) is False


def test_needs_sonar_classification_fires_when_only_one_of_the_two_is_known():
    place = _resolved_place(inpatient_treatment=False, residential_accommodation=None)
    assert needs_sonar_classification(place) is True


def test_evidence_items_are_attributed_to_their_own_field_not_facility_type():
    """Root-cause regression: every evidence item used to be dumped onto
    "facility_type" regardless of what it supported, so an
    inpatient_treatment claim could never carry its own evidence at all."""
    result = SonarClassifyResult(
        classification_bucket="eligible_candidate",
        facility_type="residential_addiction_rehab",
        inpatient_treatment=True,
        residential_accommodation=True,
        confidence=0.9,
        reason="residential rehab with overnight stays",
        evidence=[
            SonarClassifyEvidence(
                field="inpatient_treatment",
                quote="Patients stay overnight for 28-day inpatient treatment",
                source_url="https://example.by/program",
            ),
            SonarClassifyEvidence(
                field="residential_accommodation",
                quote="On-site rooms provided for residents during treatment",
                source_url="https://example.by/program",
            ),
        ],
    )
    mapped = map_sonar_classify_to_enrichment(result, place_id="p1")
    assert "inpatient_treatment" in mapped.classification_evidence
    assert "residential_accommodation" in mapped.classification_evidence
    assert mapped.classification_evidence["inpatient_treatment"].evidence_quote.startswith(
        "Patients stay overnight"
    )
    assert mapped.inpatient_treatment is True
    assert mapped.residential_accommodation is True


@pytest.mark.asyncio
async def test_unverified_citation_leaves_inpatient_treatment_unresolved_not_rejected(db, monkeypatch):
    """End-to-end: an evidence-backed True claim whose citation fails
    verification must end up unresolved (None) after _apply_structured_fields
    — the SAME outcome as having no evidence at all — never a false
    rejection (False) and never a manufactured acceptance (True)."""
    from app.services.scraping import maps_sonar_citation_verification

    async def fake_verify(_db, *, place, source_url):
        return False  # every citation fails verification

    monkeypatch.setattr(
        maps_sonar_citation_verification, "verify_citation_refers_to_place", fake_verify
    )

    place = _resolved_place()
    result = map_sonar_classify_to_enrichment(
        SonarClassifyResult(
            classification_bucket="eligible_candidate",
            inpatient_treatment=True,
            confidence=0.9,
            reason="claims overnight stays",
            evidence=[
                SonarClassifyEvidence(
                    field="inpatient_treatment",
                    quote="Overnight stays available",
                    source_url="https://unverifiable.example/",
                )
            ],
        ),
        place_id="p1",
    )
    assert result.classification_evidence["inpatient_treatment"].evidence_quote

    pruned = await verify_and_prune_unverified_evidence(db, place=place, result=result)
    assert "inpatient_treatment" not in pruned.classification_evidence

    _apply_structured_fields(place, pruned)
    assert place.inpatient_treatment is None  # unresolved, not False, not True


@pytest.mark.asyncio
async def test_verified_citation_confirms_inpatient_treatment(db, monkeypatch):
    """The mirror case: a citation that DOES verify must still result in the
    field being confirmed True, through the normal, already-existing
    evidence-backed path."""
    from app.services.scraping import maps_sonar_citation_verification

    async def fake_verify(_db, *, place, source_url):
        return True  # citation genuinely confirms this facility

    monkeypatch.setattr(
        maps_sonar_citation_verification, "verify_citation_refers_to_place", fake_verify
    )

    place = _resolved_place()
    result = map_sonar_classify_to_enrichment(
        SonarClassifyResult(
            classification_bucket="eligible_candidate",
            inpatient_treatment=True,
            confidence=0.9,
            reason="overnight stays confirmed",
            evidence=[
                SonarClassifyEvidence(
                    field="inpatient_treatment",
                    quote="Patients may stay overnight for supervised treatment",
                    source_url="https://realsite.example/program",
                )
            ],
        ),
        place_id="p1",
    )

    pruned = await verify_and_prune_unverified_evidence(db, place=place, result=result)
    assert "inpatient_treatment" in pruned.classification_evidence

    _apply_structured_fields(place, pruned)
    assert place.inpatient_treatment is True


def test_filling_unresolved_only_never_downgrades_an_already_resolved_field():
    """Root-cause regression: broadening needs_sonar_classification to also
    fire when only inpatient/residential is missing means Sonar can now run
    even when facility_type/ownership were ALREADY confidently resolved by
    the primary pass. A weaker/different second-opinion value for those
    fields must never overwrite the already-confirmed one — only a
    genuinely unresolved field may be filled in."""
    from app.services.scraping.maps_classification_service import (
        _apply_structured_fields_filling_unresolved_only,
    )

    place = _resolved_place(
        facility_type="residential_addiction_rehab",
        ownership_status="confirmed_non_government",
        organization_scope="facility",
        operator_type="nonprofit",
        residential_accommodation=None,
        inpatient_treatment=None,
    )
    # A weaker/contradicting second opinion that would, if applied blindly,
    # downgrade already-confirmed fields back to unresolved — but DOES
    # supply the one genuinely missing piece of evidence.
    sonar_result = map_sonar_classify_to_enrichment(
        SonarClassifyResult(
            classification_bucket="needs_review",
            facility_type=None,
            operator_type=None,
            inpatient_treatment=True,
            confidence=0.4,
            reason="weak second opinion",
            evidence=[
                SonarClassifyEvidence(
                    field="inpatient_treatment",
                    quote="Overnight stays available for treatment",
                    source_url="https://example.by/program",
                )
            ],
        ),
        place_id="p1",
    )

    _apply_structured_fields_filling_unresolved_only(place, sonar_result)

    # Already-confirmed fields survive untouched...
    assert place.facility_type == "residential_addiction_rehab"
    assert place.ownership_status == "confirmed_non_government"
    assert place.organization_scope == "facility"
    assert place.operator_type == "nonprofit"
    # ...while the genuinely unresolved field gets filled in.
    assert place.inpatient_treatment is True


# --- Sonar's own explicit ownership_status field (Part 1 of the Belarus
# run 5acff6f7 ownership-blocker fix): resolve_sonar_ownership_status ---


def _belarus_sonar_place(**overrides):
    base = dict(
        id="p1",
        raw_name="Tsentr Statsionarnoy Pomoshchi \"Pravo Na Zhizn'\"",
        canonical_name="Tsentr Statsionarnoy Pomoshchi \"Pravo Na Zhizn'\"",
        city_name="Rudnya",
        formatted_address="ул. Сосновая 3 аг., Rudnya, Belarus",
        official_website=None,
        raw_website=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


BELARUS_NGO_QUOTE = (
    "Реабилитационный центр «Право на жизнь» Мозырского районного "
    "отделения Республиканского общественного объединения «Матери "
    "против наркотиков»"
)
BELARUS_SOURCE_URL = (
    "https://pmplus.by/activities/social-rehabilitation/rehabilitation/"
    "gomelskaya-oblast/"
)


def test_parse_sonar_classify_response_normalizes_ownership_status():
    text = json.dumps(
        {
            "classification_bucket": "eligible_candidate",
            "facility_type": "residential addiction rehabilitation center",
            "operator_type": "association",
            "ownership_status": "Confirmed_Non_Government",
            "addiction_treatment_mission": "confirmed",
            "confidence": 0.9,
            "reason": "named NGO operator",
            "evidence": [],
        }
    )
    result = parse_sonar_classify_response(text)
    assert result.ownership_status == "confirmed_non_government"


def test_parse_sonar_classify_response_rejects_invalid_ownership_status():
    text = json.dumps(
        {
            "classification_bucket": "needs_review",
            "ownership_status": "definitely private trust me",
            "confidence": 0.5,
            "reason": "garbage value",
            "evidence": [],
        }
    )
    result = parse_sonar_classify_response(text)
    assert result.ownership_status == "unknown"


def test_resolve_sonar_ownership_status_confirms_with_verified_facility_specific_evidence():
    """The actual Belarus run 5acff6f7 candidate: Sonar's own
    ownership_status="confirmed_non_government", backed by a real,
    non-hedged, place-specific (via cross-script name matching) evidence
    item, must upgrade the bucket-derived default."""
    place = _belarus_sonar_place()
    result = map_sonar_classify_to_enrichment(
        SonarClassifyResult(
            classification_bucket="eligible_candidate",
            facility_type="residential addiction rehabilitation center",
            operator_type="association",
            ownership_status="confirmed_non_government",
            inpatient_treatment=True,
            residential_accommodation=True,
            confidence=0.93,
            reason="named NGO operator with address match",
            evidence=[
                SonarClassifyEvidence(
                    field="ownership_status",
                    quote=BELARUS_NGO_QUOTE,
                    source_url=BELARUS_SOURCE_URL,
                ),
            ],
        ),
        place_id="p1",
    )
    assert result.ownership_status == "probable_non_government"  # bucket default, pre-resolve

    resolved = resolve_sonar_ownership_status(place, result)
    assert resolved.ownership_status == MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value


def test_resolve_sonar_ownership_status_leaves_probable_default_untouched():
    """Sonar itself says "probable" (or has no ownership_status evidence
    item at all) — must stay at the existing conservative default,
    REVIEW-eligible, never force-upgraded."""
    place = _belarus_sonar_place()
    result = map_sonar_classify_to_enrichment(
        SonarClassifyResult(
            classification_bucket="eligible_candidate",
            facility_type="residential addiction rehabilitation center",
            ownership_status="probable_non_government",
            confidence=0.6,
            reason="likely private but no documented fact",
            evidence=[],
        ),
        place_id="p1",
    )
    resolved = resolve_sonar_ownership_status(place, result)
    assert resolved.ownership_status == MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value


def test_resolve_sonar_ownership_status_rejects_hedged_assessment():
    place = _belarus_sonar_place()
    result = map_sonar_classify_to_enrichment(
        SonarClassifyResult(
            classification_bucket="eligible_candidate",
            ownership_status="confirmed_non_government",
            confidence=0.7,
            reason="hedged",
            evidence=[
                SonarClassifyEvidence(
                    field="ownership_status",
                    quote="Likely but unconfirmed non-government ownership based on the name.",
                    source_url=BELARUS_SOURCE_URL,
                ),
            ],
        ),
        place_id="p1",
    )
    resolved = resolve_sonar_ownership_status(place, result)
    assert resolved.ownership_status == MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value


def test_resolve_sonar_ownership_status_rejects_name_pattern_only_reasoning():
    """Requirement 5: never grant confirmed solely because a facility's
    name sounds private/nonprofit — a bare name/suffix pattern claim must
    be rejected exactly like everywhere else in this codebase."""
    place = _belarus_sonar_place()
    result = map_sonar_classify_to_enrichment(
        SonarClassifyResult(
            classification_bucket="eligible_candidate",
            ownership_status="confirmed_non_government",
            confidence=0.8,
            reason="name pattern",
            evidence=[
                SonarClassifyEvidence(
                    field="ownership_status",
                    quote="The facility name suggests a private, non-government operation.",
                    source_url=BELARUS_SOURCE_URL,
                ),
            ],
        ),
        place_id="p1",
    )
    resolved = resolve_sonar_ownership_status(place, result)
    assert resolved.ownership_status == MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value


def test_resolve_sonar_ownership_status_rejects_unrelated_branch_evidence():
    """Requirement 6: branch-specific identity still applies — evidence
    about a different, unrelated organization must not confirm THIS
    place's ownership."""
    place = _belarus_sonar_place()
    result = map_sonar_classify_to_enrichment(
        SonarClassifyResult(
            classification_bucket="eligible_candidate",
            ownership_status="confirmed_non_government",
            confidence=0.85,
            reason="unrelated org",
            evidence=[
                SonarClassifyEvidence(
                    field="ownership_status",
                    quote=(
                        "Минское общественное объединение «Здоровая нация» "
                        "работает отдельно от других региональных центров"
                    ),
                    source_url="https://example.by/other-org",
                ),
            ],
        ),
        place_id="p1",
    )
    resolved = resolve_sonar_ownership_status(place, result)
    assert resolved.ownership_status == MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value


def test_resolve_sonar_ownership_status_confirms_government_with_verified_evidence():
    """Symmetric case: a confirmed_government assessment, backed by real
    place-specific evidence, must also be allowed to upgrade — and
    compute_client_eligibility already excludes CONFIRMED_GOVERNMENT
    ownership, so this keeps a genuinely government-run facility
    correctly excluded instead of stuck at a weaker default."""
    place = _belarus_sonar_place(
        raw_name="Minsk Regional Narcology Dispensary",
        canonical_name="Minsk Regional Narcology Dispensary",
        city_name="Minsk",
        formatted_address="Minsk, Belarus",
    )
    result = map_sonar_classify_to_enrichment(
        SonarClassifyResult(
            classification_bucket="needs_review",
            ownership_status="confirmed_government",
            confidence=0.9,
            reason="state narcology dispensary",
            evidence=[
                SonarClassifyEvidence(
                    field="ownership_status",
                    quote=(
                        "Minsk Regional Narcology Dispensary is a state "
                        "healthcare institution operated by the Ministry of Health."
                    ),
                    source_url="https://minzdrav.gov.by/en/minsk-regional-dispensary",
                ),
            ],
        ),
        place_id="p1",
    )
    resolved = resolve_sonar_ownership_status(place, result)
    assert resolved.ownership_status == MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value

    snapshot = _resolved_place(
        ownership_status=resolved.ownership_status,
        operating_status="open",
    )
    assert compute_client_eligibility(snapshot) == MapsClientEligibility.EXCLUDED.value


def test_resolve_sonar_ownership_status_cross_script_greek_evidence():
    """Requirement "different countries and scripts": a Greek-script
    quote for a facility whose stored name is Latin-transliterated must
    cross-script match exactly like the Cyrillic case — this exercises
    the same underlying is_likely_branch_specific_evidence fix, now via
    the ownership path."""
    place = _belarus_sonar_place(
        raw_name="Kentro Apotoxinosis Elpida",
        canonical_name="Kentro Apotoxinosis Elpida",
        city_name="Athens",
        formatted_address="Athens, Greece",
    )
    result = map_sonar_classify_to_enrichment(
        SonarClassifyResult(
            classification_bucket="eligible_candidate",
            ownership_status="confirmed_non_government",
            confidence=0.88,
            reason="Greek NGO quote",
            evidence=[
                SonarClassifyEvidence(
                    field="ownership_status",
                    quote="Το Κέντρο Αποτοξίνωσης Ελπίδα λειτουργεί ως μη κερδοσκοπικός οργανισμός.",
                    source_url="https://example.gr/kentro-elpida",
                ),
            ],
        ),
        place_id="p1",
    )
    resolved = resolve_sonar_ownership_status(place, result)
    assert resolved.ownership_status == MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value


def test_resolve_sonar_ownership_status_end_to_end_into_eligibility():
    """Requirement "Sonar's new schema mapping into the existing
    eligibility computation", full round trip: facility_type +
    ownership_status both resolved from Sonar's own output must reach
    ELIGIBLE through the unmodified compute_client_eligibility."""
    place = _belarus_sonar_place()
    result = map_sonar_classify_to_enrichment(
        SonarClassifyResult(
            classification_bucket="eligible_candidate",
            facility_type="residential addiction rehabilitation center",
            operator_type="association",
            ownership_status="confirmed_non_government",
            inpatient_treatment=True,
            residential_accommodation=True,
            confidence=0.93,
            reason="full resolution",
            evidence=[
                SonarClassifyEvidence(
                    field="facility_type",
                    quote=BELARUS_NGO_QUOTE,
                    source_url=BELARUS_SOURCE_URL,
                ),
                SonarClassifyEvidence(
                    field="ownership_status",
                    quote=BELARUS_NGO_QUOTE,
                    source_url=BELARUS_SOURCE_URL,
                ),
            ],
        ),
        place_id="p1",
    )
    resolved = resolve_sonar_ownership_status(place, result)

    # facility_type normalization (free text -> enum) is a separate step
    # (normalize_facility_type_with_keywords, applied by
    # _apply_structured_fields in the real pipeline) -- exercise it here
    # too, so this test reflects the real two-step pipeline rather than
    # asserting on Sonar's own raw free-text facility_type value.
    from app.services.scraping.maps_evidence_normalization import (
        normalize_facility_type_with_keywords,
    )
    from app.services.scraping.maps_place_enrichment_service import FACILITY_TYPE_VALUES

    facility_type_evidence = resolved.classification_evidence.get("facility_type")
    normalized_facility_type = normalize_facility_type_with_keywords(
        resolved.facility_type,
        FACILITY_TYPE_VALUES,
        place=place,
        quote=facility_type_evidence.evidence_quote if facility_type_evidence else "",
        source_url=facility_type_evidence.source_url if facility_type_evidence else "",
    )

    snapshot = _resolved_place(
        facility_type=normalized_facility_type,
        ownership_status=resolved.ownership_status,
        operator_type="association",
        organization_scope="facility",
        addiction_focus_confirmed=True,
        inpatient_treatment=True,
        residential_accommodation=True,
        operating_status="unknown",
    )
    assert compute_client_eligibility(snapshot) == MapsClientEligibility.ELIGIBLE.value

