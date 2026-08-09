"""Add bed_count column to maps_places.

Residential/inpatient capacity (number of beds), extracted during detail
enrichment the same way treatment_price already is — a stated fact from web
evidence, null when a facility doesn't publish it.

Revision ID: 044
Revises: 043
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_places",
        sa.Column("bed_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("maps_places", "bed_count")
