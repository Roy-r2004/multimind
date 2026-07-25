"""Assess whether a facility/location is inside the mission target country."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.scraping.countries import COUNTRIES, resolve_country

# ITU calling-code prefixes used as weak phone-country signals (longest-match).
_PHONE_PREFIXES: list[tuple[str, str]] = sorted(
    [
        ("43", "AT"),
        ("49", "DE"),
        ("33", "FR"),
        ("377", "MC"),
        ("39", "IT"),
        ("41", "CH"),
        ("34", "ES"),
        ("351", "PT"),
        ("31", "NL"),
        ("32", "BE"),
        ("48", "PL"),
        ("420", "CZ"),
        ("421", "SK"),
        ("36", "HU"),
        ("40", "RO"),
        ("359", "BG"),
        ("385", "HR"),
        ("386", "SI"),
        ("372", "EE"),
        ("371", "LV"),
        ("370", "LT"),
        ("358", "FI"),
        ("46", "SE"),
        ("47", "NO"),
        ("45", "DK"),
        ("44", "GB"),
        ("353", "IE"),
        ("1", "US"),
        ("61", "AU"),
        ("64", "NZ"),
        ("81", "JP"),
        ("82", "KR"),
        ("86", "CN"),
        ("91", "IN"),
        ("972", "IL"),
        ("971", "AE"),
        ("966", "SA"),
        ("20", "EG"),
        ("212", "MA"),
        ("216", "TN"),
        ("213", "DZ"),
        ("27", "ZA"),
        ("55", "BR"),
        ("52", "MX"),
        ("54", "AR"),
        ("56", "CL"),
        ("57", "CO"),
    ],
    key=lambda item: -len(item[0]),
)

# Neighbor pairs for leakage tests (not exhaustive).
_NEIGHBORS: dict[str, set[str]] = {
    "FR": {"BE", "LU", "DE", "CH", "IT", "ES", "AD", "MC"},
    "MC": {"FR"},
    "AT": {"DE", "CZ", "SK", "HU", "SI", "IT", "CH", "LI"},
    "DE": {"AT", "FR", "CH", "BE", "NL", "LU", "DK", "PL", "CZ"},
    "EE": {"LV", "RU", "FI"},
}


@dataclass(frozen=True)
class CountryContainmentResult:
    status: str  # confirmed_target | uncertain | confirmed_outside | legacy_unassessed
    reason: str
    signals: dict[str, Any] = field(default_factory=dict)
    publication_class: str = "review_required"  # verified | review_required | excluded


def assess_country_containment(
    *,
    target_country_code: str,
    extracted_country_code: str | None = None,
    extracted_country_raw: str | None = None,
    address_text: str | None = None,
    phone_values: list[str] | None = None,
    website_host: str | None = None,
    country_source: str = "execution_scope",
) -> CountryContainmentResult:
    """Graduated containment. Execution country alone is not proof of a local site."""
    target = resolve_country(target_country_code)
    signals: dict[str, Any] = {
        "target_country_code": target.code,
        "country_source": country_source,
        "extracted_country_code": extracted_country_code,
        "extracted_country_raw": extracted_country_raw,
        "address_text_present": bool((address_text or "").strip()),
        "phone_count": len(phone_values or []),
        "website_host": website_host,
    }

    # Strong: explicit extracted country conflicts with mission.
    if extracted_country_code and extracted_country_code.upper() != target.code:
        signals["conflict"] = "extracted_country_mismatch"
        return CountryContainmentResult(
            status="confirmed_outside",
            reason=(
                f"Extracted country {extracted_country_code.upper()} conflicts with "
                f"target {target.code}"
            ),
            signals=signals,
            publication_class="excluded",
        )

    address = (address_text or "").casefold()
    foreign_name_hits = _foreign_country_name_hits(address, target.code)
    if foreign_name_hits:
        signals["foreign_country_names_in_address"] = foreign_name_hits
        return CountryContainmentResult(
            status="confirmed_outside",
            reason=f"Address text references foreign country: {', '.join(foreign_name_hits)}",
            signals=signals,
            publication_class="excluded",
        )

    phone_countries = {_infer_phone_country(phone) for phone in (phone_values or [])}
    phone_countries.discard(None)
    signals["phone_inferred_countries"] = sorted(phone_countries)  # type: ignore[arg-type]
    foreign_phones = {code for code in phone_countries if code != target.code}
    if foreign_phones and not (address and target.name.casefold() in address):
        # Foreign phone without local address proof → outside or uncertain.
        if extracted_country_code == target.code or (address and _looks_local(address, target)):
            signals["phone_conflict_soft"] = sorted(foreign_phones)
        else:
            signals["conflict"] = "foreign_phone_prefix"
            return CountryContainmentResult(
                status="confirmed_outside",
                reason=f"Phone prefix indicates {', '.join(sorted(foreign_phones))}",
                signals=signals,
                publication_class="excluded",
            )

    tld_country = _tld_country(website_host)
    if tld_country:
        signals["website_tld_country"] = tld_country

    local_address = bool(address) and _looks_local(address, target)
    strong_match = extracted_country_code == target.code
    weak_tld_ok = tld_country in {None, target.code} or tld_country in _NEIGHBORS.get(target.code, set())

    if strong_match and (local_address or phone_countries <= {target.code, None}):
        return CountryContainmentResult(
            status="confirmed_target",
            reason="Extracted country matches target with supporting location/contact signals",
            signals=signals,
            publication_class="verified",
        )

    if local_address and (not foreign_phones) and weak_tld_ok:
        return CountryContainmentResult(
            status="confirmed_target",
            reason="Address text supports target country without contradictory signals",
            signals=signals,
            publication_class="verified",
        )

    # Execution-scope-only or weak signals → uncertain (review), never auto-verified.
    if country_source == "execution_scope" and not local_address and not strong_match:
        return CountryContainmentResult(
            status="uncertain",
            reason="Only mission/execution country available; physical site not proven in-country",
            signals=signals,
            publication_class="review_required",
        )

    if strong_match or local_address:
        return CountryContainmentResult(
            status="uncertain",
            reason="Partial in-country signals; needs review",
            signals=signals,
            publication_class="review_required",
        )

    return CountryContainmentResult(
        status="uncertain",
        reason="Insufficient evidence to confirm physical location in target country",
        signals=signals,
        publication_class="review_required",
    )


def _looks_local(address: str, target) -> bool:
    if target.name.casefold() in address:
        return True
    if target.code.casefold() in address.split():
        return True
    # Common city/region tokens are not modeled offline; postal-like digits alone are weak.
    return False


def _foreign_country_name_hits(address: str, target_code: str) -> list[str]:
    if not address:
        return []
    hits: list[str] = []
    for code, country in COUNTRIES.items():
        if code == target_code:
            continue
        name = country.name.casefold()
        if len(name) < 4:
            continue
        if name in address:
            hits.append(country.name)
    return hits[:5]


def _infer_phone_country(value: str) -> str | None:
    digits = "".join(ch for ch in value if ch.isdigit())
    if value.strip().startswith("00"):
        digits = digits[2:] if digits.startswith("00") else digits
    elif value.strip().startswith("+"):
        pass
    else:
        return None
    for prefix, code in _PHONE_PREFIXES:
        if digits.startswith(prefix):
            return code
    return None


def _tld_country(host: str | None) -> str | None:
    if not host:
        return None
    host = host.lower().strip(".")
    parts = host.split(".")
    if len(parts) < 2:
        return None
    tld = parts[-1]
    if len(tld) == 2 and tld.upper() in COUNTRIES:
        # Special cases: .uk → GB, .com not a country
        if tld == "uk":
            return "GB"
        return tld.upper()
    return None
