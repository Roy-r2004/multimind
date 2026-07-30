"""Add standalone Google Places Maps census tables.

Revision ID: 025
Revises: 024
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maps_census_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("created_by", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("country_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cells_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cells_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("places_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("places_classified_relevant", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("places_with_website", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_maps_census_runs_org_id", "maps_census_runs", ["organization_id"])
    op.create_index("ix_maps_census_runs_status", "maps_census_runs", ["status"])
    op.create_index("ix_maps_census_runs_created_at", "maps_census_runs", ["created_at"])

    op.create_table(
        "maps_census_cells",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("maps_census_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("region_name", sa.String(length=160), nullable=False),
        sa.Column("city_name", sa.String(length=160), nullable=True),
        sa.Column("query_text", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("places_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_maps_census_cells_run_id", "maps_census_cells", ["run_id"])
    op.create_index("ix_maps_census_cells_status", "maps_census_cells", ["status"])

    op.create_table(
        "maps_places",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("maps_census_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("google_place_id", sa.String(length=255), nullable=False),
        sa.Column("raw_name", sa.String(length=512), nullable=False),
        sa.Column("canonical_name", sa.String(length=512), nullable=False),
        sa.Column("place_types", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("formatted_address", sa.String(length=512), nullable=True),
        sa.Column("city_name", sa.String(length=160), nullable=True),
        sa.Column("region_name", sa.String(length=160), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("international_phone_number", sa.String(length=64), nullable=True),
        sa.Column("raw_website", sa.String(length=512), nullable=True),
        sa.Column("official_website", sa.String(length=512), nullable=True),
        sa.Column("is_relevant", sa.Boolean(), nullable=True),
        sa.Column("relevance_reason", sa.String(length=300), nullable=True),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("discovered_via_query", sa.String(length=300), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "google_place_id", name="uq_maps_place_run_google_id"),
    )
    op.create_index("ix_maps_places_run_id", "maps_places", ["run_id"])
    op.create_index("ix_maps_places_is_relevant", "maps_places", ["is_relevant"])


def downgrade() -> None:
    op.drop_table("maps_places")
    op.drop_table("maps_census_cells")
    op.drop_table("maps_census_runs")
