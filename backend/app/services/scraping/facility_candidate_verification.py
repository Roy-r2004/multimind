"""Deterministic Phase 7 normalization, country verification and identity rules."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from app.services.scraping.countries import resolve_country


class CountryDecision(StrEnum):
    INSIDE = "inside_requested_country"
    OUTSIDE = "outside_requested_country"
    UNCERTAIN = "uncertain"


class CandidateFinalStatus(StrEnum):
    ACCEPTED = "accepted"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


@dataclass(frozen=True)
class CountryEvidence:
    kind: str
    value: str
    source: str


@dataclass(frozen=True)
class CountryVerification:
    decision: CountryDecision
    reason_code: str
    evidence: tuple[CountryEvidence, ...]


_SPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_PHONE_EXT = re.compile(r"(?:ext\.?|extension|x)\s*\d+\s*$", re.I)
_FACILITY_TYPES = {
    "rehab": "rehabilitation_center",
    "rehabilitation centre": "rehabilitation_center",
    "rehabilitation center": "rehabilitation_center",
    "addiction treatment center": "addiction_treatment_center",
    "addiction treatment centre": "addiction_treatment_center",
    "detox": "detoxification_center",
    "detox center": "detoxification_center",
    "clinic": "clinic",
    "hospital": "hospital",
    "residential treatment": "residential_treatment",
    "outpatient": "outpatient_program",
}


def canonical_hash(payload: object) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def normalize_text(value: str | None) -> str | None:
    if not value:
        return None
    text = unicodedata.normalize("NFKC", value).strip()
    return _SPACE.sub(" ", text) or None


def normalize_name(value: str | None) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    folded = unicodedata.normalize("NFKD", text.casefold())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return _SPACE.sub(" ", _NON_WORD.sub(" ", folded)).strip() or None


def normalize_email(value: str | None) -> str | None:
    text = normalize_text(value)
    if not text or text.count("@") != 1:
        return None
    local, domain = text.rsplit("@", 1)
    return f"{local.casefold()}@{domain.casefold().rstrip('.')}" if local and domain else None


def normalize_phone(value: str | None) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    text = _PHONE_EXT.sub("", text)
    prefix = "+" if text.startswith("+") else ""
    digits = "".join(ch for ch in text if ch.isdigit())
    return f"{prefix}{digits}" if len(digits) >= 6 else None


def normalize_website(value: str | None) -> tuple[str | None, str | None]:
    text = normalize_text(value)
    if not text:
        return None, None
    candidate = text if "://" in text else f"https://{text}"
    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").casefold().rstrip(".")
    except ValueError:
        return None, None
    if not host:
        return None, None
    port = f":{parts.port}" if parts.port and parts.port not in {80, 443} else ""
    path = re.sub(r"/+", "/", parts.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit(("https", host + port, path, parts.query, "")), host.removeprefix("www.")


def normalize_address(value: str | None) -> str | None:
    text = normalize_text(value)
    return text.casefold().strip(" ,.;") if text else None


def normalize_city_region(value: str | None) -> str | None:
    return normalize_name(value)


def normalize_facility_type(value: str | None) -> str | None:
    key = normalize_name(value)
    return _FACILITY_TYPES.get(key, key.replace(" ", "_") if key else None)


def verify_country(
    requested_country: str,
    evidence: Iterable[CountryEvidence],
) -> CountryVerification:
    requested = resolve_country(requested_country)
    usable: list[tuple[int, CountryEvidence, str]] = []
    priority = {
        "full_address": 0,
        "coordinates": 1,
        "official_registration": 2,
        "explicit_physical_location": 3,
        "city_or_region": 4,
    }
    ignored = {"domain_suffix", "page_language", "hosting_country", "phone_prefix"}
    for item in evidence:
        if item.kind in ignored or item.kind not in priority:
            continue
        country = _country_from_value(item.value)
        if country:
            usable.append((priority[item.kind], item, country))
    if not usable:
        return CountryVerification(CountryDecision.UNCERTAIN, "no_reliable_physical_country_evidence", ())
    best_priority = min(row[0] for row in usable)
    best = [row for row in usable if row[0] == best_priority]
    countries = {row[2] for row in best}
    retained = tuple(row[1] for row in sorted(usable, key=lambda row: row[0]))
    if len(countries) != 1:
        return CountryVerification(CountryDecision.UNCERTAIN, "conflicting_physical_country_evidence", retained)
    observed = next(iter(countries))
    if observed == requested.iso2:
        return CountryVerification(CountryDecision.INSIDE, f"physical_evidence_matches_{requested.iso2}", retained)
    return CountryVerification(CountryDecision.OUTSIDE, f"physical_evidence_identifies_{observed}", retained)


def final_status(
    country: CountryVerification,
    *,
    has_name: bool,
    has_physical_locator: bool,
) -> tuple[CandidateFinalStatus, str]:
    if country.decision == CountryDecision.OUTSIDE:
        return CandidateFinalStatus.REJECTED, country.reason_code
    if not has_name:
        return CandidateFinalStatus.REJECTED, "missing_facility_name"
    if country.decision == CountryDecision.UNCERTAIN or not has_physical_locator:
        return CandidateFinalStatus.NEEDS_REVIEW, country.reason_code
    return CandidateFinalStatus.ACCEPTED, "supported_identity_inside_requested_country"


def identity_fingerprint(
    *, name: str | None, address: str | None, website: str | None,
    phone: str | None, email: str | None,
) -> str:
    canonical_url, domain = normalize_website(website)
    payload = {
        "name": normalize_name(name),
        "address": normalize_address(address),
        "domain": domain,
        "website": _canonical_website_identity(canonical_url, domain),
        "phone": normalize_phone(phone),
        "email": normalize_email(email),
    }
    return canonical_hash(payload)


def exact_identity_keys(
    *, name: str | None, address: str | None, website: str | None,
    phone: str | None, email: str | None,
) -> set[str]:
    normalized_name = normalize_name(name)
    canonical_url, domain = normalize_website(website)
    website_identity = _canonical_website_identity(canonical_url, domain)
    keys: set[str] = set()
    for kind, value in (
        ("email", normalize_email(email)),
        ("phone", normalize_phone(phone)),
        ("website", website_identity),
        ("name_address", f"{normalized_name}|{normalize_address(address)}" if normalized_name and address else None),
        ("name_domain", f"{normalized_name}|{domain}" if normalized_name and domain else None),
    ):
        if value:
            keys.add(f"{kind}:{value}")
    return keys


def _canonical_website_identity(
    canonical_url: str | None, canonical_domain: str | None
) -> str | None:
    if not canonical_url or not canonical_domain:
        return None
    parts = urlsplit(canonical_url)
    port = f":{parts.port}" if parts.port and parts.port not in {80, 443} else ""
    return urlunsplit(
        ("https", canonical_domain + port, parts.path, parts.query, "")
    )


def probable_duplicate_score(left: dict[str, str | None], right: dict[str, str | None]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    left_name, right_name = normalize_name(left.get("name")), normalize_name(right.get("name"))
    if left_name and left_name == right_name:
        reasons.append("same_normalized_name")
    _, left_domain = normalize_website(left.get("website"))
    _, right_domain = normalize_website(right.get("website"))
    if left_domain and left_domain == right_domain:
        reasons.append("same_domain")
    if normalize_phone(left.get("phone")) and normalize_phone(left.get("phone")) == normalize_phone(right.get("phone")):
        reasons.append("same_phone")
    if normalize_city_region(left.get("city")) and normalize_city_region(left.get("city")) == normalize_city_region(right.get("city")):
        reasons.append("same_city_or_region")
    weights = {"same_normalized_name": .45, "same_domain": .35, "same_phone": .45, "same_city_or_region": .15}
    return min(1.0, sum(weights[item] for item in reasons)), reasons


def _country_from_value(value: str) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    try:
        return resolve_country(text).iso2
    except Exception:
        pass
    folded = text.casefold()
    # Country-name containment is deterministic and intentionally conservative.
    matches = []
    from app.services.scraping.countries import COUNTRIES
    for country in COUNTRIES.values():
        if re.search(rf"(?<!\w){re.escape(country.name.casefold())}(?!\w)", folded):
            matches.append(country.iso2)
    return matches[0] if len(set(matches)) == 1 else None
