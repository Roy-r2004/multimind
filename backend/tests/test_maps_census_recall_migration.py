import importlib.util
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.db.models import (
    MapsCensusRegion,
    MapsCensusRun,
    MapsClientEligibility,
    MapsContactStatus,
    MapsLifecycleStatus,
    MapsPlace,
)


def load_migration(revision: str):
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / f"{revision}_maps_census_recall_upgrade.py"
    )
    if not path.exists():
        path = next(
            candidate
            for candidate in (Path(__file__).resolve().parents[1] / "alembic" / "versions").glob(
                f"{revision}_*.py"
            )
        )
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


def seed_pre_031_maps_rows(conn) -> None:
    conn.exec_driver_sql(
        """
        INSERT INTO maps_census_runs (
            id, organization_id, created_by, country_code, country_name, status
        )
        VALUES ('run-1', 'org-1', 'user-1', 'DZ', 'Algeria', 'completed')
        """
    )
    conn.exec_driver_sql(
        """
        INSERT INTO maps_census_cells (
            id, run_id, region_name, city_name, query_text, status, places_found
        )
        VALUES ('cell-1', 'run-1', 'Algiers', 'Algiers', 'rehab algiers', 'completed', 4)
        """
    )
    conn.exec_driver_sql(
        """
        INSERT INTO maps_places (
            id, run_id, google_place_id, raw_name, canonical_name, is_relevant,
            verification_verdict, international_phone_number, official_website
        )
        VALUES
            (
                'place-confirmed', 'run-1', 'g-confirmed', 'Confirmed Rehab', 'Confirmed Rehab',
                1, 'confirmed', '+213 555 00 00', 'https://confirmed.example/'
            ),
            (
                'place-review', 'run-1', 'g-review', 'Review Rehab', 'Review Rehab',
                1, 'unknown', NULL, 'https://review.example/'
            ),
            (
                'place-contradicted', 'run-1', 'g-contradicted', 'Contradicted Rehab', 'Contradicted Rehab',
                0, 'contradicted', '+213 555 11 11', NULL
            ),
            (
                'place-discovered', 'run-1', 'g-discovered', 'Unclassified Rehab', 'Unclassified Rehab',
                NULL, NULL, NULL, NULL
            )
        """
    )


def test_migration_031_upgrades_maps_schema_and_backfills_legacy_places():
    assert MapsCensusRegion.__tablename__ == "maps_census_regions"
    assert MapsCensusRun.country_profile_status.property.columns[0].type.length == 20
    assert MapsPlace.lifecycle_status.property.columns[0].type.length == 40
    assert MapsLifecycleStatus.NEEDS_REVIEW.value == "needs_review"
    assert MapsClientEligibility.REVIEW.value == "review"
    assert MapsContactStatus.MISSING.value == "missing"

    engine = create_engine("sqlite:///:memory:")
    revisions = ["025", "026", "027", "028", "029", "030"]
    with engine.begin() as conn:
        create_dependencies(conn)
        for revision in revisions:
            run_with_ops(load_migration(revision), conn, "upgrade")
        seed_pre_031_maps_rows(conn)

        module = load_migration("031")
        assert module.revision == "031"
        assert module.down_revision == "030"

        run_with_ops(module, conn, "upgrade")

        inspector = inspect(conn)
        assert "maps_census_regions" in inspector.get_table_names()

        run_columns = {
            column["name"]: column for column in inspector.get_columns("maps_census_runs")
        }
        assert {"country_profile", "country_profile_status", "country_profile_error"}.issubset(
            run_columns
        )
        assert {"funnel_metrics", "saturation_summary"}.issubset(run_columns)

        cell_columns = {
            column["name"]: column for column in inspector.get_columns("maps_census_cells")
        }
        assert {"region_id", "query_family", "query_language"}.issubset(cell_columns)
        assert {"new_unique_places", "new_plausible_places"}.issubset(cell_columns)
        assert "ix_maps_census_cells_region_id" in {
            index["name"] for index in inspector.get_indexes("maps_census_cells")
        }
        assert {
            (
                tuple(fk["constrained_columns"]),
                fk["referred_table"],
                tuple(fk["referred_columns"]),
                fk["options"].get("ondelete"),
            )
            for fk in inspector.get_foreign_keys("maps_census_cells")
        } >= {
            (("region_id",), "maps_census_regions", ("id",), "SET NULL"),
            (("run_id",), "maps_census_runs", ("id",), "CASCADE"),
        }

        place_columns = {
            column["name"]: column for column in inspector.get_columns("maps_places")
        }
        assert {
            "lifecycle_status",
            "client_eligibility",
            "operator_type",
            "contact_status",
            "discovery_sources",
            "classification_confidence",
        }.issubset(place_columns)
        assert {
            index["name"] for index in inspector.get_indexes("maps_places")
        } >= {
            "ix_maps_places_lifecycle_status",
            "ix_maps_places_client_eligibility",
            "ix_maps_places_run_lifecycle_status",
            "ix_maps_places_run_client_eligibility",
        }

        place_rows = conn.exec_driver_sql(
            """
            SELECT id, lifecycle_status, client_eligibility, contact_status, discovery_sources
            FROM maps_places
            ORDER BY id
            """
        ).all()
        by_id = {
            row[0]: {
                "lifecycle_status": row[1],
                "client_eligibility": row[2],
                "contact_status": row[3],
                "discovery_sources": row[4],
            }
            for row in place_rows
        }
        assert by_id["place-confirmed"] == {
            "lifecycle_status": "confirmed_eligible",
            "client_eligibility": "eligible",
            "contact_status": "complete",
            "discovery_sources": '["google_places"]',
        }
        assert by_id["place-review"] == {
            "lifecycle_status": "needs_review",
            "client_eligibility": "review",
            "contact_status": "website_only",
            "discovery_sources": '["google_places"]',
        }
        assert by_id["place-contradicted"] == {
            "lifecycle_status": "contradicted",
            "client_eligibility": "excluded",
            "contact_status": "phone_only",
            "discovery_sources": '["google_places"]',
        }
        assert by_id["place-discovered"] == {
            "lifecycle_status": "discovered",
            "client_eligibility": "excluded",
            "contact_status": "missing",
            "discovery_sources": '["google_places"]',
        }

        run_with_ops(module, conn, "downgrade")

        downgraded = inspect(conn)
        assert "maps_census_regions" not in downgraded.get_table_names()
        assert "country_profile" not in {
            column["name"] for column in downgraded.get_columns("maps_census_runs")
        }
        assert "region_id" not in {
            column["name"] for column in downgraded.get_columns("maps_census_cells")
        }
        assert "lifecycle_status" not in {
            column["name"] for column in downgraded.get_columns("maps_places")
        }
