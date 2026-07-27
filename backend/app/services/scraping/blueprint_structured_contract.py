"""Canonical Stage 2 structured-blueprint contract, skeleton, and safe normalization."""

from __future__ import annotations

import copy
import unicodedata
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import ValidationError as PydanticValidationError

from app.schemas.api import (
    STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V1,
    STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2,
    CountryMaximumCoverageStructuredBlueprint,
    CountryMaximumCoverageStructuredBlueprintV2,
    StructuredBlueprintAny,
)

REQUIRED_TOP_LEVEL_FIELDS_V1: tuple[str, ...] = (
    "country_dossier",
    "regions",
    "languages",
    "regulatory_sources",
    "commercial_sources",
    "query_matrix",
    "region_coverage_plan",
    "discovery_strategy",
    "crawl_strategy",
    "contact_completeness_strategy",
    "verification_rules",
    "country_containment_rules",
    "deduplication_rules",
    "confidence_model",
    "completion_criteria",
    "risks",
    "citations",
    "estimated_coverage",
    "weak_areas",
    "human_review_questions",
    "approval_recommendation",
)

REQUIRED_TOP_LEVEL_FIELDS_V2: tuple[str, ...] = (
    "schema_version",
    *REQUIRED_TOP_LEVEL_FIELDS_V1[:3],
    "language_profiles",
    "important_cities",
    "local_terminology",
    "inpatient_residential_terminology",
    "private_paid_terminology",
    "addiction_categories",
    *REQUIRED_TOP_LEVEL_FIELDS_V1[3:],
)

# Backward-compatible alias used by existing callers/tests.
REQUIRED_TOP_LEVEL_FIELDS: tuple[str, ...] = REQUIRED_TOP_LEVEL_FIELDS_V1

EMPTY_LIST_FIELDS_V1: frozenset[str] = frozenset(
    {
        "regions",
        "languages",
        "regulatory_sources",
        "commercial_sources",
        "query_matrix",
        "region_coverage_plan",
        "completion_criteria",
        "risks",
        "citations",
        "weak_areas",
        "human_review_questions",
    }
)

EMPTY_LIST_FIELDS_V2: frozenset[str] = EMPTY_LIST_FIELDS_V1
# Required v2 dimension lists must be present and non-empty from the model; never invent [].

EMPTY_LIST_FIELDS: frozenset[str] = EMPTY_LIST_FIELDS_V1

TOP_LEVEL_ALIASES: dict[str, str] = {
    "administrative_regions": "regions",
    "regional_coverage_plan": "region_coverage_plan",
    "risks_limitations": "risks",
    "expected_weak_areas": "weak_areas",
}

LANGUAGE_ALIAS_FIELDS: tuple[str, ...] = ("local_languages", "official_languages")

CITATION_LIST_FIELDS: frozenset[str] = frozenset(
    {"regulatory_sources", "commercial_sources", "citations"}
)

_UNSUPPORTED_BLUEPRINT_VERSION = (
    "Unsupported structured blueprint schema version. "
    "Accepted values are missing (historical v1), the string \"1\", or the string \"2\"."
)


def detect_structured_blueprint_schema_version(
    payload: Mapping[str, Any],
) -> Literal["1", "2"]:
    """Return explicit schema version. Missing key means historical v1; all else fails closed."""
    if not isinstance(payload, Mapping):
        raise ValueError("Structured blueprint must be an object.")
    if "schema_version" not in payload:
        return STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V1
    value = payload["schema_version"]
    if value == STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V1:
        return STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V1
    if value == STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2:
        return STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2
    raise ValueError(_UNSUPPORTED_BLUEPRINT_VERSION)


def normalize_token(value: str) -> str:
    """Deterministic NFC + casefold + whitespace collapse for identity matching."""
    normalized = unicodedata.normalize("NFC", value).strip()
    collapsed = " ".join(normalized.split())
    return collapsed.casefold()


def canonical_structured_blueprint_skeleton(
    *, schema_version: Literal["1", "2"] = STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2
) -> dict[str, Any]:
    """Compact exact field structure for the requested schema version."""
    strategy = {"summary": "<non-empty strategy summary>"}
    citation = {
        "url": "<https URL or null when unknown>",
        "title": "<source title or null>",
        "source_type": "<source type or null>",
        "notes": "<optional notes or null>",
    }
    base: dict[str, Any] = {
        "country_dossier": {
            "country_name": "<selected country name>",
            "country_iso3": "<ISO Alpha-3>",
            "continent": "<continent>",
        },
        "regions": ["<first-level administrative region>"],
        "languages": ["<official or commonly used language>"],
        "regulatory_sources": [citation],
        "commercial_sources": [citation],
        "query_matrix": [
            {
                "query": "<search query>",
                "language": "<query language>",
                "purpose": "<query purpose>",
            }
        ],
        "region_coverage_plan": [
            {
                "region_name": "<region name>",
                "coverage_actions": ["<coverage action>"],
            }
        ],
        "discovery_strategy": strategy,
        "crawl_strategy": strategy,
        "contact_completeness_strategy": strategy,
        "verification_rules": strategy,
        "country_containment_rules": strategy,
        "deduplication_rules": strategy,
        "confidence_model": strategy,
        "completion_criteria": ["<completion criterion>"],
        "risks": ["<risk or limitation>"],
        "citations": [citation],
        "estimated_coverage": strategy,
        "weak_areas": ["<expected weak area>"],
        "human_review_questions": ["<human review question>"],
        "approval_recommendation": {
            "ready": False,
            "reason": "<why ready or not ready>",
        },
    }
    if schema_version == STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V1:
        return base
    return {
        "schema_version": STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2,
        **base,
        "language_profiles": [
            {
                "name": "<language display name>",
                "code": None,
                "script": None,
            }
        ],
        "important_cities": [
            {
                "name": "<important city name>",
                "region_name": "<parent first-level region from regions>",
            }
        ],
        "local_terminology": ["<local rehabilitation or addiction term>"],
        "inpatient_residential_terminology": ["<inpatient or residential term>"],
        "private_paid_terminology": ["<private or paid term>"],
        "addiction_categories": ["<addiction category>"],
    }


def normalize_structured_blueprint_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply deterministic, unambiguous alias and default normalization only.

    Never mutates the caller-provided dictionary. Never invents required v2 dimension lists.
    Unknown schema_version values fail closed via detect_structured_blueprint_schema_version.
    """
    if not isinstance(payload, dict):
        raise ValueError("Structured blueprint must be an object.")
    data = copy.deepcopy(payload)
    version = detect_structured_blueprint_schema_version(data)
    _promote_target_into_country_dossier(data)
    _apply_top_level_aliases(data)
    _merge_language_aliases(data)
    _fill_regions_from_coverage_plan(data)
    _drop_known_alias_keys(data)

    # Optional/historical list fields may default to []; required v2 dimensions must be explicit.
    for field in EMPTY_LIST_FIELDS_V1:
        if field not in data or data[field] is None:
            data[field] = []

    if version == STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2:
        data["schema_version"] = STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2
        _normalize_v2_dimension_fields(data)
    elif "schema_version" in data and data["schema_version"] == STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V1:
        # Keep explicit "1" when provided; do not invent a version for missing-key historical v1.
        data["schema_version"] = STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V1
    else:
        # Historical payloads omit schema_version.
        data.pop("schema_version", None)

    for field in CITATION_LIST_FIELDS:
        value = data.get(field)
        if isinstance(value, list):
            data[field] = [_normalize_citation_item(item) for item in value if isinstance(item, dict)]

    return data


def parse_structured_blueprint(payload: dict[str, Any] | None) -> StructuredBlueprintAny:
    """Validate as v1 or v2 without upgrading v1 into v2."""
    if not isinstance(payload, dict):
        raise ValueError("Structured blueprint must be an object.")
    version = detect_structured_blueprint_schema_version(payload)
    normalized = normalize_structured_blueprint_payload(payload)
    if version == STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2:
        return CountryMaximumCoverageStructuredBlueprintV2.model_validate(normalized)
    return CountryMaximumCoverageStructuredBlueprint.model_validate(normalized)


def validate_structured_blueprint_for_campaign(payload: dict[str, Any] | None) -> (
    CountryMaximumCoverageStructuredBlueprintV2
):
    """New mission campaigns, approvals, and edits require a complete v2 structured blueprint."""
    if not isinstance(payload, dict):
        raise ValueError(
            "An approved structured blueprint schema version 2 is required to start a "
            "Step-3-capable mission campaign. Regenerate or reapprove the blueprint under "
            "the v2 structured contract."
        )
    version = detect_structured_blueprint_schema_version(payload)
    if version != STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2:
        raise ValueError(
            "An approved structured blueprint schema version 2 is required to start a "
            "Step-3-capable mission campaign. Regenerate or reapprove the blueprint under "
            "the v2 structured contract."
        )
    return CountryMaximumCoverageStructuredBlueprintV2.model_validate(
        normalize_structured_blueprint_payload(payload)
    )


def describe_validation_contract_gap(
    payload: dict[str, Any] | None,
    exc: Exception,
    *,
    expected_schema_version: Literal["1", "2"] | None = None,
) -> dict[str, Any]:
    """Exact missing/extra field guidance for the Stage 2 repair request."""
    version: Literal["1", "2"]
    if expected_schema_version is not None:
        version = expected_schema_version
    elif isinstance(payload, Mapping):
        try:
            version = detect_structured_blueprint_schema_version(payload)
        except ValueError:
            version = STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2
    else:
        version = STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2

    required = (
        REQUIRED_TOP_LEVEL_FIELDS_V2
        if version == STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2
        else REQUIRED_TOP_LEVEL_FIELDS_V1
    )
    missing_fields: list[str] = []
    extra_fields: list[str] = []
    if isinstance(exc, PydanticValidationError):
        for error in exc.errors():
            loc = ".".join(str(part) for part in error.get("loc", ()))
            msg = str(error.get("msg", ""))
            if error.get("type") == "missing" or "Field required" in msg:
                missing_fields.append(loc or "<root>")
            if error.get("type") == "extra_forbidden" or "Extra inputs are not permitted" in msg:
                extra_fields.append(loc or "<root>")
            if error.get("type") == "too_short" and loc and loc not in missing_fields:
                missing_fields.append(loc)
    present = sorted(payload.keys()) if isinstance(payload, dict) else []
    absent_required = [field for field in required if field not in (payload or {})]
    for field in absent_required:
        if field not in missing_fields:
            missing_fields.append(field)
    if (
        version == STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2
        and isinstance(payload, dict)
        and payload.get("schema_version") != STRUCTURED_BLUEPRINT_SCHEMA_VERSION_V2
        and "schema_version" not in missing_fields
    ):
        missing_fields.insert(0, "schema_version")
    return {
        "missing_fields": missing_fields,
        "extra_fields": extra_fields,
        "present_top_level_fields": present,
        "required_top_level_fields": list(required),
        "schema_version": version,
        "canonical_skeleton": canonical_structured_blueprint_skeleton(schema_version=version),
    }


def _normalize_v2_dimension_fields(data: dict[str, Any]) -> None:
    for field in (
        "local_terminology",
        "inpatient_residential_terminology",
        "private_paid_terminology",
        "addiction_categories",
        "languages",
        "regions",
    ):
        value = data.get(field)
        if isinstance(value, list):
            data[field] = _dedupe_normalized_strings(
                [item for item in value if isinstance(item, str)]
            )

    profiles = data.get("language_profiles")
    if isinstance(profiles, list):
        cleaned_profiles: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for item in profiles:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            normalized_name = unicodedata.normalize("NFC", name).strip()
            key = normalize_token(normalized_name)
            if key in seen_names:
                continue
            seen_names.add(key)
            code = item.get("code")
            script = item.get("script")
            cleaned_profiles.append(
                {
                    "name": normalized_name,
                    "code": (
                        unicodedata.normalize("NFC", code).strip()
                        if isinstance(code, str) and code.strip()
                        else None
                    ),
                    "script": (
                        unicodedata.normalize("NFC", script).strip()
                        if isinstance(script, str) and script.strip()
                        else None
                    ),
                }
            )
        data["language_profiles"] = cleaned_profiles

    cities = data.get("important_cities")
    if isinstance(cities, list):
        cleaned_cities: list[dict[str, Any]] = []
        seen_pairs: set[tuple[str, str]] = set()
        for item in cities:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            region_name = item.get("region_name")
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(region_name, str) or not region_name.strip():
                continue
            city_name = unicodedata.normalize("NFC", name).strip()
            parent = unicodedata.normalize("NFC", region_name).strip()
            pair = (normalize_token(city_name), normalize_token(parent))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            cleaned_cities.append({"name": city_name, "region_name": parent})
        data["important_cities"] = cleaned_cities


def _dedupe_normalized_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        item = unicodedata.normalize("NFC", raw).strip()
        if not item:
            continue
        key = normalize_token(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _promote_target_into_country_dossier(data: dict[str, Any]) -> None:
    target = data.get("target")
    if not isinstance(target, dict):
        return
    dossier = data.get("country_dossier")
    if not isinstance(dossier, dict):
        dossier = {}
        data["country_dossier"] = dossier
    if "country_name" not in dossier and isinstance(target.get("country_name"), str):
        dossier["country_name"] = target["country_name"]
    if "country_iso3" not in dossier:
        iso3 = target.get("iso3") or target.get("country_iso3")
        if isinstance(iso3, str):
            dossier["country_iso3"] = iso3
    if "continent" not in dossier and isinstance(target.get("continent"), str):
        dossier["continent"] = target["continent"]


def _apply_top_level_aliases(data: dict[str, Any]) -> None:
    for alias, canonical in TOP_LEVEL_ALIASES.items():
        if canonical in data or alias not in data:
            continue
        data[canonical] = data[alias]


def _drop_known_alias_keys(data: dict[str, Any]) -> None:
    for alias in TOP_LEVEL_ALIASES:
        data.pop(alias, None)
    for alias in LANGUAGE_ALIAS_FIELDS:
        data.pop(alias, None)
    data.pop("target", None)
    data.pop("template_version", None)
    data.pop("mission_title", None)


def _merge_language_aliases(data: dict[str, Any]) -> None:
    merged: list[str] = []
    existing = data.get("languages")
    if isinstance(existing, list):
        for item in existing:
            if isinstance(item, str) and item.strip() and item not in merged:
                merged.append(item)
    for key in LANGUAGE_ALIAS_FIELDS:
        value = data.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip() and item not in merged:
                    merged.append(item)
    if merged:
        data["languages"] = merged


def _fill_regions_from_coverage_plan(data: dict[str, Any]) -> None:
    if "regions" in data and data["regions"] is not None:
        return
    plan = data.get("region_coverage_plan") or data.get("regional_coverage_plan")
    if not isinstance(plan, list):
        return
    regions: list[str] = []
    for item in plan:
        if isinstance(item, dict):
            name = item.get("region_name") or item.get("name")
            if isinstance(name, str) and name.strip() and name not in regions:
                regions.append(name)
    if regions:
        data["regions"] = regions


def _normalize_citation_item(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    if "title" not in out and isinstance(out.get("name"), str):
        out["title"] = out["name"]
    out.pop("name", None)
    if "source_type" not in out and isinstance(out.get("type"), str):
        out["source_type"] = out["type"]
    out.pop("type", None)
    url = out.get("url")
    if url is None or (isinstance(url, str) and not url.strip()):
        out["url"] = None
    for key in list(out.keys()):
        if key not in {"url", "title", "source_type", "notes"}:
            out.pop(key, None)
    return out
