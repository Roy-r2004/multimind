"""Tests for maps_place_website_resolution.classify_website_relationship.

Root-cause regression coverage for the Phase 2 pipeline-wide bug where a
correctly-resolved, branch-specific official_website on a multi-location
chain domain was misclassified as "unrelated"/"unknown" because name-match
scoring only ever looked at the URL's HOST (the parent/chain brand), never
the URL's PATH (where a branch's own name slug actually lives) — causing
maps_classification_service.py and maps_enrichment_cascade_service.py
(both gated on ``website_relationship in CRAWLABLE_RELATIONSHIPS``) to skip
crawling it entirely.
"""

from __future__ import annotations

from app.services.scraping.maps_place_website_resolution import (
    CRAWLABLE_RELATIONSHIPS,
    classify_website_relationship,
)


def test_branch_specific_path_on_chain_domain_is_crawlable():
    """Real case: A-klinikkasäätiön Päihdesairaala. Its official_website
    correctly resolved to its own branch page on the parent chain's
    domain; the host alone ("a-klinikka.fi") shares almost nothing with
    the branch's own name, but the URL PATH ("paihdesairaala") does, once
    Finnish diacritics are folded to match the ASCII URL slug."""
    relationship, confidence, evidence = classify_website_relationship(
        url="https://www.a-klinikka.fi/toimipisteet/paihdesairaala/",
        facility_name="A-klinikkasäätiön Päihdesairaala",
        city="Espoo",
        country_name="Finland",
    )
    assert relationship in CRAWLABLE_RELATIONSHIPS
    assert confidence >= 0.6
    assert evidence.get("path_token_match") is True


def test_sibling_branch_on_same_chain_domain_also_crawlable_via_own_path():
    """A different branch on the same domain must independently resolve
    as crawlable from ITS OWN path — this is not a Finland- or
    facility-specific special case, it is the general host+path overlap
    rule applied to any branch."""
    relationship, _confidence, evidence = classify_website_relationship(
        url="https://www.a-klinikka.fi/toimipisteet/a-klinikkakoti-hameenlinna/",
        facility_name="A-klinikkakoti Hämeenlinna",
        city="Hämeenlinna",
        country_name="Finland",
    )
    assert relationship in CRAWLABLE_RELATIONSHIPS
    assert evidence.get("path_token_match") is True


def test_unrelated_domain_with_no_name_or_path_overlap_stays_unrelated():
    """A genuinely unrelated site (directory, aggregator, wrong business)
    must not be rescued into "official" just because SOME path exists —
    the path must still carry the facility's own name tokens."""
    relationship, _confidence, _evidence = classify_website_relationship(
        url="https://citywide-business-directory.example/listings/healthcare/",
        facility_name="A-klinikkasäätiön Päihdesairaala",
        city="Espoo",
        country_name="Finland",
    )
    assert relationship not in CRAWLABLE_RELATIONSHIPS


def test_branch_specific_path_fix_generalizes_to_other_countries_and_chains():
    """Not a Finland/A-klinikka special case: any chain domain whose host
    carries only the parent brand, with a branch-specific path carrying
    the branch's own name, benefits from the same fix — proven here with
    a fictional German facility and a fictional French one."""
    de_relationship, _confidence, de_evidence = classify_website_relationship(
        url="https://www.suchtklinik-gruppe.de/standorte/muenchen-zentrum/",
        facility_name="Suchtklinik München Zentrum",
        city="München",
        country_name="Germany",
    )
    assert de_relationship in CRAWLABLE_RELATIONSHIPS
    assert de_evidence.get("path_token_match") is True

    fr_relationship, _confidence, fr_evidence = classify_website_relationship(
        url="https://www.groupe-addiction.fr/centres/clinique-bordeaux/",
        facility_name="Clinique Bordeaux",
        city="Bordeaux",
        country_name="France",
    )
    assert fr_relationship in CRAWLABLE_RELATIONSHIPS
    assert fr_evidence.get("path_token_match") is True


def test_generic_homepage_with_weak_name_overlap_stays_unresolved():
    """A chain homepage URL (no branch-specific path at all) with weak
    host overlap should NOT be inflated to "official" by this fix — the
    path-token signal only helps when the path genuinely contains the
    facility's own name, not merely because a path exists."""
    relationship, _confidence, evidence = classify_website_relationship(
        url="https://www.a-klinikka.fi/",
        facility_name="A-klinikkasäätiön Päihdesairaala",
        city="Espoo",
        country_name="Finland",
    )
    assert "path_token_match" not in evidence
    assert relationship not in {"official"}


def test_cyrillic_facility_name_matches_its_own_latin_domain():
    """Root-cause regression for Belarus run 5acff6f7-21f8-4427-88a1-38d0a394ce83
    (Phase 2 Eligible stuck at 0): NFKD diacritic-stripping does nothing for
    Cyrillic — it is a different codepoint range, not an accented Latin
    letter — so without transliteration, ``re.findall(r"[a-z0-9]+", ...)``
    finds ZERO tokens in a Cyrillic facility name, and name_token_overlap is
    always 0.0 regardless of how genuinely official the site is. Confirmed
    against this real, live website: saikov.by is "Доктор Сайков"'s own
    site, but scored "unrelated" before this fix purely because the name is
    Cyrillic, excluding it from crawling and therefore from ever getting
    inpatient/residential evidence."""
    relationship, confidence, evidence = classify_website_relationship(
        url="https://saikov.by/",
        facility_name="Доктор Сайков",
        city="Minsk",
        country_name="Belarus",
    )
    assert relationship in CRAWLABLE_RELATIONSHIPS
    assert confidence >= 0.6
    assert evidence["name_token_overlap"] > 0.0


def test_cyrillic_facility_name_with_hyphenated_domain_matches():
    """Same root cause, a second real case from the same run: "ПМР-медсервис"
    (an acronym + Russian word, hyphenated like the Latin original) against
    its own domain pmr-med.com."""
    relationship, _confidence, evidence = classify_website_relationship(
        url="http://pmr-med.com/",
        facility_name="ПМР-медсервис",
        city="Minsk",
        country_name="Belarus",
    )
    assert relationship in CRAWLABLE_RELATIONSHIPS
    assert evidence["name_token_overlap"] > 0.0


def test_cyrillic_name_on_a_genuinely_unrelated_acronym_domain_stays_unrelated():
    """The fix must not manufacture a match where none exists: a domain
    that is a pure acronym unrelated to the facility's actual name (common
    for real small businesses) must still score as unrelated/unknown —
    transliteration only helps when a real textual relationship exists."""
    relationship, _confidence, _evidence = classify_website_relationship(
        url="https://tcb.by/rehabcentr/",
        facility_name='Миссия "Возвращение" Барановичское отделение',
        city="Baranovichi",
        country_name="Belarus",
    )
    assert relationship not in CRAWLABLE_RELATIONSHIPS


def test_substring_overlap_matches_a_name_concatenated_without_separators():
    """Generic improvement, not Cyrillic-specific: a facility name whose
    actual brand ("ResursMed") has no space/hyphen separating it from
    surrounding descriptive text, transliterated or not, must still
    token-match a domain that contains that brand as a substring (e.g.
    "resursmedical.by"), not require an exact whole-token equality."""
    name_tokens_present = classify_website_relationship(
        url="https://brandnamecorp.example/",
        facility_name="Services offered by BrandName company",
        city=None,
        country_name="Germany",
    )
    relationship, _confidence, evidence = name_tokens_present
    assert evidence["name_token_overlap"] > 0.0
    assert relationship not in {"unrelated"}
