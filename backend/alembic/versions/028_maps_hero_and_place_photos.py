"""Add Pexels country hero image and Google Places photo reference columns.

Revision ID: 028
Revises: 027
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_census_runs", sa.Column("hero_image_url", sa.String(length=1024), nullable=True)
    )
    op.add_column(
        "maps_places", sa.Column("photo_reference", sa.String(length=300), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("maps_places", "photo_reference")
    op.drop_column("maps_census_runs", "hero_image_url")
