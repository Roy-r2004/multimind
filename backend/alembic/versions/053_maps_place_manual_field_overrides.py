"""Add manual_field_overrides JSON to maps_places.

Tracks export fields a user has edited so later AI enrichment skips those
columns without skipping the rest of the facility.

Revision ID: 053
Revises: 052
Create Date: 2026-09-07
"""

import sqlalchemy as sa
from alembic import op

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_places",
        sa.Column("manual_field_overrides", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("maps_places", "manual_field_overrides")
