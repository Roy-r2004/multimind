"""Track how a Maps place's official website was found (places vs. search fallback).

Revision ID: 026
Revises: 025
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("maps_places", sa.Column("website_source", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("maps_places", "website_source")
