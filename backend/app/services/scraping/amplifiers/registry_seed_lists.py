"""Registry seed helpers derived from country blueprint defaults."""

from __future__ import annotations

from typing import Any


def build_registry_seed_list(
    *,
    country_name: str,
    country_blueprint: dict[str, Any] | None,
    source_strategy: list[dict[str, Any]] | None = None,
) -> list[str]:
    seeds: list[str] = []
    for value in (country_blueprint or {}).get("registries") or []:
        text = str(value or "").strip()
        if text:
            seeds.append(text)
    for item in source_strategy or []:
        if not isinstance(item, dict):
            continue
        source_type = str(item.get("source_type") or "").strip()
        if source_type:
            seeds.append(source_type)
    if not seeds:
        seeds = [
            f"{country_name} health ministry registry",
            f"{country_name} licensed provider directory",
            f"{country_name} rehabilitation center directory",
            f"{country_name} addiction treatment registry",
        ]
    return _dedupe(seeds)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result
