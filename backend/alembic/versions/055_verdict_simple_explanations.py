"""Auxiliary Simple Explanation rows linked to verdicts.

Revision ID: 055
Revises: 054
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from alembic import op

revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "verdict_simple_explanations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("verdict_id", sa.String(length=36), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=True),
        sa.Column("tokens_output", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["verdict_id"], ["verdicts.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("verdict_id", name="uq_verdict_simple_explanation_verdict"),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="ck_verdict_simple_explanation_status",
        ),
    )
    op.create_index(
        "ix_verdict_simple_explanations_verdict_id",
        "verdict_simple_explanations",
        ["verdict_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_verdict_simple_explanations_verdict_id",
        table_name="verdict_simple_explanations",
    )
    op.drop_table("verdict_simple_explanations")
