"""Add per-model-set referee_system_prompt override.

Revision ID: 051
Revises: 050
Create Date: 2026-09-06
"""

import sqlalchemy as sa

from alembic import op

revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_sets", sa.Column("referee_system_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("model_sets", "referee_system_prompt")
