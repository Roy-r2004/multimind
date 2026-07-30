"""Shared result classification and completeness helpers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

SOCIAL_CONTACT_TYPES = {"facebook", "instagram", "linkedin", "tiktok", "x", "youtube"}
PHONE_CONTACT_TYPES = {"phone", "hotline", "whatsapp"}


def normalized_publication_class(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text == "verified":
        return "verified"
    if text == "excluded":
        return "excluded"
    return "review_required"


def bucket_label(value: Any) -> str:
    publication_class = normalized_publication_class(value)
    if publication_class == "review_required":
        return "review"
    return publication_class


def empty_result_counts() -> dict[str, int]:
    return {"verified": 0, "review": 0, "excluded": 0, "kept": 0}


def with_kept_total(counts: dict[str, int]) -> dict[str, int]:
    """``kept`` is the roster the UI shows: everything AI cleanup did not exclude."""
    counts["kept"] = counts["verified"] + counts["review"]
    return counts


def result_counts(facilities: Iterable[Any]) -> dict[str, int]:
    counts = empty_result_counts()
    for facility in facilities:
        counts[bucket_label(getattr(facility, "publication_class", None))] += 1
    return with_kept_total(counts)


def facility_completeness_percent(facility: Any) -> float:
    locations = list(getattr(facility, "locations", None) or [])
    contacts = list(getattr(facility, "contacts", None) or [])
    if locations:
        scores = [location_completeness_percent(location, contacts=contacts) for location in locations]
        return round(max(scores), 2)

    has_address = bool(getattr(facility, "primary_address", None))
    has_phone = any(_is_phone_contact(contact) for contact in contacts)
    return _completeness_score(has_address=has_address, has_phone=has_phone)


def execution_completeness_percent(facilities: Iterable[Any]) -> float:
    items = list(facilities)
    if not items:
        return 0.0
    return round(
        sum(facility_completeness_percent(facility) for facility in items) / len(items),
        2,
    )


def location_completeness_percent(location: Any, *, contacts: Iterable[Any]) -> float:
    has_address = bool(getattr(location, "full_address", None))
    location_id = getattr(location, "id", None)
    has_phone = any(
        _is_phone_contact(contact)
        and (
            getattr(contact, "location_id", None) == location_id
            or getattr(contact, "location_id", None) is None and getattr(contact, "is_primary", False)
        )
        for contact in contacts
    )
    return _completeness_score(has_address=has_address, has_phone=has_phone)


def primary_phone_for_location(location: Any, contacts: Iterable[Any]) -> str | None:
    location_id = getattr(location, "id", None)
    for contact in contacts:
        if not _is_phone_contact(contact):
            continue
        if getattr(contact, "location_id", None) == location_id and getattr(contact, "is_primary", False):
            return getattr(contact, "value", None)
    for contact in contacts:
        if not _is_phone_contact(contact):
            continue
        if getattr(contact, "location_id", None) == location_id:
            return getattr(contact, "value", None)
    return None


def primary_phone_for_facility(facility: Any) -> str | None:
    contacts = list(getattr(facility, "contacts", None) or [])
    for contact in contacts:
        if _is_phone_contact(contact) and getattr(contact, "is_primary", False):
            return getattr(contact, "value", None)
    for contact in contacts:
        if _is_phone_contact(contact):
            return getattr(contact, "value", None)
    return None


def _completeness_score(*, has_address: bool, has_phone: bool) -> float:
    score = 0.0
    if has_address:
        score += 50.0
    if has_phone:
        score += 50.0
    return score


def _is_phone_contact(contact: Any) -> bool:
    contact_type = str(getattr(contact, "contact_type", "") or "").strip().lower()
    return contact_type in PHONE_CONTACT_TYPES and contact_type not in SOCIAL_CONTACT_TYPES
