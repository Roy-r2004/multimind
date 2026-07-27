"""Round-trip coverage for migration 027 clarification columns."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def _migration_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "027_execution_plan_clarification.py"
    )
    spec = importlib.util.spec_from_file_location("migration_027_clarification", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clarification_migration_round_trip() -> None:
    migration = _migration_module()
    assert migration.down_revision == "026"
    metadata = sa.MetaData()
    sa.Table(
        "scraping_executions",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("mission_id", sa.String(36), nullable=False),
        sa.Column("blueprint_id", sa.String(36), nullable=False),
        sa.Column("execution_type", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("country_name", sa.String(120), nullable=False),
        sa.Column("execution_plan_hash", sa.String(64), nullable=True),
    )
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    original_op = migration.op
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO scraping_executions "
                    "(id, organization_id, mission_id, blueprint_id, execution_type, mode, "
                    "status, country_code, country_name, execution_plan_hash) "
                    "VALUES ('exec-1', 'org-1', 'mission-1', 'bp-1', 'mission_campaign', 'mock', "
                    "'completed', 'LB', 'Lebanon', 'abc')"
                )
            )
            before = {c["name"] for c in inspect(connection).get_columns("scraping_executions")}
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            after = {c["name"] for c in inspect(connection).get_columns("scraping_executions")}
            added = {
                "clarification_status",
                "clarification_schema_version",
                "clarification_requests_json",
                "clarification_decisions_json",
                "resolved_execution_plan_json",
                "resolved_execution_plan_hash",
                "clarification_model_slug_snapshot",
                "clarification_attempt_count",
                "clarification_started_at",
                "clarification_completed_at",
                "clarification_error_code",
                "clarification_provider_operation_id",
                "clarification_provider_metadata_json",
            }
            assert added.isdisjoint(before)
            assert added <= after
            row = connection.execute(
                text(
                    "SELECT clarification_status, resolved_execution_plan_hash "
                    "FROM scraping_executions WHERE id = 'exec-1'"
                )
            ).one()
            assert row == (None, None)
            migration.downgrade()
            round_trip = {
                c["name"] for c in inspect(connection).get_columns("scraping_executions")
            }
            assert round_trip == before
            surviving = connection.execute(
                text("SELECT id, status FROM scraping_executions WHERE id = 'exec-1'")
            ).one()
            assert surviving == ("exec-1", "completed")
            migration.upgrade()
            assert added <= {
                c["name"] for c in inspect(connection).get_columns("scraping_executions")
            }
    finally:
        migration.op = original_op
        engine.dispose()
