from types import SimpleNamespace

from app.schemas.api import SourceDiscoveryQueryPlan
from app.services.scraping.scale_profile import (
    CENSUS_PER_CELL_FETCH,
    MODE_FULL_CENSUS,
    MODE_REAL,
    expected_pages_from_blueprint,
    resolve_dynamic_scale_profile,
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


def test_austria_sized_matrix_scales_fetch_without_clamps():
    # 9 regions × 1 language × 5 categories
    cells = 45
    profile = resolve_dynamic_scale_profile(
        MODE_FULL_CENSUS,
        _settings(),
        cell_count=cells,
        expected_pages=None,
    )
    assert profile.mode == MODE_FULL_CENSUS
    assert profile.retrieval_max_per_cell == CENSUS_PER_CELL_FETCH
    assert profile.retrieval_max_per_execution == cells * CENSUS_PER_CELL_FETCH
    assert profile.retrieval_max_per_execution == 1800
    assert profile.extraction_max_documents == 900
    assert profile.extraction_max_chunks == 2700
    assert profile.publication_max_candidates == 3600
    assert profile.serper_max_queries_per_discovery >= 10


def test_expected_pages_raises_budget_with_no_ceiling():
    profile = resolve_dynamic_scale_profile(
        MODE_FULL_CENSUS,
        _settings(),
        cell_count=45,
        expected_pages=5000,
    )
    assert profile.retrieval_max_per_execution == 5000
    assert profile.extraction_max_documents == 2500
    assert profile.publication_max_candidates == 10000


def test_expected_pages_from_blueprint():
    assert expected_pages_from_blueprint({"estimated_workload": {"expected_pages": 1200}}) == 1200
    assert expected_pages_from_blueprint({"estimated_workload": {}}) is None
    assert expected_pages_from_blueprint(None) is None


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
