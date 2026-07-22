"""Add durable turn cancellation signal

Revision ID: 018
Revises: 017
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "turns",
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_turns_cancel_requested_at", "turns", ["cancel_requested_at"])


def downgrade() -> None:
    op.drop_index("ix_turns_cancel_requested_at", table_name="turns")
    op.drop_column("turns", "cancel_requested_at")
