"""Canonical Stage 2 structured-blueprint contract, skeleton, and safe normalization."""

from __future__ import annotations

import copy
from typing import Any

from pydantic import ValidationError

REQUIRED_TOP_LEVEL_FIELDS: tuple[str, ...] = (
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

EMPTY_LIST_FIELDS: frozenset[str] = frozenset(
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


def canonical_structured_blueprint_skeleton() -> dict[str, Any]:
    """Compact exact field structure expected by CountryMaximumCoverageStructuredBlueprint."""
    strategy = {"summary": "<non-empty strategy summary>"}
    citation = {
        "url": "<https URL or null when unknown>",
        "title": "<source title or null>",
        "source_type": "<source type or null>",
        "notes": "<optional notes or null>",
    }
    return {
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


def normalize_structured_blueprint_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply deterministic, unambiguous alias and default normalization only."""
    data = copy.deepcopy(payload)
    if not isinstance(data, dict):
        return data

    _promote_target_into_country_dossier(data)
    _apply_top_level_aliases(data)
    _merge_language_aliases(data)
    _fill_regions_from_coverage_plan(data)
    _drop_known_alias_keys(data)

    for field in EMPTY_LIST_FIELDS:
        if field not in data or data[field] is None:
            data[field] = []

    for field in CITATION_LIST_FIELDS:
        value = data.get(field)
        if isinstance(value, list):
            data[field] = [_normalize_citation_item(item) for item in value if isinstance(item, dict)]

    return data


def describe_validation_contract_gap(
    payload: dict[str, Any] | None, exc: Exception
) -> dict[str, Any]:
    """Exact missing/extra field guidance for the Stage 2 repair request."""
    missing_fields: list[str] = []
    extra_fields: list[str] = []
    if isinstance(exc, ValidationError):
        for error in exc.errors():
            loc = ".".join(str(part) for part in error.get("loc", ()))
            msg = str(error.get("msg", ""))
            if error.get("type") == "missing" or "Field required" in msg:
                missing_fields.append(loc or "<root>")
            if error.get("type") == "extra_forbidden" or "Extra inputs are not permitted" in msg:
                extra_fields.append(loc or "<root>")
    present = sorted(payload.keys()) if isinstance(payload, dict) else []
    absent_required = [field for field in REQUIRED_TOP_LEVEL_FIELDS if field not in (payload or {})]
    for field in absent_required:
        if field not in missing_fields:
            missing_fields.append(field)
    return {
        "missing_fields": missing_fields,
        "extra_fields": extra_fields,
        "present_top_level_fields": present,
        "required_top_level_fields": list(REQUIRED_TOP_LEVEL_FIELDS),
        "canonical_skeleton": canonical_structured_blueprint_skeleton(),
    }


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
    # Drop unknown extras that commonly appear alongside aliases.
    for key in list(out.keys()):
        if key not in {"url", "title", "source_type", "notes"}:
            out.pop(key, None)
    return out
