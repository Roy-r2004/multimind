"""Add chat_attachments for composer file uploads.

Revision ID: 042
Revises: 041
Create Date: 2026-08-05
"""

import sqlalchemy as sa

from alembic import op

revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_attachments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "chat_id",
            sa.String(length=36),
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "uploaded_by_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "turn_id",
            sa.String(length=36),
            sa.ForeignKey("turns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("stored_name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("text_excerpt", sa.Text(), nullable=True),
        sa.Column("excerpt_status", sa.String(length=32), nullable=False),
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
    )
    op.create_index(
        "ix_chat_attachments_org_chat",
        "chat_attachments",
        ["org_id", "chat_id"],
    )
    op.create_index(
        "ix_chat_attachments_turn_id",
        "chat_attachments",
        ["turn_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_attachments_turn_id", table_name="chat_attachments")
    op.drop_index("ix_chat_attachments_org_chat", table_name="chat_attachments")
    op.drop_table("chat_attachments")
