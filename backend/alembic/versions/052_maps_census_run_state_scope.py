"""Add state_code and state_name to maps_census_runs.

Revision ID: 052
Revises: 051
Create Date: 2026-09-07
"""

import sqlalchemy as sa
from alembic import op

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("maps_census_runs", sa.Column("state_code", sa.String(length=10), nullable=True))
    op.add_column("maps_census_runs", sa.Column("state_name", sa.String(length=120), nullable=True))


def downgrade() -> None:
    op.drop_column("maps_census_runs", "state_name")
    op.drop_column("maps_census_runs", "state_code")
