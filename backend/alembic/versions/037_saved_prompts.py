"""Add saved_prompts table for user-question-only saves.

Revision ID: 037
Revises: 036
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_prompts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("org_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column(
            "chat_id",
            sa.String(length=36),
            sa.ForeignKey("chats.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "turn_id",
            sa.String(length=36),
            sa.ForeignKey("turns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_saved_prompts_org_user_updated",
        "saved_prompts",
        ["org_id", "user_id", "updated_at"],
    )
    op.create_index("ix_saved_prompts_turn_id", "saved_prompts", ["turn_id"])

    op.create_table(
        "saved_prompt_labels",
        sa.Column(
            "prompt_id",
            sa.String(length=36),
            sa.ForeignKey("saved_prompts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "label_id",
            sa.String(length=36),
            sa.ForeignKey("content_labels.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.UniqueConstraint("prompt_id", "label_id", name="uq_saved_prompt_label"),
    )


def downgrade() -> None:
    op.drop_table("saved_prompt_labels")
    op.drop_index("ix_saved_prompts_turn_id", table_name="saved_prompts")
    op.drop_index("ix_saved_prompts_org_user_updated", table_name="saved_prompts")
    op.drop_table("saved_prompts")
