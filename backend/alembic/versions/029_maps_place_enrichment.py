"""Add website enrichment fields for Maps Census Phase 2 export columns.

Revision ID: 029
Revises: 028
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_census_runs",
        sa.Column("places_enriched", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "maps_census_runs",
        sa.Column("enrichment_refresh_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "maps_census_runs",
        sa.Column("enrichment_refresh_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "maps_places",
        sa.Column("enrichment_status", sa.String(length=20), server_default="pending", nullable=False),
    )
    op.add_column("maps_places", sa.Column("enrichment_error_message", sa.Text(), nullable=True))
    op.add_column(
        "maps_places", sa.Column("enrichment_completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "maps_places",
        sa.Column("enrichment_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("maps_places", sa.Column("addictions_treated", sa.JSON(), nullable=True))
    op.add_column("maps_places", sa.Column("languages_spoken", sa.JSON(), nullable=True))
    op.add_column("maps_places", sa.Column("treatment_price", sa.String(length=512), nullable=True))
    op.add_column("maps_places", sa.Column("enrichment_pages_crawled", sa.JSON(), nullable=True))
    op.create_index("ix_maps_places_enrichment_status", "maps_places", ["enrichment_status"])


def downgrade() -> None:
    op.drop_index("ix_maps_places_enrichment_status", table_name="maps_places")
    op.drop_column("maps_places", "enrichment_pages_crawled")
    op.drop_column("maps_places", "treatment_price")
    op.drop_column("maps_places", "languages_spoken")
    op.drop_column("maps_places", "addictions_treated")
    op.drop_column("maps_places", "enrichment_attempts")
    op.drop_column("maps_places", "enrichment_completed_at")
    op.drop_column("maps_places", "enrichment_error_message")
    op.drop_column("maps_places", "enrichment_status")
    op.drop_column("maps_census_runs", "enrichment_refresh_completed_at")
    op.drop_column("maps_census_runs", "enrichment_refresh_attempts")
    op.drop_column("maps_census_runs", "places_enriched")
