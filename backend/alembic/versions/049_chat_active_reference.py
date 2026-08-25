"""Add persistent active single-chat reference.

Revision ID: 049
Revises: 048
Create Date: 2026-08-25
"""

import sqlalchemy as sa

from alembic import op

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.add_column(
            sa.Column("active_referenced_chat_id", sa.String(length=36), nullable=True)
        )
        batch.create_foreign_key(
            "fk_chats_active_referenced_chat_id_chats",
            "chats", ["active_referenced_chat_id"], ["id"], ondelete="SET NULL",
        )
        batch.create_index("ix_chats_active_referenced_chat_id", ["active_referenced_chat_id"])


def downgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.drop_index("ix_chats_active_referenced_chat_id")
        batch.drop_constraint("fk_chats_active_referenced_chat_id_chats", type_="foreignkey")
        batch.drop_column("active_referenced_chat_id")
