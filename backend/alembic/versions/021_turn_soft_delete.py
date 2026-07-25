"""Add soft-delete support to turns table

Revision ID: 021
Revises: 020
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("turns", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_turns_deleted_at", "turns", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_turns_deleted_at", table_name="turns")
    op.drop_column("turns", "deleted_at")
