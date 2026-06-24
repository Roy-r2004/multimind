"""org_models table for OpenRouter catalog additions

Revision ID: 002
Revises: 001
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_models",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("openrouter_slug", sa.String(256), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("vendor", sa.String(128), nullable=False),
        sa.Column("blurb", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("org_id", "model_id", name="uq_org_model"),
    )
    op.create_index("ix_org_models_model_id", "org_models", ["model_id"])


def downgrade() -> None:
    op.drop_index("ix_org_models_model_id", "org_models")
    op.drop_table("org_models")
