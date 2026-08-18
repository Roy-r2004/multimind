"""Tests for My Playbooks migration 048."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, inspect


def load_migration(revision: str):
    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    path = next(versions_dir.glob(f"{revision}_*.py"))
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_with_ops(module, conn, fn_name: str) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    ctx = MigrationContext.configure(conn)
    ops = Operations(ctx)
    original_op = module.op
    module.op = ops
    try:
        getattr(module, fn_name)()
    finally:
        module.op = original_op


def create_dependencies(conn) -> None:
    conn.exec_driver_sql("CREATE TABLE organizations (id VARCHAR(36) PRIMARY KEY)")
    conn.exec_driver_sql("CREATE TABLE users (id VARCHAR(36) PRIMARY KEY)")
    conn.exec_driver_sql(
        """
        CREATE TABLE chats (
            id VARCHAR(36) PRIMARY KEY,
            org_id VARCHAR(36) NOT NULL REFERENCES organizations(id),
            created_by VARCHAR(36) NOT NULL REFERENCES users(id)
        )
        """
    )
    conn.exec_driver_sql(
        """
        CREATE TABLE turns (
            id VARCHAR(36) PRIMARY KEY,
            chat_id VARCHAR(36) NOT NULL REFERENCES chats(id)
        )
        """
    )
    conn.exec_driver_sql("INSERT INTO organizations (id) VALUES ('org-1')")
    conn.exec_driver_sql("INSERT INTO users (id) VALUES ('user-1')")


def test_migration_048_upgrades_and_downgrades():
    engine = create_engine("sqlite:///:memory:")
    module = load_migration("048")
    with engine.begin() as conn:
        create_dependencies(conn)
        run_with_ops(module, conn, "upgrade")

        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        assert "playbooks" in tables
        assert "playbook_observations" in tables
        assert "playbook_observation_sources" in tables
        assert "playbook_runs" in tables
        assert "playbook_source_states" in tables
        assert "playbook_excluded_sources" in tables

        playbook_cols = {col["name"] for col in inspector.get_columns("playbooks")}
        assert {
            "id",
            "org_id",
            "user_id",
            "status",
            "injection_enabled",
            "core_summary",
            "extraction_version",
            "playbook_version",
            "last_success_run_id",
            "last_success_at",
            "created_at",
            "updated_at",
        } <= playbook_cols

        playbook_uniques = inspector.get_unique_constraints("playbooks")
        assert any(
            uc["name"] == "uq_playbook_org_user" and set(uc["column_names"]) == {"org_id", "user_id"}
            for uc in playbook_uniques
        )

        source_uniques = inspector.get_unique_constraints("playbook_source_states")
        assert any(
            uc["name"] == "uq_playbook_source_state"
            and set(uc["column_names"]) == {"playbook_id", "source_type", "source_id"}
            for uc in source_uniques
        )

        observation_indexes = inspector.get_indexes("playbook_observations")
        assert any(
            idx["name"] == "ix_playbook_observations_playbook_category_status"
            and idx["column_names"] == ["playbook_id", "category", "status"]
            for idx in observation_indexes
        )
        run_indexes = inspector.get_indexes("playbook_runs")
        assert any(
            idx["name"] == "ix_playbook_runs_playbook_created_at"
            and idx["column_names"] == ["playbook_id", "created_at"]
            for idx in run_indexes
        )
        excluded_indexes = inspector.get_indexes("playbook_excluded_sources")
        assert any(idx["name"] == "uq_playbook_excluded_chat" and idx["unique"] for idx in excluded_indexes)
        assert any(idx["name"] == "uq_playbook_excluded_turn" and idx["unique"] for idx in excluded_indexes)

        fks = inspector.get_foreign_keys("playbooks")
        assert any(
            fk["referred_table"] == "playbook_runs"
            and fk["constrained_columns"] == ["last_success_run_id"]
            for fk in fks
        )

        run_with_ops(module, conn, "downgrade")
        remaining = set(inspect(conn).get_table_names())
        assert "playbooks" not in remaining
        assert "playbook_observations" not in remaining
        assert "playbook_observation_sources" not in remaining
        assert "playbook_runs" not in remaining
        assert "playbook_source_states" not in remaining
        assert "playbook_excluded_sources" not in remaining
        # Dependency tables stay; migration must not drop chats/turns/users.
        assert {"organizations", "users", "chats", "turns"} <= remaining
