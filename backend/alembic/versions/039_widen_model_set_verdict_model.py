"""Widen model_sets.verdict_model (and turns) for OpenRouter or: ids.

SQLite does not enforce VARCHAR(64); PostgreSQL does. Custom OpenRouter
model ids (`or:provider--model-name`) and the UI's habit of copying long
descriptions into best_for caused production INSERT/UPDATE 500s while
local creates succeeded.

Revision ID: 039
Revises: 038
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "model_sets",
        "verdict_model",
        existing_type=sa.String(length=64),
        type_=sa.String(length=128),
        existing_nullable=False,
    )
    op.alter_column(
        "turns",
        "verdict_model",
        existing_type=sa.String(length=64),
        type_=sa.String(length=128),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "turns",
        "verdict_model",
        existing_type=sa.String(length=128),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.alter_column(
        "model_sets",
        "verdict_model",
        existing_type=sa.String(length=128),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
