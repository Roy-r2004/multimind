"""Add cascaded enrichment pipeline fields to maps_places."""

from alembic import op
import sqlalchemy as sa

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_places",
        sa.Column("enrichment_pipeline_state", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "maps_places",
        sa.Column("website_relationship", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "maps_places",
        sa.Column("website_relationship_confidence", sa.Numeric(precision=5, scale=4), nullable=True),
    )
    op.add_column(
        "maps_places",
        sa.Column("website_relationship_evidence", sa.JSON(), nullable=True),
    )
    op.add_column(
        "maps_places",
        sa.Column("website_resolution_source", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "maps_places",
        sa.Column("enrichment_extraction_source", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("maps_places", "enrichment_extraction_source")
    op.drop_column("maps_places", "website_resolution_source")
    op.drop_column("maps_places", "website_relationship_evidence")
    op.drop_column("maps_places", "website_relationship_confidence")
    op.drop_column("maps_places", "website_relationship")
    op.drop_column("maps_places", "enrichment_pipeline_state")
