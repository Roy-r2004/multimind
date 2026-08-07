"""Add index on chats(org_id, updated_at) for history listing.

Revision ID: 043
Revises: 042
Create Date: 2026-08-07

Non-destructive: creates/drops index only. Does not modify chat rows.
"""

from alembic import op

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_chats_org_updated_at",
        "chats",
        ["org_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chats_org_updated_at", table_name="chats")
