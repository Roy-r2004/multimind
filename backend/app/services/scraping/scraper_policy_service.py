"""Build locked execution policy snapshots and country blueprint defaults."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.services.scraping.countries import resolve_country

MISSION_PROFILE_FULL_NATIONAL_CENSUS = "full_national_census"
MISSION_PROFILE_PRIVATE_RESIDENTIAL = "private_residential"
SUPPORTED_MISSION_PROFILES = {
    MISSION_PROFILE_FULL_NATIONAL_CENSUS,
    MISSION_PROFILE_PRIVATE_RESIDENTIAL,
}
SCRAPER_POLICY_ID = "scraper-policy-v1"

_DEFAULT_LANGUAGE_MAP: dict[str, list[dict[str, str]]] = {
    "FR": [{"code": "fr", "name": "French"}, {"code": "en", "name": "English"}],
    "LB": [
        {"code": "ar", "name": "Arabic"},
        {"code": "fr", "name": "French"},
        {"code": "en", "name": "English"},
    ],
    "AT": [{"code": "de", "name": "German"}, {"code": "en", "name": "English"}],
}
_DEFAULT_PHONE_PATTERNS: dict[str, list[str]] = {
    "FR": [r"^\+33", r"^0[1-9]"],
    "LB": [r"^\+961", r"^0[1-9]"],
    "AT": [r"^\+43", r"^0[1-9]"],
}


@dataclass(frozen=True)
class ScraperPolicyBundle:
    mission_profile: str
    policy_snapshot: dict[str, Any]
    country_blueprint: dict[str, Any]


def build_policy_bundle(
    *,
    mission_title: str,
    mission_prompt: str,
    country_code: str,
    country_name: str | None,
    requested_mission_profile: str | None = None,
    blueprint_json: dict[str, Any] | None = None,
) -> ScraperPolicyBundle:
    country = resolve_country(country_code)
    mission_profile = resolve_mission_profile(
        requested=requested_mission_profile,
        mission_title=mission_title,
        mission_prompt=mission_prompt,
    )
    effective_country_name = country_name or country.name
    country_blueprint = build_country_blueprint(
        country_code=country.code,
        country_name=effective_country_name,
        mission_profile=mission_profile,
        blueprint_json=blueprint_json,
    )
    policy_snapshot = build_policy_snapshot(
        country_code=country.code,
        country_name=effective_country_name,
        mission_profile=mission_profile,
        country_blueprint=country_blueprint,
    )
    return ScraperPolicyBundle(
        mission_profile=mission_profile,
        policy_snapshot=policy_snapshot,
        country_blueprint=country_blueprint,
    )


def resolve_mission_profile(
    *,
    requested: str | None,
    mission_title: str,
    mission_prompt: str,
) -> str:
    normalized = str(requested or "").strip().lower()
    if normalized in SUPPORTED_MISSION_PROFILES:
        return normalized
    searchable = f"{mission_title}\n{mission_prompt}".casefold()
    if "private" in searchable and any(
        token in searchable for token in ("residential", "inpatient", "clinic", "facility")
    ):
        return MISSION_PROFILE_PRIVATE_RESIDENTIAL
    return MISSION_PROFILE_FULL_NATIONAL_CENSUS


def build_country_blueprint(
    *,
    country_code: str,
    country_name: str,
    mission_profile: str,
    blueprint_json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw_blueprint = blueprint_json if isinstance(blueprint_json, dict) else {}
    scope = raw_blueprint.get("scope") if isinstance(raw_blueprint.get("scope"), dict) else {}
    regions = _existing_regions(scope.get("regions"))
    if not regions:
        regions = [country_name]
    languages = _DEFAULT_LANGUAGE_MAP.get(
        country_code,
        [{"code": "en", "name": "English"}],
    )
    registries = [
        f"{country_name} health ministry registry",
        f"{country_name} licensed facility directory",
        f"{country_name} provider association listings",
    ]
    if mission_profile == MISSION_PROFILE_PRIVATE_RESIDENTIAL:
        registries.append(f"{country_name} private residential treatment directories")
    return {
        "country_code": country_code,
        "country_name": country_name,
        "mission_profile": mission_profile,
        "regions": [{"code": _slug(region), "name": region} for region in regions],
        "languages": languages,
        "registries": registries,
        "phone_patterns": _DEFAULT_PHONE_PATTERNS.get(country_code, [r"^\+"]),
    }


def build_policy_snapshot(
    *,
    country_code: str,
    country_name: str,
    mission_profile: str,
    country_blueprint: dict[str, Any],
) -> dict[str, Any]:
    settings = get_settings()
    return {
        "policy_id": SCRAPER_POLICY_ID,
        "country_code": country_code,
        "country_name": country_name,
        "mission_profile": mission_profile,
        "hard_gates": [
            "Target-country evidence must agree with the mission country.",
            "Do not publish facilities without a verified name.",
            "Country-specific defaults may expand languages, regions, registries, and phone patterns but cannot weaken hard gates.",
        ],
        "exclusions": [
            "Exclude wrong-country facilities.",
            "Exclude fictional or clearly mock facilities from real exports.",
        ],
        "security": {
            "robots_policy": settings.source_retrieval_robots_policy,
            "bounded_js_rendering": True,
            "allow_private_networks": False,
        },
        "discovery_defaults": {
            "registries": list(country_blueprint.get("registries") or []),
            "phone_patterns": list(country_blueprint.get("phone_patterns") or []),
            "languages": [item["code"] for item in country_blueprint.get("languages") or []],
        },
    }


def enrich_blueprint_payload(
    payload: dict[str, Any],
    *,
    mission_title: str,
    mission_prompt: str,
    country_code: str,
    country_name: str,
) -> dict[str, Any]:
    bundle = build_policy_bundle(
        mission_title=mission_title,
        mission_prompt=mission_prompt,
        country_code=country_code,
        country_name=country_name,
        blueprint_json=payload,
    )
    enriched = dict(payload)
    scope = dict(enriched.get("scope") or {})
    scope["countries"] = [country_name]
    if not _existing_regions(scope.get("regions")):
        scope["regions"] = [item["name"] for item in bundle.country_blueprint["regions"]]
    enriched["scope"] = scope

    if _looks_generic_languages(enriched.get("languages")):
        enriched["languages"] = [item["name"] for item in bundle.country_blueprint["languages"]]

    source_strategy = list(enriched.get("source_strategy") or [])
    seen_sources = {
        str(item.get("source_type") or "").strip().casefold()
        for item in source_strategy
        if isinstance(item, dict)
    }
    priority = len(source_strategy) + 1
    for registry_name in bundle.country_blueprint["registries"]:
        if registry_name.casefold() in seen_sources:
            continue
        source_strategy.append(
            {
                "source_type": registry_name,
                "priority": priority,
                "trust_tier": "high",
                "purpose": "country blueprint seeded registry source",
                "required": priority == 1,
            }
        )
        priority += 1
    enriched["source_strategy"] = source_strategy

    enriched["verification_rules"] = _dedupe_text_items(
        [
            *(enriched.get("verification_rules") or []),
            "Require address and phone corroboration before marking a facility verified.",
            "Use country phone patterns and location evidence as verification gates.",
        ]
    )
    enriched["compliance_rules"] = _dedupe_text_items(
        [
            *(enriched.get("compliance_rules") or []),
            "Respect robots policy and bounded JS-render allowlists.",
            "Do not weaken hard country-containment rules.",
        ]
    )
    enriched["policy_snapshot"] = bundle.policy_snapshot
    enriched["country_blueprint"] = bundle.country_blueprint
    return enriched


def _existing_regions(value: Any) -> list[str]:
    items: list[str] = []
    for entry in value if isinstance(value, list) else []:
        text = str(entry or "").strip()
        if text and text.casefold() != "unknown":
            items.append(text)
    return items


def _looks_generic_languages(value: Any) -> bool:
    items = [str(item or "").strip().casefold() for item in value if isinstance(value, list)]
    return not items or items == ["english"] or items == ["unknown"]


def _dedupe_text_items(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _slug(value: str) -> str:
    collapsed = "-".join(value.casefold().split())
    cleaned = "".join(char for char in collapsed if char.isalnum() or char == "-").strip("-")
    return cleaned or "region"
