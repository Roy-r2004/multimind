from types import SimpleNamespace

from app.schemas.api import SourceDiscoveryQueryPlan
from app.services.scraping.scale_profile import (
    CENSUS_PER_CELL_FETCH,
    DIR_PER_CELL_FETCH,
    MODE_DIRECTORY_FIRST,
    MODE_FULL_CENSUS,
    MODE_REAL,
    expected_pages_from_blueprint,
    resolve_dynamic_scale_profile,
    resolve_scale_profile,
    shrink_dimensions_for_directory_first,
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


def test_directory_first_scales_with_cell_count():
    # National × 2 local languages × 4 directory categories
    cells = 8
    profile = resolve_dynamic_scale_profile(
        MODE_DIRECTORY_FIRST,
        _settings(),
        cell_count=cells,
        expected_pages=None,
    )
    assert profile.mode == MODE_DIRECTORY_FIRST
    assert profile.label == "Directory-first"
    assert profile.retrieval_max_per_cell == DIR_PER_CELL_FETCH
    # Floor keeps enough budget for official seed retrieval.
    assert profile.retrieval_max_per_execution == max(cells * DIR_PER_CELL_FETCH, 250)
    assert profile.serper_max_queries_per_discovery <= profile.discovery_query_hard_cap


def test_directory_first_expected_pages_raises_budget():
    profile = resolve_dynamic_scale_profile(
        MODE_DIRECTORY_FIRST,
        _settings(),
        cell_count=8,
        expected_pages=5000,
    )
    assert profile.retrieval_max_per_execution == 5000
    assert profile.extraction_max_documents == 2500
    assert profile.publication_max_candidates == 10000


def test_directory_first_provisional_until_cells_known():
    profile = resolve_scale_profile(MODE_DIRECTORY_FIRST, _settings())
    assert profile.mode == MODE_DIRECTORY_FIRST
    assert profile.retrieval_max_per_execution == 0
    assert profile.extraction_max_documents == 1


def test_shrink_collapses_to_national_local_directory_categories():
    regions = [
        {"code": "W", "name": "Vienna"},
        {"code": "S", "name": "Salzburg"},
    ]
    languages = [
        {"code": "de", "name": "German"},
        {"code": "en", "name": "English"},
        {"code": "fr", "name": "French"},
        {"code": "it", "name": "Italian"},
    ]
    categories = [
        "clinic websites",
        "official registry",
        "private blogs",
        "licensed provider directory",
        "news articles",
        "health ministry list",
    ]
    out_regions, out_languages, out_categories = shrink_dimensions_for_directory_first(
        regions,
        languages,
        categories,
        country_code="at",
        country_name="Austria",
    )
    assert out_regions == [{"code": "AT", "name": "Austria"}]
    assert all(
        (lang.get("code") or "").casefold() not in {"en", "eng"} for lang in out_languages
    )
    assert len(out_languages) <= 2
    assert out_categories == [
        "official registry",
        "licensed provider directory",
        "health ministry list",
    ]


def test_shrink_dimensions_falls_back_to_default_directory_categories():
    out_regions, out_languages, categories = shrink_dimensions_for_directory_first(
        [{"code": "X", "name": "Somewhere"}],
        [{"code": "de", "name": "German"}],
        ["clinic websites", "news articles"],
        country_code="AT",
        country_name="Austria",
    )
    assert out_regions == [{"code": "AT", "name": "Austria"}]
    assert out_languages == [{"code": "de", "name": "German"}]
    assert "official registry" in categories
    assert "licensed provider directory" in categories


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
