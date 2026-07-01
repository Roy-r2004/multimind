"""Keep verdict lessons when chats or turns are deleted

Revision ID: 006
Revises: 005
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("verdict_lessons_turn_id_fkey", "verdict_lessons", type_="foreignkey")
    op.drop_constraint("verdict_lessons_chat_id_fkey", "verdict_lessons", type_="foreignkey")
    op.alter_column("verdict_lessons", "turn_id", existing_type=sa.String(36), nullable=True)
    op.alter_column("verdict_lessons", "chat_id", existing_type=sa.String(36), nullable=True)
    op.create_foreign_key(
        "verdict_lessons_turn_id_fkey",
        "verdict_lessons",
        "turns",
        ["turn_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "verdict_lessons_chat_id_fkey",
        "verdict_lessons",
        "chats",
        ["chat_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("verdict_lessons_turn_id_fkey", "verdict_lessons", type_="foreignkey")
    op.drop_constraint("verdict_lessons_chat_id_fkey", "verdict_lessons", type_="foreignkey")
    op.create_foreign_key(
        "verdict_lessons_turn_id_fkey",
        "verdict_lessons",
        "turns",
        ["turn_id"],
        ["id"],
    )
    op.create_foreign_key(
        "verdict_lessons_chat_id_fkey",
        "verdict_lessons",
        "chats",
        ["chat_id"],
        ["id"],
    )
    op.alter_column("verdict_lessons", "turn_id", existing_type=sa.String(36), nullable=False)
    op.alter_column("verdict_lessons", "chat_id", existing_type=sa.String(36), nullable=False)
