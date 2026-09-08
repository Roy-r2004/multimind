"""Field-level locks so manual Phase 2 edits survive later AI enrichment."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

EDITABLE_EXPORT_FIELDS = frozenset(
    {
        "canonical_name",
        "addictions_treated",
        "formatted_address",
        "languages_spoken",
        "official_website",
        "contact_email",
        "international_phone_number",
        "treatment_price",
        "bed_count",
    }
)


def overridden_fields(place: Any) -> set[str]:
    raw = getattr(place, "manual_field_overrides", None) or []
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def is_manually_overridden(place: Any, field_name: str) -> bool:
    return field_name in overridden_fields(place)


def set_unless_overridden(place: Any, field_name: str, value: Any) -> bool:
    """Assign ``place.field_name = value`` unless the user locked that field.

    Returns True when the write happened.
    """
    if is_manually_overridden(place, field_name):
        return False
    setattr(place, field_name, value)
    return True


def add_override_fields(place: Any, field_names: Iterable[str]) -> None:
    current = [str(item) for item in (place.manual_field_overrides or []) if str(item).strip()]
    seen = set(current)
    for name in field_names:
        if name in seen:
            continue
        if name not in EDITABLE_EXPORT_FIELDS:
            continue
        current.append(name)
        seen.add(name)
    place.manual_field_overrides = current
