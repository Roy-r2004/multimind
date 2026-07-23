"""Throughput profiles for scrape executions (standard vs full census)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

MODE_REAL = "real"
MODE_FULL_CENSUS = "full_census"
SUPPORTED_EXECUTION_MODES = {MODE_REAL, MODE_FULL_CENSUS}


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
    normalized = (mode or MODE_REAL).strip().lower()
    if normalized == MODE_FULL_CENSUS:
        return ScaleProfile(
            mode=MODE_FULL_CENSUS,
            label="Full census",
            serper_results_per_query=20,
            serper_max_queries_per_discovery=12,
            retrieval_max_per_cell=40,
            retrieval_max_per_execution=800,
            extraction_max_documents=250,
            extraction_max_chunks=800,
            publication_max_candidates=2000,
            discovery_query_hard_cap=16,
            discovery_results_hard_cap=20,
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
