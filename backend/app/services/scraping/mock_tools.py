"""Deterministic mock scraping tool adapters.

These adapters intentionally never touch external networks or local browsers.
They produce stable structured outputs for the execution orchestrator to persist.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

from app.db.models import ScrapingBlueprint, ScrapingExecution, ScrapingTask


DEFAULT_SOURCE_CATEGORIES = [
    "government_registry",
    "medical_directory",
    "hospital_directory",
    "ngo_directory",
    "business_directory",
    "social_public",
    "pdf_document",
    "general_web",
]


@dataclass(frozen=True)
class CountryProfile:
    country_code: str
    country_name: str
    administrative_regions: list[dict[str, str | None]]
    languages: list[dict[str, str | None]]
    source_categories: list[str]
    terminology_hints: list[str]

    def as_json(self) -> dict[str, Any]:
        return {
            "mock": True,
            "country_code": self.country_code,
            "country_name": self.country_name,
            "administrative_regions": self.administrative_regions,
            "languages": self.languages,
            "source_categories": self.source_categories,
            "terminology_hints": self.terminology_hints,
        }


class CountryProfileProvider(Protocol):
    def build_profile(
        self, execution: ScrapingExecution, blueprint: ScrapingBlueprint
    ) -> CountryProfile: ...


class MockCountryProfileProvider:
    def build_profile(
        self, execution: ScrapingExecution, blueprint: ScrapingBlueprint
    ) -> CountryProfile:
        blueprint_json = blueprint.blueprint_json or {}
        scope = blueprint_json.get("scope") or {}
        source_strategy = blueprint_json.get("source_strategy") or []

        languages = _language_items(blueprint_json.get("languages") or [])
        if not languages and execution.country_code == "LB":
            languages = [
                {"code": "ar", "name": "Arabic"},
                {"code": "en", "name": "English"},
                {"code": "fr", "name": "French"},
            ]
        if not languages:
            languages = [{"code": "en", "name": "English"}]

        region_names = _dedupe(scope.get("regions") or [])
        if not region_names and execution.country_code == "LB":
            region_names = [
                "Beirut",
                "Mount Lebanon",
                "North Lebanon",
                "Akkar",
                "Beqaa",
                "Baalbek-Hermel",
                "South Lebanon",
                "Nabatieh",
            ]
        if not region_names:
            region_names = [execution.country_name]

        categories = _dedupe(
            item.get("source_type")
            for item in source_strategy
            if isinstance(item, dict) and item.get("source_type")
        )
        if not categories:
            categories = DEFAULT_SOURCE_CATEGORIES

        search_terms = blueprint_json.get("search_terms") or []
        terminology = _dedupe(
            item.get("term")
            for item in search_terms
            if isinstance(item, dict) and item.get("term")
        )

        return CountryProfile(
            country_code=execution.country_code,
            country_name=execution.country_name,
            administrative_regions=[
                {"code": _slug(region_name), "name": region_name} for region_name in region_names
            ],
            languages=languages,
            source_categories=categories,
            terminology_hints=terminology,
        )


class MockSearchTool:
    def run(self, task: ScrapingTask) -> dict[str, Any]:
        return {"mock": True, "queries": _queries(task)}


class MockHttpFetchTool:
    def run(self, task: ScrapingTask) -> dict[str, Any]:
        return {"mock": True, "fetched": True, "identifier": f"mock://http/{task.id}"}


class MockBrowserFetchTool:
    def run(self, task: ScrapingTask) -> dict[str, Any]:
        return {"mock": True, "rendered": False, "identifier": f"mock://browser/{task.id}"}


class MockDocumentParserTool:
    def run(self, task: ScrapingTask) -> dict[str, Any]:
        return {
            "mock": True,
            "documents_found": _stable_int(task.id, "docs", minimum=0, maximum=2),
        }


class MockSocialDiscoveryTool:
    def run(self, task: ScrapingTask) -> dict[str, Any]:
        return {
            "mock": True,
            "public_profiles": _stable_int(task.id, "social", minimum=0, maximum=2),
        }


class MockRecordExtractorTool:
    def run(self, task: ScrapingTask) -> dict[str, Any]:
        return {
            "mock": True,
            "records_extracted": _stable_int(task.id, "records", minimum=0, maximum=7),
        }


class MockEntityResolutionTool:
    def run(self, task: ScrapingTask) -> dict[str, Any]:
        return {
            "mock": True,
            "duplicates_detected": _stable_int(task.id, "dupes", minimum=0, maximum=3),
        }


class MockVerificationTool:
    def run(self, task: ScrapingTask) -> dict[str, Any]:
        return {
            "mock": True,
            "records_verified": _stable_int(task.id, "verified", minimum=0, maximum=6),
        }


class MockCoverageAuditTool:
    def run(self, task: ScrapingTask) -> dict[str, Any]:
        return {
            "mock": True,
            "gap_tasks_recommended": _stable_int(task.id, "gaps", minimum=0, maximum=2),
        }


def _language_items(values: list[Any]) -> list[dict[str, str | None]]:
    items: list[dict[str, str | None]] = []
    for value in values:
        name = str(value).strip()
        if not name:
            continue
        code = {
            "arabic": "ar",
            "english": "en",
            "french": "fr",
        }.get(name.lower())
        items.append({"code": code, "name": name})
    return items


def _dedupe(values: Any) -> list[str]:
    result: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in result:
            result.append(text)
    return result


def _queries(task: ScrapingTask) -> list[str]:
    payload = task.input_json or {}
    pieces = [
        payload.get("region_name"),
        payload.get("language_name"),
        payload.get("source_category"),
        "rehabilitation directory",
    ]
    return [" ".join(str(piece) for piece in pieces if piece)]


def _stable_int(*parts: str, minimum: int, maximum: int) -> int:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    span = maximum - minimum + 1
    return minimum + (int(digest[:8], 16) % span)


def _slug(value: str) -> str:
    return "-".join(value.lower().replace("_", " ").split())
