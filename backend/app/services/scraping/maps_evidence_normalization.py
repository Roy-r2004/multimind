"""Conservative normalization of free-text AI classification values, and a
guard against organization-wide evidence being mistaken for branch-specific
confirmation.

Root cause (Finland run 0afe4ca7-2dd3-47aa-a2cd-566e6b19b208, 9+ relevant
places): neither the structured classifier nor the primary extraction
prompt ever enumerates the actual valid enum codes for
operator_type/ownership_status/facility_type/organization_scope — the model
answers in natural-language business English ("Private company", "Private",
"nonprofit", "Regional (multi-location across Finland)") instead of the
internal snake_case codes. ``_normalize_choice`` in
maps_place_enrichment_service.py only casefolds and strips whitespace
before an exact-match check against the allowed set, so any of these
genuinely correct, evidence-backed answers is silently discarded to
``None`` — even though the underlying evidence was sound.

A second, more serious issue surfaced investigating the same places: for a
multi-location organization (A-klinikka Oy's nationwide network), the model
cited evidence about OTHER branches (e.g. "A-klinikkakoti Hämeenlinna and
Pyhtää offer around-the-clock supported housing") to confirm
``residential_accommodation=true`` for an unrelated branch (A Klinikka
Raahe, A-klinikka Kotka/Lappeenranta/Valkeakoski/Jyväskylä/Varkaus) whose
own page was never cited. Branch-specific fields (residential_
accommodation, inpatient_treatment) must never be confirmed from evidence
that is plainly about a *different* location.

This module intentionally does NOT try to resolve every possible phrase —
only the literal synonyms actually observed, or obviously equivalent
variants of them, and only when the underlying evidence_quote/source_url
clears the branch-specificity and hedge-language checks. Anything outside
that stays unresolved (``None``) rather than guessed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any

from app.db.models import (
    MapsFacilityType,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
)

# Keys are casefolded, whitespace-collapsed literal AI outputs observed in
# real runs (or obvious variants of them) — not a general-purpose synonym
# dictionary. Each maps to exactly one target enum value; nothing here
# infers ownership from a business name or domain alone (that evidence
# check happens separately, in the prompt and in the model's own evidence
# requirement) — this only translates an already-stated conclusion into
# the matching internal code.
OWNERSHIP_STATUS_SYNONYMS: dict[str, str] = {
    "private": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
    "private company": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
    "privately owned": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
    "nonprofit": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
    "non-profit": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
    "non profit": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
    "ngo": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
    "non-governmental": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
    "non-governmental organization": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
    "foundation": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
    "association": MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value,
    "government": MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value,
    "public": MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value,
    "state-owned": MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value,
    "state owned": MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value,
    "municipal": MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value,
    "probably private": MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value,
    "likely private": MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value,
    "probably non-government": MapsOwnershipStatus.PROBABLE_NON_GOVERNMENT.value,
}

OPERATOR_TYPE_SYNONYMS: dict[str, str] = {
    "private": MapsOperatorType.PRIVATE_COMPANY.value,
    "private company": MapsOperatorType.PRIVATE_COMPANY.value,
    "limited company": MapsOperatorType.PRIVATE_COMPANY.value,
    "nonprofit": MapsOperatorType.NONPROFIT.value,
    "non-profit": MapsOperatorType.NONPROFIT.value,
    "non profit": MapsOperatorType.NONPROFIT.value,
    "foundation": MapsOperatorType.NONPROFIT.value,
    "association": MapsOperatorType.ASSOCIATION.value,
    "public hospital": MapsOperatorType.PUBLIC_HOSPITAL.value,
    "government agency": MapsOperatorType.GOVERNMENT_AGENCY.value,
    "hospital system": MapsOperatorType.HOSPITAL_SYSTEM.value,
    "university": MapsOperatorType.UNIVERSITY.value,
}

ORGANIZATION_SCOPE_SYNONYMS: dict[str, str] = {
    "network": MapsOrganizationScope.NETWORK.value,
    "regional": MapsOrganizationScope.NETWORK.value,
    "regional (multi-location across finland)": MapsOrganizationScope.NETWORK.value,
    "nationwide": MapsOrganizationScope.NETWORK.value,
    "national network": MapsOrganizationScope.NETWORK.value,
    "multi-location": MapsOrganizationScope.NETWORK.value,
    "multiple locations": MapsOrganizationScope.NETWORK.value,
    "chain": MapsOrganizationScope.NETWORK.value,
    "single location": MapsOrganizationScope.FACILITY.value,
    "standalone": MapsOrganizationScope.FACILITY.value,
    "solo practice": MapsOrganizationScope.INDIVIDUAL_PRACTICE.value,
}

# facility_type is deliberately NOT given a broad synonym table — "addiction
# treatment facility/center" (the actual free-text seen in the Finland data)
# is too generic to map to any ONE specific enum value without guessing
# residential vs. outpatient vs. general. Only a handful of genuinely
# unambiguous keyword matches are accepted; everything else stays
# unresolved rather than guessed, per the conservative-normalization
# requirement.
#
# residential_addiction_rehab / inpatient_detox_center / therapeutic_community
# are ELIGIBLE_FACILITY_TYPES — compute_client_eligibility grants ELIGIBLE
# unconditionally once facility_type is one of these, with NO further check
# of residential_accommodation/inpatient_treatment. Mapping facility_type
# into one of them is therefore a MORE consequential decision than those
# two booleans, not a lesser one, and must pass the SAME branch-specific-
# evidence and hedge-language guards those booleans already get — see
# _normalize_facility_type_requires_evidence below. "Strong" keywords
# (containing the word "residential", or the specific phrase "therapeutic
# community") are self-evidently about on-site overnight capacity; "weak"
# keywords (a bare "detox" mention) are not — detox programs are commonly
# OUTPATIENT, so a bare "detox"/"detoxification" phrase additionally
# requires a corroborating on-site/overnight/bed-count signal in the same
# quote before it is trusted (see Suvera Oy: "addiction detoxification and
# rehabilitation unit" alone must not become inpatient_detox_center).
_STRONG_FACILITY_TYPE_KEYWORD_RULES: tuple[tuple[str, str], ...] = (
    ("therapeutic community", MapsFacilityType.THERAPEUTIC_COMMUNITY.value),
    ("residential rehab", MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value),
    ("residential addiction", MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value),
    ("residential rehabilitation", MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value),
    ("inpatient detox", MapsFacilityType.INPATIENT_DETOX_CENTER.value),
    ("residential detox", MapsFacilityType.INPATIENT_DETOX_CENTER.value),
)
_WEAK_FACILITY_TYPE_KEYWORD_RULES: tuple[tuple[str, str], ...] = (
    ("detoxification unit", MapsFacilityType.INPATIENT_DETOX_CENTER.value),
    ("detox", MapsFacilityType.INPATIENT_DETOX_CENTER.value),
)
# Non-residential targets never grant unconditional eligibility (outpatient
# is hard-excluded, or stays unresolved) — no extra guard needed for these.
_NO_GUARD_FACILITY_TYPE_KEYWORD_RULES: tuple[tuple[str, str], ...] = (
    ("outpatient addiction center", MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value),
    ("outpatient clinic", MapsFacilityType.OUTPATIENT_ADDICTION_CENTER.value),
)

RESIDENTIAL_GRANTING_FACILITY_TYPES = frozenset(
    {
        MapsFacilityType.RESIDENTIAL_ADDICTION_REHAB.value,
        MapsFacilityType.INPATIENT_DETOX_CENTER.value,
        MapsFacilityType.THERAPEUTIC_COMMUNITY.value,
    }
)

_RESIDENTIAL_CAPACITY_MARKERS = (
    "residential",
    "inpatient",
    "overnight",
    "24-hour",
    "24 hour",
    "24/7",
    "around-the-clock",
    "around the clock",
    "live-in",
)
_BED_COUNT_PATTERN = re.compile(r"\b\d{1,4}\s*-?\s*(bed|beds|paikka|paikkainen|paikkaa)\b")

_HEDGE_PHRASES = (
    "no explicit confirmation",
    "not explicitly confirmed",
    "not confirmed",
    "unconfirmed",
    "unclear",
    "often includes residential",
    "often include residential",
    "likely but unconfirmed",
    "but no explicit",
    "but no confirmation",
    "without explicit",
)


# Audit finding (Finland run, 22 proposed ownership recoveries): the
# overwhelming majority of the AI's OWN evidence_quote for ownership_
# status/operator_type was pure name-suffix or domain pattern-matching —
# "Name suffix 'Oy' indicates a private limited company", "The suffix
# 'ry' indicates nonprofit ownership", "Website URL domain livesaatio.fi
# indicates nonprofit foundation". These suffixes ARE real, legally
# mandated Finnish business-form markers, but a LEGAL FORM is not the
# same fact as OWNERSHIP — a Finnish municipality can and does own "Oy"
# (private limited company) entities, so "Oy" alone never proves non-
# government ownership, only that it is *structured* as a private
# limited company. A genuine ownership fact names an actual owner/parent/
# acquisition ("A-klinikka Oy is owned by Attendo", "...on siirtynyt
# virallisesti Attendon omistukseen" — officially transferred to
# Attendo's ownership) — that is accepted; bare suffix/name/domain
# pattern-matching, with no such fact, is not, even though the table
# above would otherwise map it.
_NAME_OR_DOMAIN_INFERENCE_MARKERS = (
    "name suffix",
    "suffix '",
    'suffix "',
    "name includes",
    "facility name",
    "name indicates",
    "name suggests",
    "website url domain",
    "domain indicates",
    "the name '",
    'the name "',
    "known private care operator",
    "wording ",
    "nimi '",
    'nimi "',
)
_GENUINE_OWNERSHIP_FACT_MARKERS = (
    "owned by",
    "is owned",
    "acquisition",
    "acquired by",
    "omistukseen",
    "omistaa",
    "parent company",
    "subsidiary of",
    "transferred to",
    "siirtynyt",
)


def is_name_or_domain_inference_only(quote: str | None) -> bool:
    """True when the evidence quote's OWN stated reasoning is purely a
    name/suffix/domain pattern match with no accompanying documented
    ownership fact — too weak to confirm actual ownership (see module-
    level note above). Never flags a quote that also names a real owner/
    acquisition/parent entity, even if it mentions the suffix too (e.g.
    "Company form 'Oy' and confirmed acquisition by private company
    Attendo" passes — it has a genuine fact alongside the suffix
    observation)."""
    folded = _fold(quote or "")
    if not folded:
        return False
    has_weak_marker = any(marker in folded for marker in _NAME_OR_DOMAIN_INFERENCE_MARKERS)
    has_strong_marker = any(marker in folded for marker in _GENUINE_OWNERSHIP_FACT_MARKERS)
    return has_weak_marker and not has_strong_marker


def normalize_ownership_or_operator_with_synonyms(
    raw_value: str | None,
    allowed: set[str],
    synonyms: dict[str, str],
    *,
    quote: str | None,
) -> str | None:
    """``normalize_choice_with_synonyms`` gated by
    ``is_name_or_domain_inference_only`` — used for ownership_status and
    operator_type specifically, where the risk of a name/suffix/domain-
    only inference masquerading as confirmed ownership is real (see
    module docstring)."""
    if is_name_or_domain_inference_only(quote):
        return None
    return normalize_choice_with_synonyms(raw_value, allowed, synonyms)


def _fold(text: str) -> str:
    """Casefold + collapse whitespace + strip Finnish/Scandinavian
    diacritics (ä/ö/å -> a/o/a) so a URL path like "paihdesairaala" matches
    the facility name "Päihdesairaala" despite the ASCII-only URL slug."""
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", ascii_only).strip().casefold()


# Scientific/BGN-style Cyrillic -> Latin transliteration, covering Russian
# and the Belarusian-specific letters (і, ў) -- enough to turn a genuine
# Cyrillic source quote into the SAME token spelling Google Maps already
# romanized the place's own name into (Belarus run 5acff6f7: canonical_name
# "Pravo Na Zhizn'" / quote "Право на жизнь"). Not phonetically perfect
# (е/э both -> e, ъ/ь dropped), but exact enough for token-overlap identity
# matching, which is all this is used for.
_CYRILLIC_TO_LATIN: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ў": "u", "ґ": "g", "є": "ye", "ї": "yi", "ʼ": "",
}
# Basic Greek -> Latin transliteration (ISO 843-ish), same purpose/caveats.
_GREEK_TO_LATIN: dict[str, str] = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i",
    "θ": "th", "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x",
    "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y",
    "φ": "f", "χ": "ch", "ψ": "ps", "ω": "o",
}
_TRANSLITERATION_MAP: dict[str, str] = {**_CYRILLIC_TO_LATIN, **_GREEK_TO_LATIN}


def _transliterate_to_latin(text: str) -> str:
    """Best-effort char-by-char transliteration of Cyrillic/Greek into
    Latin letters; any character outside those two maps (Latin, Arabic,
    digits, punctuation, ...) passes through unchanged. This is NOT a
    claim of phonetic correctness for every script -- Arabic in particular
    has no practical 1:1 Latin mapping -- it only needs to make the common
    "place name romanized by Google Maps, source quote in its native
    script" case produce matching tokens. Scripts this cannot transliterate
    still get identity-matched through other signals (address, website
    domain) in ``is_likely_branch_specific_evidence``.

    NFKD-decomposes and strips combining marks BEFORE mapping: an accented
    Greek vowel (e.g. "ί", tonos) is a single precomposed codepoint that
    is NOT the bare "ι" this module's Greek table maps -- decomposing it
    to base letter + combining accent (then dropping the accent, same as
    ``_fold``) first is what lets the table's single-entry-per-base-letter
    map actually match every accented form instead of silently passing
    the untransliterated precomposed character through unchanged.
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    base_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    folded = base_only.casefold()
    return "".join(_TRANSLITERATION_MAP.get(ch, ch) for ch in folded)


# Generic address words (both scripts) that must never, by themselves,
# establish facility identity -- only a specific street/building token can.
_GENERIC_ADDRESS_WORDS = {
    "street", "avenue", "road", "boulevard", "lane", "district", "oblast",
    "region", "ulitsa", "ulica", "prospekt", "pereulok",
    "улица", "ул", "проспект", "переулок", "область", "район", "аг",
}


def _unicode_word_tokens(text: str, *, min_length: int = 4) -> set[str]:
    """Script-agnostic tokenizer: letter runs in ANY script (Cyrillic,
    Greek, Arabic, ...), not just ASCII a-z. The old ASCII-only
    ``re.findall(r"[a-z]+", name)`` silently produced ZERO tokens for any
    place whose own stored name is non-Latin, making every branch-
    specific-evidence check fail closed even for a genuine same-script
    match."""
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_folded = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return {
        tok.casefold()
        for tok in re.findall(r"[^\W\d_]+", ascii_folded, re.UNICODE)
        if len(tok) >= min_length
    }


def normalize_choice_with_synonyms(
    raw_value: str | None, allowed: set[str], synonyms: dict[str, str]
) -> str | None:
    """Exact match against ``allowed`` first (unchanged strict behavior);
    falls back to the literal-synonym table only when that fails.

    Matches by substring containment (either direction), not just exact
    equality: ``MapsPlaceEnrichmentResult``'s fields are capped at 32
    characters (``TruncatingModel``), so a genuinely matching phrase like
    "Regional (multi-location across Finland)" (41 chars) already arrives
    here truncated to "Regional (multi-location across" by the time any
    normalization runs — an exact-equality lookup would never match it.
    Still never guesses outside the table: only ever maps to a value that
    some known synonym key is a substring of, or is a substring of."""
    if not raw_value:
        return None
    candidate = raw_value.strip().casefold()
    if candidate in allowed:
        return candidate
    if candidate in synonyms:
        return synonyms[candidate]
    for key, target in synonyms.items():
        if key in candidate or candidate in key:
            return target
    return None


def normalize_organization_scope_with_synonyms(
    raw_value: str | None,
    allowed: set[str],
    synonyms: dict[str, str],
    *,
    place: Any,
    quote: str | None,
    source_url: str | None,
) -> str | None:
    """``normalize_choice_with_synonyms`` with one override: a "network"/
    "regional"/"nationwide" recovery is downgraded to "facility" when the
    evidence is itself genuinely branch-specific (names THIS place, not
    just its parent).

    Root cause this closes (A-klinikkasäätiön Päihdesairaala, run
    0afe4ca7-2dd3-47aa-a2cd-566e6b19b208): its organization_scope evidence
    was "A-klinikkasäätiö is a national Finnish foundation operating
    multiple addiction services" — a true fact about the PARENT
    foundation's nationwide footprint, wrongly applied to this ONE,
    individually identified, single-address hospital. A single physical
    location is still a single "facility" no matter how large the
    network it belongs to is — exactly the same branch-vs-organization
    conflation already guarded against for residential_accommodation/
    inpatient_treatment and facility_type, just surfacing on a third
    field. Uses the lenient (city-or-name) branch check, matching the
    booleans — organization_scope is not itself an eligibility-granting
    field the way the three ELIGIBLE_FACILITY_TYPES are, so it does not
    need facility_type's stricter name-only requirement.
    """
    recovered = normalize_choice_with_synonyms(raw_value, allowed, synonyms)
    if recovered == MapsOrganizationScope.NETWORK.value and is_likely_branch_specific_evidence(
        place, quote=quote, source_url=source_url
    ):
        return MapsOrganizationScope.FACILITY.value
    return recovered


def confirms_onsite_residential_capacity(text: str | None) -> bool:
    """A corroborating signal that people are actually admitted and stay
    on-site — a specific bed/place count, or explicit residential/
    inpatient/24-hour wording — distinct from merely offering a clinical
    service (e.g. "detox") that commonly exists in outpatient form too."""
    folded = _fold(text or "")
    if any(marker in folded for marker in _RESIDENTIAL_CAPACITY_MARKERS):
        return True
    return bool(_BED_COUNT_PATTERN.search(folded))


def normalize_facility_type_with_keywords(
    raw_value: str | None,
    allowed: set[str],
    *,
    place: Any = None,
    quote: str | None = None,
    source_url: str | None = None,
) -> str | None:
    """Keyword-based facility_type recovery, gated for the three
    ELIGIBLE_FACILITY_TYPES (residential_addiction_rehab,
    inpatient_detox_center, therapeutic_community) by the SAME branch-
    specific-evidence and hedge-language guards the residential_
    accommodation/inpatient_treatment booleans get — mapping into one of
    these is a stronger, unconditional eligibility grant than either
    boolean on its own, so it must never be held to a weaker evidence bar.
    ``place``/``quote``/``source_url`` are optional for backward
    compatibility, but any candidate target in RESIDENTIAL_GRANTING_
    FACILITY_TYPES is rejected (returns None) unless all three are given
    and clear every guard — a caller that omits them gets the strict,
    unconditionally-safe outcome, never a silently ungated one.
    """
    if not raw_value:
        return None
    candidate = raw_value.strip().casefold()
    if candidate in allowed:
        return candidate

    target: str | None = None
    needs_corroboration = False
    for keyword, mapped in _STRONG_FACILITY_TYPE_KEYWORD_RULES:
        if keyword in candidate:
            target = mapped
            break
    if target is None:
        for keyword, mapped in _WEAK_FACILITY_TYPE_KEYWORD_RULES:
            if keyword in candidate:
                target = mapped
                needs_corroboration = True
                break
    if target is None:
        for keyword, mapped in _NO_GUARD_FACILITY_TYPE_KEYWORD_RULES:
            if keyword in candidate:
                return mapped
        return None

    if target not in RESIDENTIAL_GRANTING_FACILITY_TYPES:
        return target

    if place is None:
        return None
    if contains_hedge_language(quote):
        return None
    if needs_corroboration and not confirms_onsite_residential_capacity(quote):
        return None
    if not is_likely_branch_specific_evidence(
        place, quote=quote, source_url=source_url, require_name_match=True
    ):
        return None
    return target


def contains_hedge_language(text: str | None) -> bool:
    """True when the evidence quote itself hedges the claim it is
    supposedly supporting (e.g. "no explicit confirmation", "implies") —
    a boolean stamped True/False against hedged evidence is the model
    contradicting its own stated uncertainty, not a real confirmation."""
    if not text:
        return False
    folded = _fold(text)
    return any(phrase in folded for phrase in _HEDGE_PHRASES)


_LOCATION_INDEX_MARKERS = ("toimipisteet", "/locations/", "/branches/", "/offices/")


_GENERIC_NAME_WORDS = {
    "klinikka",
    "clinic",
    "center",
    "centre",
    "keskus",
    "hoito",
    "palvelu",
    "yksikko",
    "yksikkö",
    # Cyrillic generic facility words and their transliterations — the
    # exact same "klinikka"/"clinic" problem, just in Russian/Belarusian:
    # "центр" (center), "клиника"/"клиник" (clinic) appear in virtually
    # every rehab-facility name AND every unrelated one, so matching on
    # them alone (via _transliterate_to_latin) would wrongly treat any
    # two different Cyrillic-named facilities as the same place.
    "центр",
    "tsentr",
    "клиника",
    "клиник",
    "klinika",
    "klinik",
    "больница",
    "больниц",
    "bolnitsa",
    "bolnits",
}


def _place_tokens(place: Any) -> tuple[set[str], str]:
    """Returns (name_tokens, city_token) separately — callers that need a
    genuine name match (not just a shared city) must be able to tell the
    two apart; see ``is_likely_branch_specific_evidence``'s
    ``require_name_match``.

    ``name_tokens`` is Unicode-aware (any script, not just ASCII a-z) and
    includes both ``canonical_name`` and ``raw_name`` — Google Maps'
    romanization of a name sometimes drops letters the raw listing kept —
    plus each name's own Latin transliteration, so a Cyrillic/Greek
    source quote can still be matched against an already-romanized stored
    name (and vice versa). See ``_transliterate_to_latin``.
    """
    city = _fold(getattr(place, "city_name", None) or "")
    raw_names = (
        getattr(place, "canonical_name", None) or "",
        getattr(place, "raw_name", None) or "",
    )
    name_tokens: set[str] = set()
    for raw_name in raw_names:
        name_tokens |= _unicode_word_tokens(raw_name)
        name_tokens |= _unicode_word_tokens(_transliterate_to_latin(raw_name))
    # Generic chain-brand/category words would match on every branch
    # regardless of which one — matching on "klinikka" (clinic) proves
    # nothing about THIS specific branch.
    name_tokens = {tok for tok in name_tokens if tok not in _GENERIC_NAME_WORDS}
    return name_tokens, city


def _address_corroborates(place: Any, *, quote: str | None) -> bool:
    """True when a specific, non-generic token from this place's OWN
    stored address also appears in the evidence quote (e.g. a street
    name) — establishing place-specific identity even when the quote
    cites a parent/registered organization name that does not match the
    Google Maps listing name at all (Belarus run 5acff6f7: the facility-
    type evidence names the operating NGO and district office, not the
    listing's own name, but the SAME classification call's residential_
    accommodation evidence quotes this exact street address). A bare
    city/region/generic address word is never enough by itself — see
    ``_GENERIC_ADDRESS_WORDS`` — this still requires a specific,
    place-identifying token, never a broad area match.
    """
    if not quote:
        return False
    address = getattr(place, "formatted_address", None) or ""
    if not address:
        return False
    city = _fold(getattr(place, "city_name", None) or "")
    address_tokens = _unicode_word_tokens(address) - _GENERIC_ADDRESS_WORDS
    if city:
        address_tokens.discard(city)
    if not address_tokens:
        return False
    quote_tokens = _unicode_word_tokens(quote)
    return bool(address_tokens & quote_tokens)


def _website_domain_matches(place: Any, *, source_url: str | None) -> bool:
    """True when the evidence's source_url resolves to the SAME domain as
    this place's own already-verified website — the strongest possible
    identity signal, independent of name or script entirely. Only fires
    when this place actually HAS a stored website to compare against;
    never infers identity from an unrelated domain."""
    if not source_url:
        return False
    place_site = getattr(place, "official_website", None) or getattr(place, "raw_website", None)
    if not place_site:
        return False
    from urllib.parse import urlparse

    def _domain(url: str) -> str:
        if "//" not in url:
            url = f"//{url}"
        host = (urlparse(url).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host

    source_domain = _domain(source_url)
    place_domain = _domain(place_site)
    return bool(source_domain) and source_domain == place_domain


def is_likely_branch_specific_evidence(
    place: Any,
    *,
    quote: str | None,
    source_url: str | None,
    require_name_match: bool = False,
) -> bool:
    """True when the evidence plausibly describes THIS place rather than
    a *different*, specifically-named branch/entity of the same
    organization.

    Default (``require_name_match=False``, used for the residential_
    accommodation/inpatient_treatment booleans): trust the evidence — a
    single generic crawled-page quote about one facility is the normal
    case and must not be rejected for merely not repeating the facility's
    own name/city verbatim. A city-name match alone is enough. This only
    flips to False for the specific failure pattern that caused real
    harm: a chain operator's "branch locator" style URL (a Finnish
    "toimipisteet" — locations — index, or an equivalent /locations//
    /branches//offices/ path) naming a slug that does not match this
    place's own city/name at all — e.g. "a-klinikka.fi/toimipisteet/a-
    klinikkakoti-hameenlinna/" cited as evidence for the unrelated Raahe/
    Kotka/Lappeenranta/Valkeakoski/Jyväskylä/Varkaus branches, none of
    which are Hämeenlinna.

    ``require_name_match=True`` (used for facility_type's unconditional-
    eligibility-granting targets, a higher-stakes decision): a city-name
    match is NOT enough on its own — the quote/URL must name THIS
    specific facility (a real word from its own name), not merely share
    its city. This catches a different, equally real failure: Suvera Oy's
    "evidence" was entirely about "Avara Oulu", a differently-branded
    unit that merely happens to be in the same city (Oulu) as Suvera —
    sharing a city says nothing about whether they are the same place.
    When ``place``'s own name has no usable token at all (e.g. a bare
    chain-brand name like "A-KLINIKKA", where "klinikka" is filtered as
    generic), there is nothing specific to match and this returns False.

    Name matching is Unicode- and script-aware (direct token overlap, plus
    each side's Cyrillic/Greek->Latin transliteration — see
    ``_place_tokens``/``_transliterate_to_latin``), so a romanized stored
    name still matches a native-script source quote and vice versa
    (Belarus run 5acff6f7). When even that fails — the quote cites a
    parent/registered organization name that shares no token with the
    Google Maps listing name at all — a specific (non-generic) shared
    address token, or the source_url resolving to this SAME place's own
    already-verified website domain, is still genuine place-specific
    identity, not a generic keyword or an unrelated page; see
    ``_address_corroborates``/``_website_domain_matches``. None of these
    paths ever grant a match on a generic word, a shared city, or a
    merely-shared domain with a *different* branded entity — each still
    requires a signal specific to this exact place.

    An empty quote AND empty source_url always returns False — there is
    nothing to trust at all in that case.
    """
    folded_quote = _fold(quote or "")
    folded_url = _fold(source_url or "")
    if not folded_quote and not folded_url:
        return False

    name_tokens, city_token = _place_tokens(place)
    if require_name_match:
        if not name_tokens:
            return False
        if any(tok in folded_quote for tok in name_tokens):
            return True
        quote_variant_tokens = _unicode_word_tokens(
            quote or ""
        ) | _unicode_word_tokens(_transliterate_to_latin(quote or ""))
        if name_tokens & quote_variant_tokens:
            return True
        if _address_corroborates(place, quote=quote):
            return True
        return _website_domain_matches(place, source_url=source_url)

    tokens = name_tokens | ({city_token} if city_token else set())
    if any(tok in folded_quote or tok in folded_url for tok in tokens):
        return True

    if any(marker in folded_url for marker in _LOCATION_INDEX_MARKERS):
        # A branch-locator-style URL that does not name this place at all
        # (the token check above already failed) is evidence about some
        # OTHER specific branch, not a generic/ambiguous page.
        return False

    # No branch-locator URL pattern and nothing explicitly contradicts
    # this being about the place itself — the normal, legitimate case of
    # one generic quote from one facility's own crawled page.
    return True


# Jurisdiction-scoped legal forms whose non-government status is
# UNAMBIGUOUS within that specific country's own law — deliberately NOT a
# generic "association"/"NGO" keyword list, and deliberately never applied
# to a country other than the one each phrase was verified for (do not
# extrapolate one jurisdiction's legal designation to every country).
#
# Belarus ("общественное объединение" — "public association"): defined by
# Belarus's Law "On Public Associations" as a voluntary, non-profit,
# NON-GOVERNMENTAL organization — a legal-form fact, not an inference from
# wording. The hybrid state-public form ("государственно-общественное
# объединение") is deliberately NOT matched by these stems in isolation —
# see the exclusion check in the matching function below.
#
# Each entry is a tuple of STEM-groups (word stems, not full inflected
# forms): Russian/Belarusian decline every word by grammatical case
# ("общественное объединение" in a sentence's subject position vs.
# "общественного объединения" when it is the OBJECT of "district office
# OF the..." — exactly the real Belarus quote's own grammar). Matching on
# a stem common to every case ending, rather than one exact literal
# phrase, is what makes this actually work on real quoted sentences
# without needing a full morphological analyzer. Every stem in a group
# must be present for that group to count as a match.
_UNAMBIGUOUS_NON_GOVERNMENT_LEGAL_FORMS_BY_COUNTRY: dict[str, tuple[tuple[str, ...], ...]] = {
    "BY": (
        ("обществен", "объединен"),  # "общественное объединение", any case
    ),
}
# Hybrid state-public legal forms whose non-government status is NOT
# unambiguous — if the quote also contains one of these (for the same
# country), the match above is rejected regardless of the stems found.
_AMBIGUOUS_HYBRID_LEGAL_FORMS_BY_COUNTRY: dict[str, tuple[str, ...]] = {
    "BY": ("государственно-обществен",),
}


def recover_ownership_status_from_unambiguous_ngo_evidence(
    place: Any, *, country_code: str | None
) -> str | None:
    """Zero-AI-call, offline-only recovery of ``ownership_status`` from
    evidence ALREADY saved on ``place`` — never calls an AI provider,
    never scrapes anything new.

    For the Belarus run 5acff6f7 facility_type fix: the facility_type
    evidence quote for Belarus facility 32e01f47 explicitly names
    "Республиканского общественного объединения «Матери против
    наркотиков»" as the specific entity this facility is a district
    office OF — a genuine, source-grounded, place-specific ownership fact
    that was simply filed under the "facility_type" evidence key, not
    "ownership_status" (which never existed as its own evidence item
    before this fix added it to the Sonar schema — see
    ``maps_sonar_classify.resolve_sonar_ownership_status``). This scans
    EVERY already-saved evidence item's quote (not just one keyed
    "ownership_status") for one of this place's own country's exact,
    unambiguous legal-form phrases.

    Every one of the following must hold, or this returns ``None`` (never
    a guess, never REVIEW silently promoted on weak grounds):
      - ``country_code`` has an entry in the allowlist at all (a country
        with no entry never recovers anything through this path);
      - some evidence item's quote contains one of that country's exact
        phrases;
      - that quote is not hedged (``contains_hedge_language``);
      - that quote is source-grounded and ties the organization to THIS
        specific facility as its operator — not organization-wide, not a
        different branch, not a bare nearby mention
        (``is_likely_branch_specific_evidence`` with
        ``require_name_match=True``, same strict check facility_type's
        residential-granting path already requires).
    """
    if not country_code:
        return None
    code = country_code.strip().upper()
    stem_groups = _UNAMBIGUOUS_NON_GOVERNMENT_LEGAL_FORMS_BY_COUNTRY.get(code)
    if not stem_groups:
        return None
    hybrid_stems = _AMBIGUOUS_HYBRID_LEGAL_FORMS_BY_COUNTRY.get(code, ())
    evidence_map = getattr(place, "classification_evidence", None) or {}
    for item in evidence_map.values():
        if not isinstance(item, Mapping):
            continue
        quote = item.get("evidence_quote") or ""
        if not quote:
            continue
        folded_quote = _fold(quote)
        if any(_fold(stem) in folded_quote for stem in hybrid_stems):
            continue
        if not any(
            all(_fold(stem) in folded_quote for stem in group) for group in stem_groups
        ):
            continue
        if contains_hedge_language(quote):
            continue
        source_url = item.get("source_url") or ""
        if not is_likely_branch_specific_evidence(
            place, quote=quote, source_url=source_url, require_name_match=True
        ):
            continue
        return MapsOwnershipStatus.CONFIRMED_NON_GOVERNMENT.value
    return None


__all__ = [
    "OWNERSHIP_STATUS_SYNONYMS",
    "OPERATOR_TYPE_SYNONYMS",
    "ORGANIZATION_SCOPE_SYNONYMS",
    "RESIDENTIAL_GRANTING_FACILITY_TYPES",
    "confirms_onsite_residential_capacity",
    "contains_hedge_language",
    "is_likely_branch_specific_evidence",
    "is_name_or_domain_inference_only",
    "normalize_choice_with_synonyms",
    "normalize_facility_type_with_keywords",
    "normalize_organization_scope_with_synonyms",
    "normalize_ownership_or_operator_with_synonyms",
    "recover_ownership_status_from_unambiguous_ngo_evidence",
]
