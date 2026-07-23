from types import SimpleNamespace

from app.schemas.api import SourceDiscoveryQueryPlan
from app.services.scraping.scale_profile import (
    MODE_FULL_CENSUS,
    MODE_REAL,
    resolve_scale_profile,
)
from app.services.scraping.source_discovery_service import _normalize_planned_query_payload


def _settings(**overrides):
    base = {
        "serper_search_results_per_query": 10,
        "serper_search_max_queries_per_discovery": 4,
        "source_retrieval_max_candidates_per_coverage_cell": 10,
        "source_retrieval_max_candidates_per_execution": 150,
        "facility_extraction_max_documents_per_execution": 50,
        "facility_extraction_max_chunks_per_execution": 120,
        "facility_publication_max_candidates_per_execution": 300,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_real_mode_uses_settings_values():
    profile = resolve_scale_profile(MODE_REAL, _settings())
    assert profile.mode == MODE_REAL
    assert profile.extraction_max_documents == 50
    assert profile.retrieval_max_per_execution == 150


def test_full_census_raises_throughput_far_above_standard():
    profile = resolve_scale_profile(MODE_FULL_CENSUS, _settings())
    assert profile.mode == MODE_FULL_CENSUS
    assert profile.extraction_max_documents >= 200
    assert profile.retrieval_max_per_execution >= 500
    assert profile.publication_max_candidates >= 1000
    assert profile.serper_max_queries_per_discovery >= 10


def test_query_plan_schema_accepts_full_census_query_counts():
    payload = _normalize_planned_query_payload(
        {
            "queries": [
                {
                    "query": f"rehab clinic austria {index}",
                    "language_code": "de",
                    "purpose": "Find directory pages " + ("x" * 400),
                }
                for index in range(12)
            ]
        },
        max_queries=12,
    )
    assert len(payload["queries"]) == 12
    assert len(payload["queries"][0]["purpose"]) <= 300
    plan = SourceDiscoveryQueryPlan.model_validate(payload)
    assert len(plan.queries) == 12
