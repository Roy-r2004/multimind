"""Add content labels and saved documents

Revision ID: 019
Revises: 018
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "content_labels",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("org_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("org_id", "user_id", "name", name="uq_content_label_org_user_name"),
    )
    op.create_index("ix_content_labels_org_user", "content_labels", ["org_id", "user_id"])

    op.create_table(
        "saved_documents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("org_id", sa.String(length=36), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("chat_id", sa.String(length=36), sa.ForeignKey("chats.id", ondelete="SET NULL"), nullable=True),
        sa.Column("turn_id", sa.String(length=36), sa.ForeignKey("turns.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "project_id",
            sa.String(length=36),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("chat_title", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("project_name", sa.String(length=255), nullable=True),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_saved_documents_org_user_updated",
        "saved_documents",
        ["org_id", "user_id", "updated_at"],
    )
    op.create_index("ix_saved_documents_turn_id", "saved_documents", ["turn_id"])

    op.create_table(
        "saved_document_labels",
        sa.Column(
            "document_id",
            sa.String(length=36),
            sa.ForeignKey("saved_documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "label_id",
            sa.String(length=36),
            sa.ForeignKey("content_labels.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.UniqueConstraint("document_id", "label_id", name="uq_saved_document_label"),
    )


def downgrade() -> None:
    op.drop_table("saved_document_labels")
    op.drop_index("ix_saved_documents_turn_id", table_name="saved_documents")
    op.drop_index("ix_saved_documents_org_user_updated", table_name="saved_documents")
    op.drop_table("saved_documents")
    op.drop_index("ix_content_labels_org_user", table_name="content_labels")
    op.drop_table("content_labels")
