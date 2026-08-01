"""Tests for Maps website crawl cache migration (Phase 3)."""

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
    conn.exec_driver_sql("INSERT INTO organizations (id) VALUES ('org-1')")
    conn.exec_driver_sql("INSERT INTO users (id) VALUES ('user-1')")


def _build_engine_through_032():
    engine = create_engine("sqlite:///:memory:")
    revisions = ["025", "026", "027", "028", "029", "030"]
    with engine.begin() as conn:
        create_dependencies(conn)
        for revision in revisions:
            run_with_ops(load_migration(revision), conn, "upgrade")
        run_with_ops(load_migration("031"), conn, "upgrade")
        run_with_ops(load_migration("032"), conn, "upgrade")
    return engine


def test_migration_033_adds_website_crawl_cache_table():
    module = load_migration("033")
    assert module.revision == "033"
    assert module.down_revision == "032"

    engine = _build_engine_through_032()
    with engine.begin() as conn:
        before_tables = set(inspect(conn).get_table_names())
        run_with_ops(module, conn, "upgrade")
        inspector = inspect(conn)
        after_tables = set(inspector.get_table_names())
        assert "maps_website_crawl_cache" in after_tables
        assert before_tables.issubset(after_tables)

        columns = {c["name"] for c in inspector.get_columns("maps_website_crawl_cache")}
        assert {
            "id",
            "normalized_domain",
            "pages",
            "fetched_at",
            "expires_at",
            "created_at",
            "updated_at",
        }.issubset(columns)

        indexes = {index["name"] for index in inspector.get_indexes("maps_website_crawl_cache")}
        assert "ix_maps_website_crawl_cache_expires_at" in indexes
        assert "uq_maps_website_crawl_cache_domain" in {
            constraint["name"] for constraint in inspector.get_unique_constraints("maps_website_crawl_cache")
        }

        run_with_ops(module, conn, "downgrade")
        assert "maps_website_crawl_cache" not in set(inspect(conn).get_table_names())
