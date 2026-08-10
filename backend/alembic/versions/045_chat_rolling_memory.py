"""Add per-chat rolling continuation memory columns.

Revision ID: 045
Revises: 044
Create Date: 2026-08-07

Stores a compact summary of turns older than the recent-history window.
Separate from user-level Brain tables.
"""

from alembic import op
import sqlalchemy as sa

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.add_column(sa.Column("rolling_memory", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("rolling_memory_through_turn_id", sa.String(length=36), nullable=True)
        )
        batch.add_column(
            sa.Column("rolling_memory_updated_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_foreign_key(
            "fk_chats_rolling_memory_through_turn_id",
            "turns",
            ["rolling_memory_through_turn_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.drop_constraint("fk_chats_rolling_memory_through_turn_id", type_="foreignkey")
        batch.drop_column("rolling_memory_updated_at")
        batch.drop_column("rolling_memory_through_turn_id")
        batch.drop_column("rolling_memory")
