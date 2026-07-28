"""SQLite + model-metadata coverage for Phase 4 Slice 1 migration 029."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.db.models import (
    CrawlEdgeRelationshipType,
    CrawlNodeSourceClassification,
    ScrapingCrawlEdge,
    ScrapingCrawlNode,
    ScrapingSourceCandidate,
    ScrapingSourceDiscoveryQuery,
    SourceDiscoveryQueryStatus,
)


def _migration_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "029_phase4_discovery_claims_and_crawl_graph.py"
    )
    spec = importlib.util.spec_from_file_location("migration_029_phase4", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_post_028_tables(metadata: sa.MetaData) -> None:
    # Stub parents so SQLite batch_alter reflection can follow FKs on downgrade.
    sa.Table(
        "organizations",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    sa.Table(
        "scraping_executions",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    sa.Table(
        "scraping_source_discovery_queries",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=True),
        sa.Column("coverage_cell_id", sa.String(36), nullable=True),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("country_name", sa.String(120), nullable=False),
        sa.Column("region_code", sa.String(32), nullable=True),
        sa.Column("region_name", sa.String(160), nullable=True),
        sa.Column("language_code", sa.String(16), nullable=False),
        sa.Column("language_name", sa.String(120), nullable=False),
        sa.Column("source_category", sa.String(120), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("purpose", sa.String(80), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("discovery_round", sa.Integer(), nullable=False),
        sa.Column("generation_ordinal", sa.Integer(), nullable=False),
        sa.Column("query_job_fingerprint", sa.String(64), nullable=True),
        sa.Column("plan_hash_snapshot", sa.String(64), nullable=True),
        sa.Column("scope_level", sa.String(32), nullable=False),
        sa.Column("important_city", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "scraping_source_candidates",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=True),
        sa.Column("coverage_cell_id", sa.String(36), nullable=True),
        sa.Column("discovery_query_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_result_id", sa.String(255), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("canonical_url", sa.String(2048), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("snippet", sa.String(1000), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("country_name", sa.String(120), nullable=False),
        sa.Column("region_code", sa.String(32), nullable=True),
        sa.Column("region_name", sa.String(160), nullable=False),
        sa.Column("language_code", sa.String(16), nullable=False),
        sa.Column("language_name", sa.String(120), nullable=False),
        sa.Column("source_category", sa.String(120), nullable=False),
        sa.Column("initial_relevance_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("initial_trust_tier", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def _insert_pending_job(connection, *, row_id: str = "q-pending") -> None:
    connection.execute(
        text(
            "INSERT INTO scraping_source_discovery_queries ("
            "id, organization_id, execution_id, country_code, country_name, "
            "region_name, language_code, language_name, source_category, query_text, "
            "status, result_count, metadata_json, "
            "purpose, priority, discovery_round, generation_ordinal, scope_level, "
            "query_job_fingerprint, plan_hash_snapshot, important_city, "
            "provider, requested_at, created_at, updated_at"
            ") VALUES ("
            ":id, 'org-1', 'exec-1', 'LB', 'Lebanon', NULL, 'en', 'English', "
            "'regulatory', 'pending job', "
            "'pending', 0, '{}', "
            "'seed_source_discovery', 100, 1, 0, 'countrywide', "
            "'fp-pending-1', 'planhash', NULL, "
            "NULL, NULL, "
            "'2026-01-01 00:00:00', '2026-01-01 00:00:00'"
            ")"
        ),
        {"id": row_id},
    )


def test_029_revision_chain_linear() -> None:
    migration = _migration_module()
    assert migration.revision == "029"
    assert migration.down_revision == "028"


def test_phase4_slice1_model_metadata() -> None:
    """ORM metadata matches migration 029 without requiring a live database."""
    assert SourceDiscoveryQueryStatus.SUCCEEDED.value == "succeeded"
    assert {m.value for m in SourceDiscoveryQueryStatus} == {
        "pending",
        "running",
        "succeeded",
        "failed",
    }
    assert CrawlNodeSourceClassification.UNCLASSIFIED.value == "unclassified"
    assert len(CrawlNodeSourceClassification) == 11
    relationship_values = {
        relationship.value for relationship in CrawlEdgeRelationshipType
    }
    phase4_relationships = {
        "directory_to_profile",
        "profile_to_official_site",
        "official_site_to_contact_page",
        "official_site_to_program_page",
        "official_site_to_location_page",
        "official_site_to_licensing_page",
        "official_site_to_evidence_page",
        "related_source",
        "discovered_link",
    }
    assert phase4_relationships <= relationship_values
    phase5_relationships = {
        "directory_to_official_site",
        "pagination",
        "load_more",
        "structured_api",
    }
    assert phase5_relationships <= relationship_values

    query_cols = {c.name for c in ScrapingSourceDiscoveryQuery.__table__.columns}
    for name in (
        "claim_token",
        "claimed_at",
        "lease_expires_at",
        "attempt_count",
        "last_attempt_at",
        "next_attempt_at",
        "last_error_code",
        "last_error_at",
    ):
        assert name in query_cols
    assert ScrapingSourceDiscoveryQuery.__table__.c.attempt_count.nullable is False
    claim_token_col = ScrapingSourceDiscoveryQuery.__table__.c.claim_token
    assert isinstance(claim_token_col.type, sa.String)
    assert claim_token_col.type.length == 36
    assert ScrapingSourceCandidate.__table__.c.region_name.nullable is True
    assert ScrapingSourceCandidate.__table__.c.crawl_node_id.nullable is True

    query_uq = {u.name for u in ScrapingSourceDiscoveryQuery.__table__.constraints if u.name}
    assert "uq_source_discovery_query_id_org_exec" in query_uq
    cand_names = {c.name for c in ScrapingSourceCandidate.__table__.constraints if c.name}
    assert "uq_source_candidate_id_org_exec" in cand_names
    assert "ck_source_candidate_crawl_node_requires_execution" in cand_names
    assert "fk_source_candidates_crawl_node_org_exec" in cand_names
    # No simple unprotected crawl_node_id-only FK.
    simple_crawl_fks = [
        fk
        for fk in ScrapingSourceCandidate.__table__.foreign_key_constraints
        if tuple(fk.column_keys) == ("crawl_node_id",)
    ]
    assert simple_crawl_fks == []

    assert "uq_crawl_node_org_exec_url_hash" in {
        u.name for u in ScrapingCrawlNode.__table__.constraints if u.name
    }
    assert "uq_crawl_edge_org_exec_rel" in {
        u.name for u in ScrapingCrawlEdge.__table__.constraints if u.name
    }
    assert "ck_crawl_edge_no_self_loop" in {
        c.name for c in ScrapingCrawlEdge.__table__.constraints if c.name
    }
    edge_fk_names = {fk.name for fk in ScrapingCrawlEdge.__table__.foreign_key_constraints}
    assert "fk_crawl_edges_discovery_query_org_exec" in edge_fk_names
    assert "fk_crawl_edges_source_candidate_org_exec" in edge_fk_names
    assert "fk_crawl_edges_from_node_org_exec" in edge_fk_names
    edge_fk_cols = {
        fk.name: tuple(fk.column_keys)
        for fk in ScrapingCrawlEdge.__table__.foreign_key_constraints
    }
    assert edge_fk_cols["fk_crawl_edges_discovery_query_org_exec"] == (
        "discovery_query_id",
        "organization_id",
        "execution_id",
    )
    assert edge_fk_cols["fk_crawl_edges_source_candidate_org_exec"] == (
        "source_candidate_id",
        "organization_id",
        "execution_id",
    )
    assert not any(
        cols == ("discovery_query_id",) or cols == ("source_candidate_id",)
        for cols in edge_fk_cols.values()
    )
    cand_fk_cols = {
        fk.name: tuple(fk.column_keys)
        for fk in ScrapingSourceCandidate.__table__.foreign_key_constraints
    }
    assert cand_fk_cols["fk_source_candidates_crawl_node_org_exec"] == (
        "crawl_node_id",
        "organization_id",
        "execution_id",
    )

    index_names = {ix.name for ix in ScrapingSourceDiscoveryQuery.__table__.indexes}
    assert "ix_source_discovery_queries_pending_claim" in index_names
    assert "ix_source_discovery_queries_running_lease" in index_names
    pending = next(
        ix
        for ix in ScrapingSourceDiscoveryQuery.__table__.indexes
        if ix.name == "ix_source_discovery_queries_pending_claim"
    )
    assert [c.name for c in pending.columns] == [
        "organization_id",
        "execution_id",
        "priority",
        "generation_ordinal",
        "next_attempt_at",
    ]


def test_029_upgrade_lifecycle_crawl_graph_and_compatible_downgrade() -> None:
    migration = _migration_module()
    metadata = sa.MetaData()
    _create_post_028_tables(metadata)
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    original_op = migration.op
    try:
        with engine.begin() as connection:
            _insert_pending_job(connection)
            connection.execute(
                text(
                    "INSERT INTO scraping_source_candidates ("
                    "id, organization_id, execution_id, discovery_query_id, provider, "
                    "rank, url, canonical_url, domain, title, snippet, "
                    "country_code, country_name, region_name, language_code, language_name, "
                    "source_category, initial_relevance_score, initial_trust_tier, status, "
                    "discovered_at, metadata_json, created_at, updated_at"
                    ") VALUES ("
                    "'cand-1', 'org-1', 'exec-1', 'q-pending', 'serper', "
                    "1, 'https://example.test/a', 'https://example.test/a', 'example.test', "
                    "'Title', 'Snippet', "
                    "'LB', 'Lebanon', 'Beirut', 'en', 'English', "
                    "'regulatory', 0.5, 'medium', 'discovered', "
                    "'2026-01-01 00:00:00', '{}', "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                    ")"
                )
            )

            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()

            cols = {
                c["name"]: c
                for c in inspect(connection).get_columns("scraping_source_discovery_queries")
            }
            for name in (
                "claim_token",
                "claimed_at",
                "lease_expires_at",
                "attempt_count",
                "last_attempt_at",
                "next_attempt_at",
                "last_error_code",
                "last_error_at",
            ):
                assert name in cols
            assert cols["attempt_count"]["nullable"] is False

            cand_cols = {
                c["name"]: c
                for c in inspect(connection).get_columns("scraping_source_candidates")
            }
            assert cand_cols["region_name"]["nullable"] is True
            assert "crawl_node_id" in cand_cols

            tables = set(inspect(connection).get_table_names())
            assert "scraping_crawl_nodes" in tables
            assert "scraping_crawl_edges" in tables

            row = connection.execute(
                text(
                    "SELECT attempt_count, status, provider, requested_at, "
                    "query_job_fingerprint, claim_token "
                    "FROM scraping_source_discovery_queries WHERE id = 'q-pending'"
                )
            ).one()
            assert row[0] == 0
            assert row[1] == "pending"
            assert row[2] is None
            assert row[3] is None
            assert row[4] == "fp-pending-1"
            assert row[5] is None

            indexes = {
                ix["name"]
                for ix in inspect(connection).get_indexes("scraping_source_discovery_queries")
            }
            assert "ix_source_discovery_queries_pending_claim" in indexes
            assert "ix_source_discovery_queries_running_lease" in indexes

            # Negative attempt_count rejected.
            with pytest.raises(Exception):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE scraping_source_discovery_queries "
                            "SET attempt_count = -1 WHERE id = 'q-pending'"
                        )
                    )

            # Invalid lease ordering rejected.
            with pytest.raises(Exception):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE scraping_source_discovery_queries SET "
                            "claimed_at = '2026-01-02 00:00:00', "
                            "lease_expires_at = '2026-01-01 00:00:00' "
                            "WHERE id = 'q-pending'"
                        )
                    )

            # region_name NULL allowed; historical NULL crawl_node + NULL execution OK.
            connection.execute(
                text(
                    "UPDATE scraping_source_candidates SET region_name = NULL "
                    "WHERE id = 'cand-1'"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO scraping_source_candidates ("
                    "id, organization_id, execution_id, discovery_query_id, "
                    "provider, rank, url, canonical_url, domain, title, snippet, "
                    "country_code, country_name, region_name, language_code, language_name, "
                    "source_category, initial_relevance_score, initial_trust_tier, status, "
                    "discovered_at, metadata_json, created_at, updated_at"
                    ") VALUES ("
                    "'cand-hist', 'org-1', NULL, 'q-pending', "
                    "'serper', 9, 'https://example.test/hist', 'https://example.test/hist', "
                    "'example.test', 'Hist', 'Snippet', "
                    "'LB', 'Lebanon', NULL, 'en', 'English', "
                    "'regulatory', 0.1, 'medium', 'discovered', "
                    "'2026-01-01 00:00:00', '{}', "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                    ")"
                )
            )
            # crawl_node_id set with execution_id NULL rejected.
            with pytest.raises(Exception):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "UPDATE scraping_source_candidates "
                            "SET crawl_node_id = 'node-1' WHERE id = 'cand-hist'"
                        )
                    )

            # Crawl node uniqueness per org+execution; same hash OK across executions.
            url_hash = "a" * 64
            connection.execute(
                text(
                    "INSERT INTO scraping_crawl_nodes ("
                    "id, organization_id, execution_id, canonical_url, canonical_url_hash, "
                    "hostname, domain, source_classification, first_seen_at, "
                    "created_at, updated_at"
                    ") VALUES ("
                    "'node-1', 'org-1', 'exec-1', 'https://example.test/a', :h, "
                    "'example.test', 'example.test', 'unclassified', "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                    ")"
                ),
                {"h": url_hash},
            )
            with pytest.raises(Exception):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO scraping_crawl_nodes ("
                            "id, organization_id, execution_id, canonical_url, "
                            "canonical_url_hash, hostname, domain, source_classification, "
                            "first_seen_at, created_at, updated_at"
                            ") VALUES ("
                            "'node-dup', 'org-1', 'exec-1', 'https://example.test/a', :h, "
                            "'example.test', 'example.test', 'directory', "
                            "'2026-01-01 00:00:00', '2026-01-01 00:00:00', "
                            "'2026-01-01 00:00:00'"
                            ")"
                        ),
                        {"h": url_hash},
                    )
            connection.execute(
                text(
                    "INSERT INTO scraping_crawl_nodes ("
                    "id, organization_id, execution_id, canonical_url, canonical_url_hash, "
                    "hostname, domain, source_classification, first_seen_at, "
                    "created_at, updated_at"
                    ") VALUES ("
                    "'node-2', 'org-1', 'exec-2', 'https://example.test/a', :h, "
                    "'example.test', 'example.test', 'unclassified', "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                    ")"
                ),
                {"h": url_hash},
            )
            connection.execute(
                text(
                    "INSERT INTO scraping_crawl_nodes ("
                    "id, organization_id, execution_id, canonical_url, canonical_url_hash, "
                    "hostname, domain, source_classification, first_seen_at, "
                    "created_at, updated_at"
                    ") VALUES ("
                    "'node-3', 'org-2', 'exec-1', 'https://example.test/a', :h, "
                    "'example.test', 'example.test', 'unclassified', "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                    ")"
                ),
                {"h": url_hash},
            )

            # Multiple candidates → one node (distinct discovery queries; same URL OK).
            connection.execute(
                text(
                    "INSERT INTO scraping_source_discovery_queries ("
                    "id, organization_id, execution_id, country_code, country_name, "
                    "region_name, language_code, language_name, source_category, query_text, "
                    "status, result_count, metadata_json, "
                    "purpose, priority, discovery_round, generation_ordinal, scope_level, "
                    "query_job_fingerprint, plan_hash_snapshot, important_city, "
                    "provider, requested_at, attempt_count, created_at, updated_at"
                    ") VALUES ("
                    "'q-pending-2', 'org-1', 'exec-1', 'LB', 'Lebanon', NULL, 'en', 'English', "
                    "'regulatory', 'pending job 2', "
                    "'pending', 0, '{}', "
                    "'seed_source_discovery', 100, 1, 1, 'countrywide', "
                    "'fp-pending-2', 'planhash', NULL, "
                    "NULL, NULL, 0, "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                    ")"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO scraping_source_candidates ("
                    "id, organization_id, execution_id, discovery_query_id, crawl_node_id, "
                    "provider, rank, url, canonical_url, domain, title, snippet, "
                    "country_code, country_name, region_name, language_code, language_name, "
                    "source_category, initial_relevance_score, initial_trust_tier, status, "
                    "discovered_at, metadata_json, created_at, updated_at"
                    ") VALUES ("
                    "'cand-2', 'org-1', 'exec-1', 'q-pending-2', 'node-1', "
                    "'serper', 1, 'https://example.test/a', 'https://example.test/a', "
                    "'example.test', 'Title 2', 'Snippet 2', "
                    "'LB', 'Lebanon', NULL, 'en', 'English', "
                    "'regulatory', 0.4, 'medium', 'discovered', "
                    "'2026-01-01 00:00:00', '{}', "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                    ")"
                )
            )
            connection.execute(
                text(
                    "UPDATE scraping_source_candidates SET crawl_node_id = 'node-1' "
                    "WHERE id = 'cand-1'"
                )
            )
            linked = connection.execute(
                text(
                    "SELECT COUNT(*) FROM scraping_source_candidates "
                    "WHERE crawl_node_id = 'node-1'"
                )
            ).scalar()
            assert linked == 2

            # Edge uniqueness + self-edge rejection.
            connection.execute(
                text(
                    "INSERT INTO scraping_crawl_nodes ("
                    "id, organization_id, execution_id, canonical_url, canonical_url_hash, "
                    "hostname, domain, source_classification, first_seen_at, "
                    "created_at, updated_at"
                    ") VALUES ("
                    "'node-b', 'org-1', 'exec-1', 'https://example.test/b', :h, "
                    "'example.test', 'example.test', 'directory', "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                    ")"
                ),
                {"h": "b" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO scraping_crawl_edges ("
                    "id, organization_id, execution_id, from_node_id, to_node_id, "
                    "relationship_type, created_at"
                    ") VALUES ("
                    "'edge-1', 'org-1', 'exec-1', 'node-b', 'node-1', "
                    "'directory_to_profile', '2026-01-01 00:00:00'"
                    ")"
                )
            )
            with pytest.raises(Exception):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO scraping_crawl_edges ("
                            "id, organization_id, execution_id, from_node_id, to_node_id, "
                            "relationship_type, created_at"
                            ") VALUES ("
                            "'edge-dup', 'org-1', 'exec-1', 'node-b', 'node-1', "
                            "'directory_to_profile', '2026-01-01 00:00:00'"
                            ")"
                        )
                    )
            with pytest.raises(Exception):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO scraping_crawl_edges ("
                            "id, organization_id, execution_id, from_node_id, to_node_id, "
                            "relationship_type, created_at"
                            ") VALUES ("
                            "'edge-self', 'org-1', 'exec-1', 'node-1', 'node-1', "
                            "'related_source', '2026-01-01 00:00:00'"
                            ")"
                        )
                    )

            # Compatible downgrade: restore region_name first (no NULL regions).
            connection.execute(
                text(
                    "UPDATE scraping_source_candidates SET region_name = 'Beirut', "
                    "crawl_node_id = NULL"
                )
            )
            connection.execute(text("DELETE FROM scraping_crawl_edges"))
            connection.execute(text("DELETE FROM scraping_crawl_nodes"))
            migration.downgrade()
            after_cols = {
                c["name"]
                for c in inspect(connection).get_columns("scraping_source_discovery_queries")
            }
            assert "claim_token" not in after_cols
            assert "attempt_count" not in after_cols
            assert "scraping_crawl_nodes" not in inspect(connection).get_table_names()
            row_after = connection.execute(
                text(
                    "SELECT status, provider, query_job_fingerprint "
                    "FROM scraping_source_discovery_queries WHERE id = 'q-pending'"
                )
            ).one()
            assert row_after[0] == "pending"
            assert row_after[1] is None
            assert row_after[2] == "fp-pending-1"
    finally:
        migration.op = original_op
        engine.dispose()


def test_029_downgrade_fails_closed_on_null_region_name() -> None:
    migration = _migration_module()
    metadata = sa.MetaData()
    _create_post_028_tables(metadata)
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    original_op = migration.op
    try:
        with engine.begin() as connection:
            _insert_pending_job(connection)
            connection.execute(
                text(
                    "INSERT INTO scraping_source_candidates ("
                    "id, organization_id, execution_id, discovery_query_id, provider, "
                    "rank, url, canonical_url, domain, title, snippet, "
                    "country_code, country_name, region_name, language_code, language_name, "
                    "source_category, initial_relevance_score, initial_trust_tier, status, "
                    "discovered_at, metadata_json, created_at, updated_at"
                    ") VALUES ("
                    "'cand-1', 'org-1', 'exec-1', 'q-pending', 'serper', "
                    "1, 'https://example.test/a', 'https://example.test/a', 'example.test', "
                    "'Title', 'Snippet', "
                    "'LB', 'Lebanon', 'Beirut', 'en', 'English', "
                    "'regulatory', 0.5, 'medium', 'discovered', "
                    "'2026-01-01 00:00:00', '{}', "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                    ")"
                )
            )
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            connection.execute(
                text(
                    "UPDATE scraping_source_candidates SET region_name = NULL "
                    "WHERE id = 'cand-1'"
                )
            )
            before_tables = set(inspect(connection).get_table_names())
            with pytest.raises(RuntimeError, match="Cannot downgrade migration 029"):
                migration.downgrade()
            after_tables = set(inspect(connection).get_table_names())
            assert "scraping_crawl_nodes" in after_tables
            assert after_tables == before_tables
    finally:
        migration.op = original_op
        engine.dispose()
