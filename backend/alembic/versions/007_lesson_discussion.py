"""Add disagreement discussion support to verdict lessons

Revision ID: 007
Revises: 006
Create Date: 2026-07-08
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "verdict_lessons",
        sa.Column("discussion_messages", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("verdict_lessons", "discussion_messages")
