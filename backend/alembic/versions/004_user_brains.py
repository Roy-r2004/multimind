"""user_brains table — persistent user memory

Revision ID: 004
Revises: 003
Create Date: 2026-06-25
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_brains",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_name", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("thinking_style", sa.Text(), nullable=False, server_default=""),
        sa.Column("likes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("dislikes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("memories", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("lesson_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", name="uq_user_brain_user"),
    )


def downgrade() -> None:
    op.drop_table("user_brains")
