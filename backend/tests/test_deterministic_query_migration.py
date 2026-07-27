"""SQLite round-trip coverage for migration 028 Step 3 deterministic query columns."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def _migration_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "028_deterministic_query_jobs.py"
    )
    spec = importlib.util.spec_from_file_location("migration_028_query_jobs", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_pre_028_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
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
        sa.Column("region_name", sa.String(160), nullable=False),
        sa.Column("language_code", sa.String(16), nullable=False),
        sa.Column("language_name", sa.String(120), nullable=False),
        sa.Column("source_category", sa.String(120), nullable=False),
        sa.Column("query_text", sa.String(512), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.String(500), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def _insert_legacy(
    connection,
    *,
    row_id: str,
    query_text: str,
    metadata_json: str,
    region_name: str = "Beirut",
) -> None:
    connection.execute(
        text(
            "INSERT INTO scraping_source_discovery_queries ("
            "id, organization_id, execution_id, country_code, country_name, "
            "region_name, language_code, language_name, source_category, query_text, "
            "provider, status, requested_at, result_count, metadata_json, "
            "created_at, updated_at"
            ") VALUES ("
            ":id, 'org-1', 'exec-1', 'LB', 'Lebanon', :region, 'en', 'English', "
            "'regulatory', :query_text, 'serper', 'succeeded', "
            "'2026-01-01 00:00:00', 1, :metadata_json, "
            "'2026-01-01 00:00:00', '2026-01-01 00:00:00'"
            ")"
        ),
        {
            "id": row_id,
            "region": region_name,
            "query_text": query_text,
            "metadata_json": metadata_json,
        },
    )


def test_028_migration_upgrade_text_unique_and_compatible_downgrade() -> None:
    migration = _migration_module()
    assert migration.revision == "028"
    assert migration.down_revision == "027"

    metadata = sa.MetaData()
    _create_pre_028_table(metadata)
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    original_op = migration.op
    try:
        with engine.begin() as connection:
            _insert_legacy(
                connection,
                row_id="q1",
                query_text="legacy query one",
                metadata_json='{"purpose": "from-metadata"}',
            )
            _insert_legacy(
                connection,
                row_id="q2",
                query_text="legacy query two",
                metadata_json="{}",
            )
            before = {
                c["name"]
                for c in inspect(connection).get_columns("scraping_source_discovery_queries")
            }
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()

            after_cols = {
                c["name"]: c
                for c in inspect(connection).get_columns("scraping_source_discovery_queries")
            }
            added = {
                "purpose",
                "priority",
                "discovery_round",
                "generation_ordinal",
                "query_job_fingerprint",
                "plan_hash_snapshot",
                "scope_level",
                "important_city",
            }
            assert added.isdisjoint(before)
            assert added <= set(after_cols)
            assert after_cols["provider"]["nullable"] is True
            assert after_cols["requested_at"]["nullable"] is True
            assert after_cols["region_name"]["nullable"] is True

            # query_text is unbounded Text (not String(512) / String(2048)).
            query_text_type = after_cols["query_text"]["type"]
            assert getattr(query_text_type, "length", None) is None

            index_names = {
                ix["name"]
                for ix in inspect(connection).get_indexes("scraping_source_discovery_queries")
            }
            assert "ix_source_discovery_queries_fingerprint" not in index_names
            assert "ix_source_discovery_queries_round" in index_names

            unique_names = {
                uq["name"]
                for uq in inspect(connection).get_unique_constraints(
                    "scraping_source_discovery_queries"
                )
            }
            # SQLite may surface the unique constraint as an index instead.
            assert (
                "uq_source_discovery_query_fingerprint" in unique_names
                or "uq_source_discovery_query_fingerprint" in index_names
            )

            rows = connection.execute(
                text(
                    "SELECT id, purpose, priority, discovery_round, generation_ordinal, "
                    "scope_level, plan_hash_snapshot, query_job_fingerprint, provider, "
                    "requested_at "
                    "FROM scraping_source_discovery_queries ORDER BY id"
                )
            ).fetchall()
            assert rows[0][0] == "q1"
            assert rows[0][1] == "from-metadata"
            assert rows[0][2] == 500
            assert rows[0][3] == 1
            assert rows[0][4] == 0
            assert rows[0][5] == "region"
            assert rows[0][6] is None
            assert rows[0][7] is None
            assert rows[0][8] == "serper"
            assert rows[0][9] is not None
            assert rows[1][1] == "legacy_source_discovery"

            # Multiple historical NULL fingerprints remain permitted.
            connection.execute(
                text(
                    "INSERT INTO scraping_source_discovery_queries ("
                    "id, organization_id, execution_id, country_code, country_name, "
                    "region_name, language_code, language_name, source_category, query_text, "
                    "provider, status, requested_at, result_count, metadata_json, "
                    "purpose, priority, discovery_round, generation_ordinal, scope_level, "
                    "query_job_fingerprint, plan_hash_snapshot, important_city, "
                    "created_at, updated_at"
                    ") VALUES ("
                    "'q3', 'org-1', 'exec-1', 'LB', 'Lebanon', 'Beirut', 'en', 'English', "
                    "'regulatory', 'legacy query three', 'serper', 'succeeded', "
                    "'2026-01-01 00:00:00', 0, '{}', "
                    "'legacy_source_discovery', 500, 1, 0, 'region', "
                    "NULL, NULL, NULL, "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                    ")"
                )
            )

            # Provenance: non-null fingerprint requires plan hash + execution_id.
            with pytest.raises(Exception):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO scraping_source_discovery_queries ("
                            "id, organization_id, execution_id, country_code, country_name, "
                            "region_name, language_code, language_name, source_category, "
                            "query_text, provider, status, requested_at, result_count, "
                            "metadata_json, purpose, priority, discovery_round, "
                            "generation_ordinal, scope_level, query_job_fingerprint, "
                            "plan_hash_snapshot, important_city, created_at, updated_at"
                            ") VALUES ("
                            "'bad-fp-no-exec', 'org-1', NULL, 'LB', 'Lebanon', 'Beirut', "
                            "'en', 'English', 'regulatory', 'bad', 'serper', 'succeeded', "
                            "'2026-01-01 00:00:00', 0, '{}', "
                            "'legacy_source_discovery', 500, 1, 0, 'region', "
                            "'fp-missing-exec', 'planhash', NULL, "
                            "'2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                            ")"
                        )
                    )
            with pytest.raises(Exception):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO scraping_source_discovery_queries ("
                            "id, organization_id, execution_id, country_code, country_name, "
                            "region_name, language_code, language_name, source_category, "
                            "query_text, provider, status, requested_at, result_count, "
                            "metadata_json, purpose, priority, discovery_round, "
                            "generation_ordinal, scope_level, query_job_fingerprint, "
                            "plan_hash_snapshot, important_city, created_at, updated_at"
                            ") VALUES ("
                            "'bad-fp-no-hash', 'org-1', 'exec-1', 'LB', 'Lebanon', 'Beirut', "
                            "'en', 'English', 'regulatory', 'bad', 'serper', 'succeeded', "
                            "'2026-01-01 00:00:00', 0, '{}', "
                            "'legacy_source_discovery', 500, 1, 0, 'region', "
                            "'fp-missing-hash', NULL, NULL, "
                            "'2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                            ")"
                        )
                    )

            # Valid plan-backed row succeeds; duplicate fingerprint for same org/exec rejected.
            connection.execute(
                text(
                    "INSERT INTO scraping_source_discovery_queries ("
                    "id, organization_id, execution_id, country_code, country_name, "
                    "region_name, language_code, language_name, source_category, query_text, "
                    "provider, status, requested_at, result_count, metadata_json, "
                    "purpose, priority, discovery_round, generation_ordinal, scope_level, "
                    "query_job_fingerprint, plan_hash_snapshot, important_city, "
                    "created_at, updated_at"
                    ") VALUES ("
                    "'q4', 'org-1', 'exec-1', 'LB', 'Lebanon', 'Beirut', 'en', 'English', "
                    "'regulatory', 'fp row', 'serper', 'succeeded', "
                    "'2026-01-01 00:00:00', 0, '{}', "
                    "'legacy_source_discovery', 500, 1, 0, 'region', "
                    "'fp-region-1', 'planhash', NULL, "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                    ")"
                )
            )
            with pytest.raises(Exception):
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO scraping_source_discovery_queries ("
                            "id, organization_id, execution_id, country_code, country_name, "
                            "region_name, language_code, language_name, source_category, "
                            "query_text, provider, status, requested_at, result_count, "
                            "metadata_json, purpose, priority, discovery_round, "
                            "generation_ordinal, scope_level, query_job_fingerprint, "
                            "plan_hash_snapshot, important_city, created_at, updated_at"
                            ") VALUES ("
                            "'q5', 'org-1', 'exec-1', 'LB', 'Lebanon', 'Beirut', 'en', "
                            "'English', 'regulatory', 'dup fingerprint', 'serper', "
                            "'succeeded', '2026-01-01 00:00:00', 0, '{}', "
                            "'legacy_source_discovery', 500, 1, 0, 'region', "
                            "'fp-region-1', 'planhash', NULL, "
                            "'2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                            ")"
                        )
                    )

            # Compatible historical rows (non-null provider/requested_at/region, NULL fp,
            # query_text <= 512) can downgrade — remove fingerprinted q4 first.
            connection.execute(
                text("DELETE FROM scraping_source_discovery_queries WHERE id = 'q4'")
            )
            migration.downgrade()
            round_trip = {
                c["name"]
                for c in inspect(connection).get_columns("scraping_source_discovery_queries")
            }
            assert added.isdisjoint(round_trip)
            surviving = connection.execute(
                text(
                    "SELECT id, provider, region_name FROM scraping_source_discovery_queries "
                    "WHERE id IN ('q1', 'q2') ORDER BY id"
                )
            ).fetchall()
            assert surviving[0][1] == "serper"
            assert surviving[0][2] == "Beirut"
    finally:
        migration.op = original_op
        engine.dispose()


def test_028_downgrade_fails_closed_without_mutating_data() -> None:
    migration = _migration_module()
    metadata = sa.MetaData()
    _create_pre_028_table(metadata)
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    original_op = migration.op
    try:
        with engine.begin() as connection:
            _insert_legacy(
                connection,
                row_id="q1",
                query_text="legacy query one",
                metadata_json="{}",
            )
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()

            # Step 3B pending plan-backed row (fingerprint set, nullable compat fields).
            connection.execute(
                text(
                    "INSERT INTO scraping_source_discovery_queries ("
                    "id, organization_id, execution_id, country_code, country_name, "
                    "region_name, language_code, language_name, source_category, query_text, "
                    "status, result_count, metadata_json, "
                    "purpose, priority, discovery_round, generation_ordinal, scope_level, "
                    "query_job_fingerprint, plan_hash_snapshot, important_city, "
                    "created_at, updated_at"
                    ") VALUES ("
                    "'pending-3b', 'org-1', 'exec-1', 'LB', 'Lebanon', NULL, 'en', 'English', "
                    "'regulatory', 'pending job', "
                    "'pending', 0, '{\"generation_source\": \"seed\"}', "
                    "'seed_source_discovery', 100, 1, 0, 'countrywide', "
                    "'fp-pending-1', 'planhash', NULL, "
                    "'2026-01-01 00:00:00', '2026-01-01 00:00:00'"
                    ")"
                )
            )
            before_cols = {
                c["name"]
                for c in inspect(connection).get_columns("scraping_source_discovery_queries")
            }
            before_count = connection.execute(
                text("SELECT COUNT(*) FROM scraping_source_discovery_queries")
            ).scalar()
            before_fp = connection.execute(
                text(
                    "SELECT query_job_fingerprint FROM scraping_source_discovery_queries "
                    "WHERE id = 'pending-3b'"
                )
            ).scalar()

            with pytest.raises(RuntimeError, match="Cannot downgrade migration 028"):
                migration.downgrade()

            after_cols = {
                c["name"]
                for c in inspect(connection).get_columns("scraping_source_discovery_queries")
            }
            after_count = connection.execute(
                text("SELECT COUNT(*) FROM scraping_source_discovery_queries")
            ).scalar()
            after_fp = connection.execute(
                text(
                    "SELECT query_job_fingerprint FROM scraping_source_discovery_queries "
                    "WHERE id = 'pending-3b'"
                )
            ).scalar()
            assert after_cols == before_cols
            assert after_count == before_count
            assert after_fp == before_fp == "fp-pending-1"
    finally:
        migration.op = original_op
        engine.dispose()


@pytest.mark.parametrize(
    "setup_sql,match",
    [
        (
            "UPDATE scraping_source_discovery_queries SET region_name = NULL "
            "WHERE id = 'q1'",
            "Cannot downgrade migration 028",
        ),
        (
            "UPDATE scraping_source_discovery_queries SET provider = NULL WHERE id = 'q1'",
            "Cannot downgrade migration 028",
        ),
        (
            "UPDATE scraping_source_discovery_queries SET requested_at = NULL WHERE id = 'q1'",
            "Cannot downgrade migration 028",
        ),
        (
            "UPDATE scraping_source_discovery_queries SET query_text = :long_text WHERE id = 'q1'",
            "Cannot downgrade migration 028",
        ),
    ],
)
def test_028_downgrade_rejects_incompatible_legacy_shapes(setup_sql: str, match: str) -> None:
    migration = _migration_module()
    metadata = sa.MetaData()
    _create_pre_028_table(metadata)
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    original_op = migration.op
    long_text = "x" * 513
    try:
        with engine.begin() as connection:
            _insert_legacy(
                connection,
                row_id="q1",
                query_text="short",
                metadata_json="{}",
            )
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            # Keep scope valid when nulling region: only region→countrywide for NULL region case.
            if "region_name = NULL" in setup_sql:
                connection.execute(
                    text(
                        "UPDATE scraping_source_discovery_queries "
                        "SET region_name = NULL, scope_level = 'countrywide', "
                        "important_city = NULL WHERE id = 'q1'"
                    )
                )
            elif ":long_text" in setup_sql:
                connection.execute(text(setup_sql), {"long_text": long_text})
            else:
                connection.execute(text(setup_sql))

            before_cols = {
                c["name"]
                for c in inspect(connection).get_columns("scraping_source_discovery_queries")
            }
            with pytest.raises(RuntimeError, match=match):
                migration.downgrade()
            after_cols = {
                c["name"]
                for c in inspect(connection).get_columns("scraping_source_discovery_queries")
            }
            assert after_cols == before_cols
    finally:
        migration.op = original_op
        engine.dispose()


def test_028_query_text_is_text_not_bounded_string() -> None:
    """Document why query_text is Text: v2 compositions and long seeds exceed 512."""
    term = "x" * 120
    city = "y" * 160
    country = "z" * 120
    composed = " ".join([term, term, term, term, city, country])
    assert len(composed) > 512
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "028_deterministic_query_jobs.py"
    )
    source = path.read_text(encoding="utf-8")
    assert "type_=sa.Text()" in source
    assert "String(2048)" not in source
