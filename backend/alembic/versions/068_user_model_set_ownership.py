"""Add per-user ownership for model sets.

Revision ID: 068
Revises: 067
"""

import sqlalchemy as sa
from alembic import op

revision = "068"
down_revision = "067_project_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_sets",
        sa.Column(
            "owner_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.create_index("ix_model_sets_owner_user_id", "model_sets", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_model_sets_owner_user_id", table_name="model_sets")
    op.drop_column("model_sets", "owner_user_id")
