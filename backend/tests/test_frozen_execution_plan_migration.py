"""Round-trip coverage for migration 026 frozen execution-plan columns."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def _migration_module():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "026_frozen_execution_plan.py"
    spec = importlib.util.spec_from_file_location("migration_026_frozen_execution_plan", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_execution_plan_migration_round_trip() -> None:
    migration = _migration_module()
    assert migration.down_revision == "025"
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
        sa.Column("execution_origin", sa.String(32), nullable=False, server_default="legacy_pipeline"),
        sa.Column("blueprint_version_snapshot", sa.Integer(), nullable=True),
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
                    "status, country_code, country_name, execution_origin, blueprint_version_snapshot) "
                    "VALUES ('exec-1', 'org-1', 'mission-1', 'bp-1', 'mission_campaign', 'mock', "
                    "'completed', 'LB', 'Lebanon', 'mission_campaign_mock', 7)"
                )
            )
            inspector = inspect(connection)
            before_columns = {
                column["name"] for column in inspector.get_columns("scraping_executions")
            }

            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            inspector = inspect(connection)
            after_columns = {
                column["name"] for column in inspector.get_columns("scraping_executions")
            }
            added = {
                "blueprint_snapshot_json",
                "frozen_execution_plan_json",
                "execution_plan_schema_version",
                "execution_plan_hash",
                "execution_plan_compiled_at",
            }
            assert added.isdisjoint(before_columns)
            assert added <= after_columns

            row = connection.execute(
                text(
                    "SELECT blueprint_snapshot_json, frozen_execution_plan_json, "
                    "execution_plan_schema_version, execution_plan_hash, execution_plan_compiled_at "
                    "FROM scraping_executions WHERE id = 'exec-1'"
                )
            ).one()
            assert row == (None, None, None, None, None)

            for column in inspector.get_columns("scraping_executions"):
                if column["name"] in added:
                    assert column["nullable"] is True

            migration.downgrade()
            inspector = inspect(connection)
            round_trip_columns = {
                column["name"] for column in inspector.get_columns("scraping_executions")
            }
            assert round_trip_columns == before_columns
            surviving = connection.execute(
                text("SELECT id, status FROM scraping_executions WHERE id = 'exec-1'")
            ).one()
            assert surviving == ("exec-1", "completed")

            migration.upgrade()
            inspector = inspect(connection)
            reupgrade_columns = {
                column["name"] for column in inspector.get_columns("scraping_executions")
            }
            assert added <= reupgrade_columns
    finally:
        migration.op = original_op
        engine.dispose()
