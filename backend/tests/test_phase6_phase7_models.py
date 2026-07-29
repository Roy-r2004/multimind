"""Host-safe mapper and metadata coverage for Package A."""

from sqlalchemy import inspect
from sqlalchemy.orm import configure_mappers

from app.db.base import Base
from app.db import models


def test_all_scraping_mappers_configure() -> None:
    configure_mappers()
    scraping = [mapper for mapper in Base.registry.mappers
                if mapper.local_table.name.startswith(("scraping_", "rehabilitation_"))]
    assert scraping
    for mapper in scraping:
        assert mapper.persist_selectable is not None
        for relationship in mapper.relationships:
            assert relationship.mapper is not None
            assert relationship.primaryjoin is not None


def test_package_a_models_match_constraint_contract() -> None:
    expected = {
        "scraping_facility_phase_work_jobs": {
            "uq_facility_phase_job_fingerprint", "uq_facility_phase_job_owner",
            "ck_facility_phase_job_kind", "ck_facility_phase_job_status",
        },
        "scraping_facility_candidate_decisions": {
            "uq_facility_candidate_decision", "ck_facility_candidate_country_decision",
            "ck_facility_candidate_final_status",
        },
        "scraping_facility_candidate_duplicates": {
            "uq_facility_candidate_duplicate_pair",
            "ck_facility_candidate_duplicate_order",
            "ck_facility_candidate_duplicate_relationship",
        },
    }
    for table_name, names in expected.items():
        table = Base.metadata.tables[table_name]
        actual = {constraint.name for constraint in table.constraints if constraint.name}
        assert names <= actual
        assert {fk.column.table.name for fk in table.foreign_keys}
    decision = inspect(models.ScrapingFacilityCandidateDecision)
    assert {"facility_candidate_id", "canonical_candidate_id"} <= {
        column.key for column in decision.columns
    }


def test_provenance_columns_are_mapped() -> None:
    chunk = Base.metadata.tables["scraping_source_document_chunks"]
    evidence = Base.metadata.tables["scraping_facility_candidate_evidence"]
    candidate = Base.metadata.tables["scraping_facility_candidates"]
    assert {"retrieval_result_id", "crawl_node_id", "original_url",
            "representation_provenance"} <= set(chunk.c.keys())
    assert {"retrieval_result_id", "crawl_node_id", "source_url"} <= set(evidence.c.keys())
    assert "directory_observation_id" in candidate.c.keys()
