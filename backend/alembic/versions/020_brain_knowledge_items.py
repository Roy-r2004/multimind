"""Add brain knowledge items for hybrid retrieval

Revision ID: 020
Revises: 019
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brain_knowledge_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("org_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "org_id",
            "user_id",
            "source_type",
            "source_id",
            name="uq_brain_knowledge_source",
        ),
    )
    op.create_index("ix_brain_knowledge_org_user", "brain_knowledge_items", ["org_id", "user_id"])
    op.create_index("ix_brain_knowledge_project", "brain_knowledge_items", ["project_id"])
    op.create_index("ix_brain_knowledge_source_type", "brain_knowledge_items", ["source_type"])


def downgrade() -> None:
    op.drop_index("ix_brain_knowledge_source_type", table_name="brain_knowledge_items")
    op.drop_index("ix_brain_knowledge_project", table_name="brain_knowledge_items")
    op.drop_index("ix_brain_knowledge_org_user", table_name="brain_knowledge_items")
    op.drop_table("brain_knowledge_items")
