"""Add manually_excluded_at column to maps_places.

Distinct signal from AI classification (lifecycle_status/client_eligibility):
a place can be AI-classified as "unrelated" and still show in the Phase 1
"every raw result" view, but a user-initiated removal must hide it there.

Revision ID: 041
Revises: 040
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa

revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_places",
        sa.Column("manually_excluded_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("maps_places", "manually_excluded_at")
