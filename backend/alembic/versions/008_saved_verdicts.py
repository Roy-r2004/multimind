"""Add saved verdict snapshots

Revision ID: 008
Revises: 007
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_verdicts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("org_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("source_verdict_id", sa.String(36), nullable=False),
        sa.Column("source_turn_id", sa.String(36), nullable=True),
        sa.Column("source_chat_id", sa.String(36), nullable=True),
        sa.Column("source_chat_title", sa.String(512), nullable=False),
        sa.Column("source_user_message", sa.Text(), nullable=False),
        sa.Column("verdict_text", sa.Text(), nullable=False),
        sa.Column("verdict_reason", sa.Text(), nullable=False),
        sa.Column("verdict_model_id", sa.String(64), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column(
            "saved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "user_id", "source_verdict_id", name="uq_saved_verdict_user_source"
        ),
    )
    op.create_index(
        "ix_saved_verdicts_org_user_saved_at",
        "saved_verdicts",
        ["org_id", "user_id", "saved_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_saved_verdicts_org_user_saved_at", table_name="saved_verdicts")
    op.drop_table("saved_verdicts")
