"""verdict_lessons table for disagreement lessons

Revision ID: 003
Revises: 002
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "verdict_lessons",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("turn_id", sa.String(36), sa.ForeignKey("turns.id"), nullable=False),
        sa.Column("chat_id", sa.String(36), sa.ForeignKey("chats.id"), nullable=False),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("disagreement_reason", sa.Text(), nullable=False),
        sa.Column("user_position", sa.Text(), nullable=False),
        sa.Column("verdict_model_id", sa.String(64), nullable=False),
        sa.Column("verdict_model_name", sa.String(255), nullable=False),
        sa.Column("verdict_text", sa.Text(), nullable=False),
        sa.Column("verdict_reason", sa.Text(), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("comparison", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="building"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("turn_id", name="uq_verdict_lesson_turn"),
    )


def downgrade() -> None:
    op.drop_table("verdict_lessons")
