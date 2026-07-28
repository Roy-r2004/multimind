"""Non-Docker mapper/metadata coverage for Phase 4 Slice 3 relationships.

Proves ScrapingSourceCandidate ↔ ScrapingCrawlNode composite-ownership linkage
configures without overlapping-relationship SAWarnings, while preserving:
- DB composite FK columns (crawl_node_id, organization_id, execution_id)
- explicit foreign_keys limited to crawl_node_id for ORM write sync
- org/execution equality in primaryjoin for isolation on load
"""

from __future__ import annotations

import warnings

from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import configure_mappers

from app.db.models import ScrapingCrawlNode, ScrapingSourceCandidate


def _overlap_conflict_warnings(caught: list[warnings.WarningMessage]) -> list[str]:
    messages: list[str] = []
    for warning in caught:
        if not issubclass(warning.category, SAWarning):
            continue
        text = str(warning.message)
        if "conflict" not in text.lower():
            continue
        if (
            "ScrapingSourceCandidate.organization" in text
            or "ScrapingSourceCandidate.execution" in text
            or "ScrapingCrawlNode.source_candidates" in text
        ):
            messages.append(text)
    return messages


def test_candidate_crawl_node_mapper_configures_without_overlap_warnings() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SAWarning)
        configure_mappers()

    assert _overlap_conflict_warnings(list(caught)) == []


def test_candidate_crawl_node_relationship_preserves_composite_ownership_semantics() -> None:
    configure_mappers()

    candidate_crawl = ScrapingSourceCandidate.__mapper__.relationships["crawl_node"]
    node_candidates = ScrapingCrawlNode.__mapper__.relationships["source_candidates"]

    assert candidate_crawl.back_populates == "source_candidates"
    assert node_candidates.back_populates == "crawl_node"

    # ORM sync target is only crawl_node_id (not organization_id / execution_id).
    cand_fk_cols = {column.key for column in candidate_crawl._user_defined_foreign_keys}
    node_fk_cols = {column.key for column in node_candidates._user_defined_foreign_keys}
    assert cand_fk_cols == {"crawl_node_id"}
    assert node_fk_cols == {"crawl_node_id"}

    # Composite ownership FK remains on the table (DB-enforced isolation).
    composite_fk = next(
        fk
        for fk in ScrapingSourceCandidate.__table__.foreign_key_constraints
        if fk.name == "fk_source_candidates_crawl_node_org_exec"
    )
    assert {column.name for column in composite_fk.columns} == {
        "crawl_node_id",
        "organization_id",
        "execution_id",
    }

    # primaryjoin includes org/execution equality (load-side isolation).
    join_sql = str(candidate_crawl.primaryjoin)
    assert "organization_id" in join_sql
    assert "execution_id" in join_sql

    # Direct ownership relationships still target their simple FK columns.
    org_rel = ScrapingSourceCandidate.__mapper__.relationships["organization"]
    exec_rel = ScrapingSourceCandidate.__mapper__.relationships["execution"]
    assert {column.key for column in org_rel._user_defined_foreign_keys} == {
        "organization_id"
    }
    assert {column.key for column in exec_rel._user_defined_foreign_keys} == {
        "execution_id"
    }
