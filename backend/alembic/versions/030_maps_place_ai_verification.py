"""Add web-search verification verdict fields for Maps Census places.

Revision ID: 030
Revises: 029
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_places", sa.Column("verification_verdict", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "maps_places", sa.Column("verification_reason", sa.String(length=400), nullable=True)
    )
    op.add_column(
        "maps_places", sa.Column("verification_source_url", sa.String(length=1024), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("maps_places", "verification_source_url")
    op.drop_column("maps_places", "verification_reason")
    op.drop_column("maps_places", "verification_verdict")
