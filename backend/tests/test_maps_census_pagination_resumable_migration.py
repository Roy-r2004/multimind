"""Migration test for revision 032 — pagination, capped-cell subdivision,
resumable cell execution, and quota-metrics columns (Phase 2 completion).

Builds the schema through revision 031 first (reusing the same dependency/seed
helpers as ``test_maps_census_recall_migration.py``), then verifies 032's
upgrade adds every new column/index/FK additively and downgrade removes them
cleanly, without touching any 031 column.
"""

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


def seed_run_and_cell(conn) -> None:
    conn.exec_driver_sql(
        """
        INSERT INTO maps_census_runs (
            id, organization_id, created_by, country_code, country_name, status
        )
        VALUES ('run-1', 'org-1', 'user-1', 'FR', 'France', 'running')
        """
    )
    conn.exec_driver_sql(
        """
        INSERT INTO maps_census_cells (
            id, run_id, region_name, city_name, query_text, status, places_found
        )
        VALUES ('cell-1', 'run-1', 'Ile-de-France', 'Paris', 'rehab paris', 'pending', 0)
        """
    )


def _build_engine_through_031():
    engine = create_engine("sqlite:///:memory:")
    revisions = ["025", "026", "027", "028", "029", "030"]
    with engine.begin() as conn:
        create_dependencies(conn)
        for revision in revisions:
            run_with_ops(load_migration(revision), conn, "upgrade")
        seed_run_and_cell(conn)
        run_with_ops(load_migration("031"), conn, "upgrade")
    return engine


def test_migration_032_is_additive_on_top_of_031():
    module = load_migration("032")
    assert module.revision == "032"
    assert module.down_revision == "031"

    engine = _build_engine_through_031()
    with engine.begin() as conn:
        before_run_columns = {c["name"] for c in inspect(conn).get_columns("maps_census_runs")}
        before_region_columns = {c["name"] for c in inspect(conn).get_columns("maps_census_regions")}
        before_cell_columns = {c["name"] for c in inspect(conn).get_columns("maps_census_cells")}

        run_with_ops(module, conn, "upgrade")

        inspector = inspect(conn)
        run_columns = {c["name"] for c in inspector.get_columns("maps_census_runs")}
        region_columns = {c["name"] for c in inspector.get_columns("maps_census_regions")}
        cell_columns = {c["name"] for c in inspector.get_columns("maps_census_cells")}

        # Additive: every pre-032 column must still be present.
        assert before_run_columns.issubset(run_columns)
        assert before_region_columns.issubset(region_columns)
        assert before_cell_columns.issubset(cell_columns)

        assert {"processing_state", "quota_metrics"}.issubset(run_columns)
        assert {
            "eligible_candidates_found",
            "review_candidates_found",
            "confirmed_public_found",
            "individuals_found",
            "unrelated_found",
        }.issubset(region_columns)
        assert {
            "parent_cell_id",
            "expansion_reason",
            "expansion_depth",
            "viewport_bounds",
            "pagination_resume_token",
            "pages_fetched",
            "raw_results_found",
            "unique_results_found",
            "duplicates_found",
            "next_page_available",
            "result_cap_reached",
            "pagination_error",
            "attempt_count",
            "started_at",
            "heartbeat_at",
            "next_retry_at",
            "last_error",
            "claimed_by",
        }.issubset(cell_columns)

        index_names = {index["name"] for index in inspector.get_indexes("maps_census_cells")}
        assert "ix_maps_census_cells_parent_cell_id" in index_names
        assert "ix_maps_census_cells_run_status_retry" in index_names

        fk_targets = {
            (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
            for fk in inspector.get_foreign_keys("maps_census_cells")
        }
        assert (("parent_cell_id",), "maps_census_cells", ("id",)) in fk_targets

        # Existing 031 seed row must survive untouched with the new columns
        # defaulting sanely (server_default zero/false, nullable JSON absent).
        row = conn.exec_driver_sql(
            "SELECT attempt_count, result_cap_reached, expansion_depth FROM maps_census_cells WHERE id = 'cell-1'"
        ).one()
        assert row[0] == 0
        assert row[1] in (0, False)
        assert row[2] == 0

        run_with_ops(module, conn, "downgrade")

        downgraded = inspect(conn)
        downgraded_run_columns = {c["name"] for c in downgraded.get_columns("maps_census_runs")}
        downgraded_region_columns = {c["name"] for c in downgraded.get_columns("maps_census_regions")}
        downgraded_cell_columns = {c["name"] for c in downgraded.get_columns("maps_census_cells")}

        assert downgraded_run_columns == before_run_columns
        assert downgraded_region_columns == before_region_columns
        assert downgraded_cell_columns == before_cell_columns
