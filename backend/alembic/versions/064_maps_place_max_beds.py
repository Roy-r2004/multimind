"""Add max_beds column to maps_places.

Manual-only capacity override. Always NULL until a user enters it via the
facility editing UI; the scraper and enrichment pipeline never read or write
this column (unlike bed_count, which the AI extraction path does populate).

Revision ID: 064
Revises: 063
Create Date: 2026-11-17
"""

from alembic import op
import sqlalchemy as sa

revision = "064"
down_revision = "063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_places",
        sa.Column("max_beds", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("maps_places", "max_beds")
