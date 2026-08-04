"""Add verdict_text to saved_prompts (nullable for existing rows).

Revision ID: 038
Revises: 037
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "saved_prompts",
        sa.Column("verdict_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("saved_prompts", "verdict_text")
