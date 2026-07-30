"""Track automatic missing-website refresh attempts on Maps census runs.

Revision ID: 027
Revises: 026
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_census_runs",
        sa.Column("website_refresh_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "maps_census_runs",
        sa.Column("website_refresh_completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("maps_census_runs", "website_refresh_completed_at")
    op.drop_column("maps_census_runs", "website_refresh_attempts")
