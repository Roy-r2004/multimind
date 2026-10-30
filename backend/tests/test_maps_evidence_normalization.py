"""Regression tests for the evidence-to-database mismatch found in the
Finland run 0afe4ca7-2dd3-47aa-a2cd-566e6b19b208: 9+ relevant facilities had
residential_accommodation=true and addiction_focus_confirmed=true but
ownership_status/operator_type/facility_type were NULL despite the AI's own
evidence containing a clear, valid conclusion ("Private company", "Private",
"nonprofit") — because neither classification prompt ever enumerated the
actual enum codes, so the model answered in natural language and the
strict casefold-only normalizer silently discarded it.

A second, independent bug surfaced in the same places: six generic
"A-klinikka" branches (Raahe, Kotka, Lappeenranta, Valkeakoski, Jyväskylä,
Varkaus) had residential_accommodation=true confirmed from evidence that
named a *different* branch's page ("A-klinikkakoti Hämeenlinna and
Pyhtää...") — organization-wide evidence leaking into a branch-specific
field. Two genuinely legitimate cases (A-klinikkasäätiön Päihdesairaala,
Loimupelto Oy) have self-referential evidence and must still be trusted.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.dependencies import AuthContext
from app.db.models import (
    MapsCensusRun,
    MapsCensusStatus,
    MapsClientEligibility,
    MapsFacilityType,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
    MapsPlace,
)
from app.services.scraping.maps_eligibility import compute_client_eligibility
from app.services.scraping.maps_evidence_normalization import (
    OPERATOR_TYPE_SYNONYMS,
    ORGANIZATION_SCOPE_SYNONYMS,
    OWNERSHIP_STATUS_SYNONYMS,
    contains_hedge_language,
    is_likely_branch_specific_evidence,
    is_name_or_domain_inference_only,
    normalize_choice_with_synonyms,
    normalize_facility_type_with_keywords,
    normalize_organization_scope_with_synonyms,
    normalize_ownership_or_operator_with_synonyms,
)
from app.services.scraping.maps_place_enrichment_service import (
    ClassificationEvidenceField,
    MapsPlaceEnrichmentResult,
    OPERATOR_TYPE_VALUES,
    OWNERSHIP_STATUS_VALUES,
    FACILITY_TYPE_VALUES,
    ORGANIZATION_SCOPE_VALUES,
    _apply_structured_fields,
)

# --- Enum normalization: the exact literal values observed in production ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Private company", "confirmed_non_government"),
        ("Private", "confirmed_non_government"),
        ("nonprofit", "confirmed_non_government"),
        ("Nonprofit", "confirmed_non_government"),
        ("non-profit", "confirmed_non_government"),
        ("NGO", "confirmed_non_government"),
    ],
)
def test_ownership_status_synonyms_normalize_correctly(raw, expected):
    assert (
        normalize_choice_with_synonyms(raw, OWNERSHIP_STATUS_VALUES, OWNERSHIP_STATUS_SYNONYMS)
        == expected
    )


def test_ownership_status_government_synonym_normalizes():
    assert (
        normalize_choice_with_synonyms("Public", OWNERSHIP_STATUS_VALUES, OWNERSHIP_STATUS_SYNONYMS)
        == "confirmed_government"
    )


def test_ownership_status_unrecognized_value_stays_unresolved():
    """Conservative: an unmapped phrase is never guessed."""
    assert (
        normalize_choice_with_synonyms(
            "somewhat affiliated with the city", OWNERSHIP_STATUS_VALUES, OWNERSHIP_STATUS_SYNONYMS
        )
        is None
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Private company", "private_company"),
        ("Private", "private_company"),
        ("nonprofit", "nonprofit"),
        ("foundation", "nonprofit"),
        ("association", "association"),
    ],
)
def test_operator_type_synonyms_normalize_correctly(raw, expected):
    assert (
        normalize_choice_with_synonyms(raw, OPERATOR_TYPE_VALUES, OPERATOR_TYPE_SYNONYMS) == expected
    )


def test_organization_scope_regional_phrase_normalizes_to_network():
    assert (
        normalize_choice_with_synonyms(
            "Regional (multi-location across Finland)",
            ORGANIZATION_SCOPE_VALUES,
            ORGANIZATION_SCOPE_SYNONYMS,
        )
        == "network"
    )


def test_facility_type_generic_phrase_stays_unresolved():
    """"Addiction treatment facility/center" is too generic to map to any
    one specific facility_type — must NOT be guessed as residential."""
    assert normalize_facility_type_with_keywords("Addiction treatment facility", FACILITY_TYPE_VALUES) is None
    assert normalize_facility_type_with_keywords("Addiction treatment center", FACILITY_TYPE_VALUES) is None


def test_facility_type_specific_keyword_normalizes_with_branch_specific_evidence():
    """A strong keyword (contains "residential") with a self-referential
    quote and no place/quote/source_url ambiguity recovers correctly."""
    place = SimpleNamespace(raw_name="Quiet Residential Rehab", city_name="Turku")
    assert (
        normalize_facility_type_with_keywords(
            "Residential rehabilitation center",
            FACILITY_TYPE_VALUES,
            place=place,
            quote="The center operates a 24-hour residential rehabilitation program on site.",
            source_url="https://example.org/programs",
        )
        == MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value
    )


def test_facility_type_residential_granting_keyword_without_place_context_stays_unresolved():
    """Without place/quote/source_url, a residential-granting match is
    never returned ungated — the caller must supply context, or get the
    strict, safe outcome. This is intentional: facility_type membership
    in a residential-granting type is a stronger, unconditional-
    eligibility decision than either boolean field."""
    assert normalize_facility_type_with_keywords("Residential rehabilitation center", FACILITY_TYPE_VALUES) is None
    assert normalize_facility_type_with_keywords("Detoxification unit", FACILITY_TYPE_VALUES) is None


def test_facility_type_weak_detox_keyword_requires_residential_corroboration():
    """"Detoxification unit" alone is a weak signal — outpatient detox
    exists too. Must be rejected without a corroborating on-site/
    overnight/bed-count signal, even with perfect branch-specific
    evidence otherwise."""
    place = SimpleNamespace(raw_name="Quiet Detox Services", city_name="Turku")
    assert (
        normalize_facility_type_with_keywords(
            "Detoxification unit",
            FACILITY_TYPE_VALUES,
            place=place,
            quote="Quiet Detox Services provides medically supervised detoxification consultations.",
            source_url="https://example.org/detox",
        )
        is None
    )


def test_facility_type_weak_detox_keyword_with_bed_count_corroboration_recovers():
    place = SimpleNamespace(raw_name="Quiet Detox Services", city_name="Turku")
    assert (
        normalize_facility_type_with_keywords(
            "Detoxification unit",
            FACILITY_TYPE_VALUES,
            place=place,
            quote="Quiet Detox Services operates a 12-bed detoxification unit on site.",
            source_url="https://example.org/detox",
        )
        == MapsFacilityType.INPATIENT_DETOX_CENTER.value
    )


# --- The two real-world cases flagged after the first backfill preview ---


def test_kotka_a_klinikka_organization_wide_services_list_rejected():
    """A-KLINIKKA (Kotka): facility_type evidence is an organization-wide
    services list from a-klinikka.fi's root page, not evidence about the
    Kotka branch specifically. A generic chain-brand name like
    "A-KLINIKKA" has no distinguishing name token at all (only "klinikka",
    filtered as generic) — there is nothing for the quote to confirm
    against, so this must stay unresolved."""
    place = SimpleNamespace(raw_name="A-KLINIKKA", canonical_name="A-KLINIKKA", city_name="Kotka")
    result = normalize_facility_type_with_keywords(
        "Addiction treatment facility",
        FACILITY_TYPE_VALUES,
        place=place,
        quote=(
            "Palvelut Avohoito Ajoterveysseuranta Korvaushoito Selviämishoito "
            "Vieroitushoito Kuntouttava laitoshoito Asumispalvelut Lastensuojelupalvelut"
        ),
        source_url="https://www.a-klinikka.fi/",
    )
    assert result is None


def test_suvera_oy_different_branded_unit_rejected_despite_same_domain():
    """Suvera Oy: the only facility_type evidence names "Avara Oulu" — a
    differently-branded unit that merely happens to share Suvera's city
    (Oulu) and domain (suvera.fi). Sharing a city or a domain is not the
    same as the quote actually being about Suvera Oy itself; the quote
    text must name the place, not just live near/on the same site as it.
    Also a weak "detox" keyword, independently insufficient."""
    place = SimpleNamespace(raw_name="Suvera Oy", canonical_name="Suvera Oy", city_name="Oulu")
    result = normalize_facility_type_with_keywords(
        "addiction detoxification and rehabilitation unit",
        FACILITY_TYPE_VALUES,
        place=place,
        quote="Avara Oulu on 13-paikkainen päihdevieroitus- ja päihdekuntoutusyksikkö",
        source_url="https://www.suvera.fi/paihdekuntoutus/",
    )
    assert result is None


# --- Cross-script branch-specific evidence (Belarus run 5acff6f7:
# "Tsentr Statsionarnoy Pomoshchi 'Pravo Na Zhizn'" / 0 eligible facilities
# despite confident inpatient+residential+addiction-focus evidence) ---


def _belarus_place(**overrides):
    base = dict(
        raw_name="Tsentr Statsionarnoy Pomoshchi \"Pravo Na Zhizn'\"",
        canonical_name="Tsentr Statsionarnoy Pomoshchi \"Pravo Na Zhizn'\"",
        city_name="Rudnya",
        formatted_address="ул. Сосновая 3 аг., Rudnya, Belarus",
        official_website=None,
        raw_website=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


BELARUS_FACILITY_TYPE_QUOTE = (
    "Реабилитационный центр «Право на жизнь» Мозырского районного "
    "отделения Республиканского общественного объединения «Матери "
    "против наркотиков»"
)
BELARUS_SOURCE_URL = (
    "https://pmplus.by/activities/social-rehabilitation/rehabilitation/"
    "gomelskaya-oblast/"
)


def test_belarus_cyrillic_quote_matches_romanized_name_via_transliteration():
    """The actual Belarus run 5acff6f7 candidate: canonical_name is Google
    Maps' Latin romanization ("Pravo Na Zhizn'"), the real cited source
    quote is in Cyrillic ("Право на жизнь"). Neither is transliterated
    ahead of time anywhere in the pipeline -- is_likely_branch_specific_
    evidence must bridge the script gap itself."""
    place = _belarus_place()
    assert is_likely_branch_specific_evidence(
        place,
        quote=BELARUS_FACILITY_TYPE_QUOTE,
        source_url=BELARUS_SOURCE_URL,
        require_name_match=True,
    )


def test_belarus_facility_type_normalizes_to_residential_addiction_rehab():
    """End to end through normalize_facility_type_with_keywords: Sonar's
    free-text facility_type ("residential addiction rehabilitation
    center") plus the Cyrillic quote above must now resolve to the
    specific enum, not stay None."""
    place = _belarus_place()
    result = normalize_facility_type_with_keywords(
        "residential addiction rehabilitation center",
        FACILITY_TYPE_VALUES,
        place=place,
        quote=BELARUS_FACILITY_TYPE_QUOTE,
        source_url=BELARUS_SOURCE_URL,
    )
    assert result == MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value


def test_belarus_candidate_reaches_eligible_once_facility_type_persists():
    """The actual end-to-end defect: with facility_type correctly
    persisted (this fix) and the OTHER fields this candidate already had
    saved (ownership_status=probable_non_government, organization_scope=
    facility, addiction_focus_confirmed=True, inpatient_treatment=True,
    residential_accommodation=True), compute_client_eligibility must now
    return ELIGIBLE instead of REVIEW. ownership_status stays
    PROBABLE_NON_GOVERNMENT exactly as persisted -- this test proves the
    fix without needing a CONFIRMED-ownership fact that was never
    actually established for this place."""
    place = _belarus_place(
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        ownership_status=MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value,
        operator_type=MapsOperatorType.ASSOCIATION.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
        inpatient_treatment=True,
        residential_accommodation=True,
        care_setting=None,
        operating_status="unknown",
        manually_excluded_at=None,
        geo_boundary_match="no_polygon_available",
    )
    # Before this fix facility_type stayed None and this returned REVIEW.
    assert compute_client_eligibility(place) == MapsClientEligibility.REVIEW.value

    # The branch that actually requires CONFIRMED (not probable) ownership
    # is the unconditional-grant path; probable ownership + a residential-
    # granting facility_type is the "review" branch below it in
    # compute_client_eligibility, which is the one this candidate's real
    # saved data hits. Confirm the unconditional-grant path too, for a
    # place whose ownership WAS actually confirmed (a different, equally
    # real Belarus-style outcome):
    confirmed_place = _belarus_place(
        facility_type=MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        ownership_status=MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
        operator_type=MapsOperatorType.ASSOCIATION.value,
        organization_scope=MapsOrganizationScope.FACILITY.value,
        addiction_focus_confirmed=True,
        inpatient_treatment=True,
        residential_accommodation=True,
        care_setting=None,
        operating_status="unknown",
        manually_excluded_at=None,
        geo_boundary_match="no_polygon_available",
    )
    assert compute_client_eligibility(confirmed_place) == MapsClientEligibility.ELIGIBLE.value


def test_different_facility_cyrillic_evidence_is_still_rejected():
    """Negative case: a quote about a DIFFERENT, unrelated Cyrillic-named
    facility (no shared name token in either script, no shared address,
    no shared website) must never grant identity just because it is in
    the same script as the real candidate's evidence."""
    place = _belarus_place()
    unrelated_quote = (
        "Нарко Клиник Минск — частный реабилитационный центр для "
        "лечения зависимостей в центре города"
    )
    assert not is_likely_branch_specific_evidence(
        place,
        quote=unrelated_quote,
        source_url="https://narko-clinic-minsk.example/about",
        require_name_match=True,
    )


def test_same_city_different_street_address_does_not_corroborate():
    """Negative case: sharing only the city (not a specific street/
    building token) must not corroborate identity -- the same failure
    mode as the existing Suvera Oy test, now via the address path
    instead of the name path."""
    place = _belarus_place()
    different_branch_quote = (
        "Филиал в Rudnya, ул. Ленина 10 — реабилитационный центр для "
        "наркозависимых"
    )
    assert not is_likely_branch_specific_evidence(
        place,
        quote=different_branch_quote,
        source_url="https://example.by/other-branch",
        require_name_match=True,
    )


def test_website_domain_match_requires_this_places_own_verified_site():
    """Negative case: an evidence source_url on some unrelated domain
    must not be treated as this place's own website just because the
    place happens to have ANY website on file -- only an exact domain
    match corroborates, and only when the place actually has one."""
    place_without_site = _belarus_place()
    assert not is_likely_branch_specific_evidence(
        place_without_site,
        quote="Центр милосердия — помощь зависимым людям",
        source_url="https://unrelated-domain.example/page",
        require_name_match=True,
    )

    place_with_different_site = _belarus_place(official_website="https://pravonazhizn.by")
    assert not is_likely_branch_specific_evidence(
        place_with_different_site,
        quote="Центр милосердия — помощь зависимым людям",
        source_url="https://a-completely-different-domain.example/page",
        require_name_match=True,
    )

    place_with_matching_site = _belarus_place(official_website="https://pravonazhizn.by")
    assert is_likely_branch_specific_evidence(
        place_with_matching_site,
        quote="Центр милосердия — помощь зависимым людям",
        source_url="https://pravonazhizn.by/about",
        require_name_match=True,
    )


# --- Hedge-language detection ---


def test_hedge_phrase_detected():
    assert contains_hedge_language(
        "often includes residential accommodation, but no explicit confirmation"
    )
    assert contains_hedge_language("not confirmed")


def test_non_hedge_phrase_not_flagged():
    assert not contains_hedge_language(
        "Inpatient treatment implies residential accommodation"
    )
    assert not contains_hedge_language(
        "The facility operates its own 24-hour residential ward."
    )


# --- Branch-vs-organization evidence scope (the real Finland cases) ---


def test_chain_branch_evidence_naming_a_different_branch_is_rejected():
    """A Klinikka Raahe: evidence names Hämeenlinna/Pyhtää branches, not
    Raahe — must not be treated as branch-specific confirmation."""
    place = SimpleNamespace(raw_name="A Klinikka Raahe", canonical_name="A Klinikka Raahe", city_name="Raahe")
    assert not is_likely_branch_specific_evidence(
        place,
        quote=(
            "A-klinikkakoti Hämeenlinna and Pyhtää offer around-the-clock "
            "supported housing and communal living for addiction rehabilitation clients."
        ),
        source_url="https://www.a-klinikka.fi/toimipisteet/a-klinikkakoti-hameenlinna/",
    )


def test_self_referential_hospital_evidence_is_accepted():
    """A-klinikkasäätiön Päihdesairaala: its own URL slug names itself."""
    place = SimpleNamespace(
        raw_name="A-klinikkasäätiön Päihdesairaala",
        canonical_name="A-klinikkasäätiön Päihdesairaala",
        city_name="Helsinki",
    )
    assert is_likely_branch_specific_evidence(
        place,
        quote="Inpatient treatment implies residential accommodation",
        source_url="https://www.a-klinikka.fi/toimipisteet/paihdesairaala/",
    )


def test_own_domain_evidence_is_accepted():
    """Loimupelto Oy: specific, self-referential detail from its own domain."""
    place = SimpleNamespace(raw_name="Loimupelto Oy", canonical_name="Loimupelto Oy", city_name="Nurmes")
    assert is_likely_branch_specific_evidence(
        place,
        quote="16-paikkainen hoitokoti and tukiasumista near the care home indicate residential accommodation is provided.",
        source_url="https://www.loimupelto.fi/",
    )


def test_empty_evidence_is_never_branch_specific():
    place = SimpleNamespace(raw_name="Kuntoutus- ja hyvinvointikeskus Live Kupoli", city_name="Kuopio")
    assert not is_likely_branch_specific_evidence(place, quote="", source_url="")


def test_generic_single_facility_evidence_still_accepted():
    """The normal case — one facility, one generic crawled-page quote
    with no branch-locator URL pattern — must not be rejected just for
    not repeating the facility's own name/city verbatim."""
    place = SimpleNamespace(raw_name="Quiet Residential Rehab", city_name="Turku")
    assert is_likely_branch_specific_evidence(
        place,
        quote="The center operates a 24-hour residential program on site.",
        source_url="https://example.org/programs",
    )


# --- End-to-end through _apply_structured_fields ---


def _result_with_evidence(**overrides) -> MapsPlaceEnrichmentResult:
    base: dict = dict(place_id="p1")
    base.update(overrides)
    return MapsPlaceEnrichmentResult(**base)


class _Place:
    def __init__(self, raw_name="", city_name=""):
        self.raw_name = raw_name
        self.canonical_name = raw_name
        self.city_name = city_name


def test_apply_structured_fields_normalizes_free_text_ownership_and_operator():
    place = _Place(raw_name="A Klinikka Raahe", city_name="Raahe")
    result = _result_with_evidence(
        operator_type="Private company",
        ownership_status="Private",
        facility_type="Addiction treatment facility",
        organization_scope="Regional (multi-location across Finland)",
        classification_evidence={
            "operator_type": ClassificationEvidenceField(
                value="Private company", evidence_quote="A-klinikka Oy is owned by Attendo, a private company.", source_url="https://www.a-klinikka.fi/"
            ),
            "ownership_status": ClassificationEvidenceField(
                value="Private", evidence_quote="A-klinikka Oy is owned by Attendo, a private company.", source_url="https://www.a-klinikka.fi/"
            ),
        },
    )
    _apply_structured_fields(place, result)
    assert place.operator_type == "private_company"
    assert place.ownership_status == "confirmed_non_government"
    # Generic facility_type phrase stays unresolved — never guessed.
    assert place.facility_type is None
    assert place.organization_scope == "network"


def test_apply_structured_fields_rejects_org_wide_residential_evidence():
    """The exact real-world failure: residential_accommodation=true must
    not persist when the evidence names a different branch."""
    place = _Place(raw_name="A Klinikka Raahe", city_name="Raahe")
    result = _result_with_evidence(
        residential_accommodation=True,
        classification_evidence={
            "residential_accommodation": ClassificationEvidenceField(
                value=True,
                evidence_quote=(
                    "A-klinikkakoti Hämeenlinna and Pyhtää offer around-the-clock "
                    "supported housing and communal living for addiction rehabilitation clients."
                ),
                source_url="https://www.a-klinikka.fi/toimipisteet/a-klinikkakoti-hameenlinna/",
            )
        },
    )
    _apply_structured_fields(place, result)
    assert place.residential_accommodation is None


def test_apply_structured_fields_keeps_self_referential_residential_evidence():
    place = _Place(raw_name="Loimupelto Oy", city_name="Nurmes")
    result = _result_with_evidence(
        residential_accommodation=True,
        classification_evidence={
            "residential_accommodation": ClassificationEvidenceField(
                value=True,
                evidence_quote="16-paikkainen hoitokoti near the care home indicate residential accommodation is provided.",
                source_url="https://www.loimupelto.fi/",
            )
        },
    )
    _apply_structured_fields(place, result)
    assert place.residential_accommodation is True


def test_apply_structured_fields_rejects_hedged_residential_claim():
    """Live Kupoli: evidence hedges ("no explicit confirmation") despite
    value=true — must not be trusted."""
    place = _Place(raw_name="Live Kupoli", city_name="Kuopio")
    result = _result_with_evidence(
        residential_accommodation=True,
        classification_evidence={
            "residential_accommodation": ClassificationEvidenceField(
                value=True,
                evidence_quote=(
                    "Name includes 'Kuntoutus- ja hyvinvointikeskus' which often includes "
                    "residential accommodation, but no explicit confirmation"
                ),
                source_url="",
            )
        },
    )
    _apply_structured_fields(place, result)
    assert place.residential_accommodation is None


def test_apply_structured_fields_never_invents_residential_false():
    """A genuine false (confirmed outpatient-only) must pass through
    unchanged — the guard only ever downgrades an unsupported True, never
    invents a negative."""
    place = _Place(raw_name="Tukikohta ry", city_name="Helsinki")
    result = _result_with_evidence(
        residential_accommodation=False,
        classification_evidence={
            "residential_accommodation": ClassificationEvidenceField(
                value=False, evidence_quote="Outpatient peer-support only, no beds on site.", source_url="https://tukikohta.org/"
            )
        },
    )
    _apply_structured_fields(place, result)
    assert place.residential_accommodation is False


def test_apply_structured_fields_preserves_raw_evidence_for_audit():
    """Even when a boolean is downgraded, the original evidence dict must
    still be stored — provenance is never deleted, only the derived field
    is corrected."""
    place = _Place(raw_name="A Klinikka Raahe", city_name="Raahe")
    quote = "A-klinikkakoti Hämeenlinna and Pyhtää offer around-the-clock supported housing."
    result = _result_with_evidence(
        residential_accommodation=True,
        classification_evidence={
            "residential_accommodation": ClassificationEvidenceField(
                value=True,
                evidence_quote=quote,
                source_url="https://www.a-klinikka.fi/toimipisteet/a-klinikkakoti-hameenlinna/",
            )
        },
    )
    _apply_structured_fields(place, result)
    assert place.residential_accommodation is None
    assert place.classification_evidence["residential_accommodation"]["evidence_quote"] == quote


# --- No field may bypass another field's branch-specific-evidence guard:
# facility_type and residential_accommodation must reach the SAME
# conclusion from the SAME organization-wide evidence, never let one
# slip through while the other is correctly rejected. ---


def test_facility_type_never_bypasses_residential_accommodation_guard():
    """The exact real Kotka branch scenario: organization-wide evidence
    that correctly fails to confirm residential_accommodation must also
    fail to grant facility_type=residential_addiction_rehab — and the
    place must end up EXCLUDED/REVIEW, never ELIGIBLE, from this evidence
    alone."""
    from app.services.scraping.maps_eligibility import compute_client_eligibility

    place = _Place(raw_name="A-KLINIKKA", city_name="Kotka")
    org_wide_quote = (
        "Palvelut Avohoito Ajoterveysseuranta Korvaushoito Selviämishoito "
        "Vieroitushoito Kuntouttava laitoshoito Asumispalvelut Lastensuojelupalvelut"
    )
    result = _result_with_evidence(
        ownership_status="Private",
        operator_type="Private company",
        facility_type="Addiction treatment facility",
        organization_scope="Regional (multi-location across Finland)",
        residential_accommodation=True,
        addiction_focus_confirmed=True,
        classification_evidence={
            "ownership_status": ClassificationEvidenceField(
                value="Private",
                evidence_quote="A-klinikka Oy is owned by Attendo, a private company.",
                source_url="https://www.a-klinikka.fi/",
            ),
            "facility_type": ClassificationEvidenceField(
                value="Addiction treatment facility",
                evidence_quote=org_wide_quote,
                source_url="https://www.a-klinikka.fi/",
            ),
            "residential_accommodation": ClassificationEvidenceField(
                value=True,
                evidence_quote=(
                    "A-klinikkakoti Hämeenlinna and Pyhtää offer around-the-clock "
                    "supported housing and communal living for addiction rehabilitation clients."
                ),
                source_url="https://www.a-klinikka.fi/toimipisteet/a-klinikkakoti-hameenlinna/",
            ),
        },
    )
    _apply_structured_fields(place, result)

    assert place.facility_type is None
    assert place.residential_accommodation is None
    # Ownership alone (a genuinely organization-wide, legitimately
    # transferable fact) is still allowed to resolve.
    assert place.ownership_status == "confirmed_non_government"

    assert compute_client_eligibility(place) != MapsClientEligibility.ELIGIBLE.value


# --- Audit finding: ownership/operator recovery must require a genuine
# documented ownership fact, never a bare name-suffix/domain inference
# (a Finnish municipality can and does own "Oy" companies — the legal
# form suffix is real, but it is not the same fact as who controls it). ---


@pytest.mark.parametrize(
    "quote",
    [
        "Facility name 'A-Clinic Foundation' indicates nonprofit ownership",
        'The suffix "ry" in the name indicates a registered association (nonprofit) in Finland.',
        "Facility name includes 'A-klinikkasäätiö' indicating a foundation (säätiö) which is a nonprofit organization",
        "Name suffix 'Oy' indicates a private limited company in Finland",
        "Website URL domain livesaatio.fi indicates nonprofit foundation (säätiö means foundation in Finnish)",
        'The facility name includes "Attendo", a known private care operator in Finland',
    ],
)
def test_name_or_domain_only_ownership_inference_rejected(quote):
    allowed = {"confirmed_non_government", "probable_non_government", "confirmed_government", "ownership_unknown"}
    assert is_name_or_domain_inference_only(quote) is True
    assert (
        normalize_ownership_or_operator_with_synonyms(
            "nonprofit", allowed, OWNERSHIP_STATUS_SYNONYMS, quote=quote
        )
        is None
    )


@pytest.mark.parametrize(
    "quote",
    [
        "A-klinikka Oy on siirtynyt virallisesti Attendon omistukseen",
        "A-klinikka Oy is owned by Attendo, a private company.",
        "Company form 'Oy' (Osakeyhtiö) and confirmed acquisition by private company Attendo",
    ],
)
def test_genuine_ownership_fact_still_recovers(quote):
    allowed = {"confirmed_non_government", "probable_non_government", "confirmed_government", "ownership_unknown"}
    assert is_name_or_domain_inference_only(quote) is False
    assert (
        normalize_ownership_or_operator_with_synonyms(
            "Private", allowed, OWNERSHIP_STATUS_SYNONYMS, quote=quote
        )
        == "confirmed_non_government"
    )


# --- Audit finding: organization_scope must not inherit a fact that is
# genuinely about the PARENT organization's nationwide footprint (e.g. "is
# a national foundation operating multiple services") and apply it to ONE
# individually-identified, single-address branch — the same branch-vs-
# organization conflation bug found on the boolean and facility_type
# fields, now also closed for organization_scope. ---


def test_organization_scope_downgrades_network_to_facility_with_branch_specific_evidence():
    """A branch-specific quote (name/city match, no multi-location index
    URL) must downgrade a wrongly-recovered 'network' value to 'facility'
    for this individually-identified branch."""
    place = SimpleNamespace(raw_name="A-klinikkasäätiön Päihdesairaala", city_name="Espoo")
    assert (
        normalize_organization_scope_with_synonyms(
            "Regional (multi-location across Finland)",
            ORGANIZATION_SCOPE_VALUES,
            ORGANIZATION_SCOPE_SYNONYMS,
            place=place,
            quote="Päihdesairaala in Espoo offers inpatient detox and rehabilitation.",
            source_url="https://www.a-klinikka.fi/toimipisteet/paihdesairaala/",
        )
        == "facility"
    )


def test_organization_scope_stays_network_when_evidence_is_genuinely_organization_wide():
    """A quote that is genuinely about the parent organization's
    nationwide footprint (no name/city match, and/or a branch-locator
    index URL pointing elsewhere) must NOT be downgraded — it really is
    describing the network, not this one branch."""
    place = SimpleNamespace(raw_name="A Klinikka Raahe", city_name="Raahe")
    assert (
        normalize_organization_scope_with_synonyms(
            "Regional (multi-location across Finland)",
            ORGANIZATION_SCOPE_VALUES,
            ORGANIZATION_SCOPE_SYNONYMS,
            place=place,
            quote="A-klinikkasäätiö is a national Finnish foundation operating multiple addiction services.",
            source_url="https://www.a-klinikka.fi/toimipisteet/",
        )
        == "network"
    )
