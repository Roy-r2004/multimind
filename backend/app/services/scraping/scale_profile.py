"""Throughput profiles for scrape executions (standard vs full census)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

MODE_REAL = "real"
MODE_FULL_CENSUS = "full_census"
SUPPORTED_EXECUTION_MODES = {MODE_REAL, MODE_FULL_CENSUS}

CENSUS_PER_CELL_FETCH = 40
CENSUS_RESULTS_PER_QUERY = 20
CENSUS_MAX_QUERIES_PER_DISCOVERY = 12
CENSUS_DISCOVERY_QUERY_HARD_CAP = 16
CENSUS_DISCOVERY_RESULTS_HARD_CAP = 20


@dataclass(frozen=True)
class ScaleProfile:
    mode: str
    label: str
    serper_results_per_query: int
    serper_max_queries_per_discovery: int
    retrieval_max_per_cell: int
    retrieval_max_per_execution: int
    extraction_max_documents: int
    extraction_max_chunks: int
    publication_max_candidates: int
    discovery_query_hard_cap: int
    discovery_results_hard_cap: int

    def as_metadata(self) -> dict[str, Any]:
        return asdict(self)


def resolve_scale_profile(mode: str, settings: Any) -> ScaleProfile:
    """Resolve a mode profile. Full census without mission size is provisional only."""
    normalized = (mode or MODE_REAL).strip().lower()
    if normalized == MODE_FULL_CENSUS:
        # Provisional until orchestrator knows coverage dimensions / expected_pages.
        return resolve_dynamic_scale_profile(
            MODE_FULL_CENSUS,
            settings,
            cell_count=0,
            expected_pages=None,
        )
    return ScaleProfile(
        mode=MODE_REAL,
        label="Standard",
        serper_results_per_query=max(settings.serper_search_results_per_query, 1),
        serper_max_queries_per_discovery=max(settings.serper_search_max_queries_per_discovery, 1),
        retrieval_max_per_cell=max(settings.source_retrieval_max_candidates_per_coverage_cell, 0),
        retrieval_max_per_execution=max(settings.source_retrieval_max_candidates_per_execution, 0),
        extraction_max_documents=max(settings.facility_extraction_max_documents_per_execution, 1),
        extraction_max_chunks=max(settings.facility_extraction_max_chunks_per_execution, 1),
        publication_max_candidates=max(
            settings.facility_publication_max_candidates_per_execution, 1
        ),
        discovery_query_hard_cap=8,
        discovery_results_hard_cap=20,
    )


def resolve_dynamic_scale_profile(
    mode: str,
    settings: Any,
    *,
    cell_count: int,
    expected_pages: int | None = None,
) -> ScaleProfile:
    """Size Full census budgets from mission coverage / blueprint pages — no product clamps."""
    normalized = (mode or MODE_REAL).strip().lower()
    if normalized != MODE_FULL_CENSUS:
        return resolve_scale_profile(mode, settings)

    cells = max(int(cell_count or 0), 0)
    pages_hint = max(int(expected_pages or 0), 0)
    retrieval_max_per_execution = max(pages_hint, cells * CENSUS_PER_CELL_FETCH)
    extraction_max_documents = max(1, retrieval_max_per_execution // 2)
    return ScaleProfile(
        mode=MODE_FULL_CENSUS,
        label="Full census",
        serper_results_per_query=CENSUS_RESULTS_PER_QUERY,
        serper_max_queries_per_discovery=CENSUS_MAX_QUERIES_PER_DISCOVERY,
        retrieval_max_per_cell=CENSUS_PER_CELL_FETCH,
        retrieval_max_per_execution=retrieval_max_per_execution,
        extraction_max_documents=extraction_max_documents,
        extraction_max_chunks=extraction_max_documents * 3,
        publication_max_candidates=extraction_max_documents * 4,
        discovery_query_hard_cap=CENSUS_DISCOVERY_QUERY_HARD_CAP,
        discovery_results_hard_cap=CENSUS_DISCOVERY_RESULTS_HARD_CAP,
    )


def expected_pages_from_blueprint(blueprint_json: dict[str, Any] | None) -> int | None:
    if not isinstance(blueprint_json, dict):
        return None
    workload = blueprint_json.get("estimated_workload")
    if not isinstance(workload, dict):
        return None
    raw = workload.get("expected_pages")
    if raw is None:
        return None
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return None


def scale_profile_from_country_profile(
    mode: str,
    settings: Any,
    country_profile_json: dict[str, Any] | None,
) -> ScaleProfile | None:
    """Restore a previously persisted dynamic budget snapshot, if present."""
    if not isinstance(country_profile_json, dict):
        return None
    budget = country_profile_json.get("scale_budget")
    if not isinstance(budget, dict):
        return None
    try:
        return ScaleProfile(
            mode=str(budget.get("mode") or mode),
            label=str(budget.get("label") or "Full census"),
            serper_results_per_query=int(budget["serper_results_per_query"]),
            serper_max_queries_per_discovery=int(budget["serper_max_queries_per_discovery"]),
            retrieval_max_per_cell=int(budget["retrieval_max_per_cell"]),
            retrieval_max_per_execution=int(budget["retrieval_max_per_execution"]),
            extraction_max_documents=int(budget["extraction_max_documents"]),
            extraction_max_chunks=int(budget["extraction_max_chunks"]),
            publication_max_candidates=int(budget["publication_max_candidates"]),
            discovery_query_hard_cap=int(budget["discovery_query_hard_cap"]),
            discovery_results_hard_cap=int(budget["discovery_results_hard_cap"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
