"""Idempotent repair for maps_places.manually_excluded_at (revision 054)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, inspect, text


def load_migration():
    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    path = next(versions_dir.glob("054_*.py"))
    spec = importlib.util.spec_from_file_location("migration_054", path)
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


def test_054_adds_manually_excluded_at_when_missing() -> None:
    module = load_migration()
    assert module.revision == "054"
    assert module.down_revision == "053"

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE maps_places (id VARCHAR(36) PRIMARY KEY)"))
        assert "manually_excluded_at" not in {
            col["name"] for col in inspect(conn).get_columns("maps_places")
        }
        run_with_ops(module, conn, "upgrade")
        columns = {col["name"] for col in inspect(conn).get_columns("maps_places")}
        assert "manually_excluded_at" in columns
        run_with_ops(module, conn, "upgrade")
        assert "manually_excluded_at" in {
            col["name"] for col in inspect(conn).get_columns("maps_places")
        }


def test_054_is_noop_when_column_already_exists() -> None:
    module = load_migration()
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE maps_places ("
                "id VARCHAR(36) PRIMARY KEY, "
                "manually_excluded_at DATETIME"
                ")"
            )
        )
        run_with_ops(module, conn, "upgrade")
        columns = {col["name"] for col in inspect(conn).get_columns("maps_places")}
        assert "manually_excluded_at" in columns
