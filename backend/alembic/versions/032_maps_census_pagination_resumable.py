"""Add pagination, capped-cell subdivision, resumable execution, and quota
metrics columns for the Maps census recall upgrade (Phase 2 completion).

Purely additive on top of 031 — no existing column is altered or dropped.

Revision ID: 032
Revises: 031
Create Date: 2026-08-01
"""

from alembic import op
import sqlalchemy as sa

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None

CELL_PARENT_FK = "fk_maps_census_cells_parent_cell_id"


def upgrade() -> None:
    with op.batch_alter_table("maps_census_runs") as batch:
        batch.add_column(sa.Column("processing_state", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("quota_metrics", sa.JSON(), nullable=True))

    with op.batch_alter_table("maps_census_regions") as batch:
        batch.add_column(
            sa.Column("eligible_candidates_found", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("review_candidates_found", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("confirmed_public_found", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("individuals_found", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("unrelated_found", sa.Integer(), nullable=False, server_default="0"))

    with op.batch_alter_table("maps_census_cells") as batch:
        batch.add_column(sa.Column("parent_cell_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("expansion_reason", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("expansion_depth", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("viewport_bounds", sa.JSON(), nullable=True))

        batch.add_column(sa.Column("pagination_resume_token", sa.String(length=2048), nullable=True))
        batch.add_column(sa.Column("pages_fetched", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(
            sa.Column("raw_results_found", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("unique_results_found", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("duplicates_found", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(
            sa.Column("next_page_available", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column("result_cap_reached", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("pagination_error", sa.Text(), nullable=True))

        batch.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_error", sa.Text(), nullable=True))
        batch.add_column(sa.Column("claimed_by", sa.String(length=128), nullable=True))

        batch.create_foreign_key(
            CELL_PARENT_FK,
            "maps_census_cells",
            ["parent_cell_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_maps_census_cells_parent_cell_id", ["parent_cell_id"], unique=False)
        batch.create_index(
            "ix_maps_census_cells_run_status_retry",
            ["run_id", "status", "next_retry_at"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("maps_census_cells") as batch:
        batch.drop_index("ix_maps_census_cells_run_status_retry")
        batch.drop_index("ix_maps_census_cells_parent_cell_id")
        batch.drop_constraint(CELL_PARENT_FK, type_="foreignkey")

        batch.drop_column("claimed_by")
        batch.drop_column("last_error")
        batch.drop_column("next_retry_at")
        batch.drop_column("heartbeat_at")
        batch.drop_column("started_at")
        batch.drop_column("attempt_count")

        batch.drop_column("pagination_error")
        batch.drop_column("result_cap_reached")
        batch.drop_column("next_page_available")
        batch.drop_column("duplicates_found")
        batch.drop_column("unique_results_found")
        batch.drop_column("raw_results_found")
        batch.drop_column("pages_fetched")
        batch.drop_column("pagination_resume_token")

        batch.drop_column("viewport_bounds")
        batch.drop_column("expansion_depth")
        batch.drop_column("expansion_reason")
        batch.drop_column("parent_cell_id")

    with op.batch_alter_table("maps_census_regions") as batch:
        batch.drop_column("unrelated_found")
        batch.drop_column("individuals_found")
        batch.drop_column("confirmed_public_found")
        batch.drop_column("review_candidates_found")
        batch.drop_column("eligible_candidates_found")

    with op.batch_alter_table("maps_census_runs") as batch:
        batch.drop_column("quota_metrics")
        batch.drop_column("processing_state")
