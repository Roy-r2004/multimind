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


def test_migration_gosom065_keys_cache_by_domain_and_seed_path():
    module = load_migration("gosom065")
    assert module.revision == "gosom065_crawl_cache_seed_path"
    assert module.down_revision == "gosom064_inpatient_treatment"

    engine = _build_engine_through_032()
    with engine.begin() as conn:
        run_with_ops(load_migration("033"), conn, "upgrade")
        conn.exec_driver_sql(
            "INSERT INTO maps_website_crawl_cache "
            "(id, normalized_domain, pages, fetched_at, expires_at, created_at, updated_at) "
            "VALUES ('row-1', 'example.test', '[]', '2026-01-01', '2026-01-02', '2026-01-01', '2026-01-01')"
        )

        run_with_ops(module, conn, "upgrade")
        inspector = inspect(conn)
        columns = {c["name"] for c in inspector.get_columns("maps_website_crawl_cache")}
        assert "seed_path" in columns

        # Pre-existing rows are backfilled with a safe default rather than
        # left NULL or dropped.
        seed_path = conn.exec_driver_sql(
            "SELECT seed_path FROM maps_website_crawl_cache WHERE id = 'row-1'"
        ).scalar_one()
        assert seed_path == "/"

        unique_constraints = {
            constraint["name"]: set(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("maps_website_crawl_cache")
        }
        assert unique_constraints.get("uq_maps_website_crawl_cache_domain_path") == {
            "normalized_domain",
            "seed_path",
        }
        assert "uq_maps_website_crawl_cache_domain" not in unique_constraints

        # Two branches on the same domain, different seed paths, no longer collide.
        conn.exec_driver_sql(
            "INSERT INTO maps_website_crawl_cache "
            "(id, normalized_domain, seed_path, pages, fetched_at, expires_at, created_at, updated_at) "
            "VALUES ('row-2', 'example.test', '/branch-a', '[]', '2026-01-01', '2026-01-02', '2026-01-01', '2026-01-01')"
        )
        conn.exec_driver_sql(
            "INSERT INTO maps_website_crawl_cache "
            "(id, normalized_domain, seed_path, pages, fetched_at, expires_at, created_at, updated_at) "
            "VALUES ('row-3', 'example.test', '/branch-b', '[]', '2026-01-01', '2026-01-02', '2026-01-01', '2026-01-01')"
        )
        count = conn.exec_driver_sql(
            "SELECT count(*) FROM maps_website_crawl_cache WHERE normalized_domain = 'example.test'"
        ).scalar_one()
        assert count == 3

        # Downgrade recreates a single-domain unique constraint, so drop the
        # now-ambiguous extra branch rows first — this test only needs to
        # prove the column itself is removed on downgrade.
        conn.exec_driver_sql("DELETE FROM maps_website_crawl_cache WHERE id IN ('row-2', 'row-3')")
        run_with_ops(module, conn, "downgrade")
        inspector = inspect(conn)
        columns = {c["name"] for c in inspector.get_columns("maps_website_crawl_cache")}
        assert "seed_path" not in columns
