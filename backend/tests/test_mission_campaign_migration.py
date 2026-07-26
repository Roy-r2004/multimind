"""Round-trip coverage for the Phase 2A campaign lifecycle migration."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect


def _migration_module():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "023_mission_campaign_lifecycle.py"
    spec = importlib.util.spec_from_file_location("migration_023_mission_campaign_lifecycle", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mission_campaign_migration_round_trip() -> None:
    migration = _migration_module()
    metadata = sa.MetaData()
    sa.Table("users", metadata, sa.Column("id", sa.String(36), primary_key=True))
    sa.Table(
        "scraping_executions",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("mission_id", sa.String(36), nullable=False),
        sa.Column("blueprint_id", sa.String(36), nullable=False),
        sa.Column("team_plan_id", sa.String(36), nullable=False),
        sa.Column("execution_type", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("country_name", sa.String(120), nullable=False),
        sa.Column("last_event_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Index(
            "uq_scraping_executions_active_team_plan",
            "team_plan_id",
            unique=True,
            sqlite_where=sa.text(
                "status in ('queued', 'running', 'cancel_requested')"
            ),
        ),
    )
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    original_op = migration.op

    try:
        with engine.begin() as connection:
            inspector = inspect(connection)
            before_columns = {
                column["name"] for column in inspector.get_columns("scraping_executions")
            }
            before_indexes = {
                index["name"] for index in inspector.get_indexes("scraping_executions")
            }

            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            inspector = inspect(connection)
            after_columns = {
                column["name"] for column in inspector.get_columns("scraping_executions")
            }
            after_indexes = {
                index["name"] for index in inspector.get_indexes("scraping_executions")
            }
            team_plan = next(
                column
                for column in inspector.get_columns("scraping_executions")
                if column["name"] == "team_plan_id"
            )

            added_columns = {
                "pause_requested_at",
                "paused_at",
                "resumed_at",
                "execution_origin",
                "blueprint_version_snapshot",
                "created_by",
                "current_stage",
                "current_stage_label",
                "current_provider",
                "current_model",
                "latest_message",
                "current_region",
                "current_website",
                "current_page",
                "budget_status",
                "campaign_budget",
                "progress_percent",
                "regions_total",
                "regions_completed",
                "candidates_discovered",
                "websites_queued",
                "pages_visited",
                "pdfs_processed",
                "verified_facilities",
                "manual_review_count",
                "excluded_count",
                "duplicates_merged",
                "phones_found",
                "emails_found",
                "country_mismatches",
                "provider_request_count",
                "input_tokens",
                "output_tokens",
                "estimated_cost",
                "budget_used",
            }
            assert added_columns.isdisjoint(before_columns)
            assert added_columns <= after_columns
            assert team_plan["nullable"] is True
            assert "uq_scraping_executions_active_mission_campaign" in after_indexes

            migration.downgrade()
            inspector = inspect(connection)
            round_trip_columns = {
                column["name"] for column in inspector.get_columns("scraping_executions")
            }
            round_trip_indexes = {
                index["name"] for index in inspector.get_indexes("scraping_executions")
            }
            team_plan = next(
                column
                for column in inspector.get_columns("scraping_executions")
                if column["name"] == "team_plan_id"
            )

            assert round_trip_columns == before_columns
            assert round_trip_indexes == before_indexes
            assert team_plan["nullable"] is False
    finally:
        migration.op = original_op
        engine.dispose()
