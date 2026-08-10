"""Add Library (folders, labels, items) and chat_attachments.library_item_id.

Revision ID: 044
Revises: 043
Create Date: 2026-08-07
"""

import sqlalchemy as sa

from alembic import op

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "library_folders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "parent_id",
            sa.String(length=36),
            sa.ForeignKey("library_folders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
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
        "ix_library_folders_org_user_parent",
        "library_folders",
        ["org_id", "user_id", "parent_id"],
    )

    op.create_table(
        "library_labels",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
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
        sa.UniqueConstraint(
            "org_id", "user_id", "name", name="uq_library_label_org_user_name"
        ),
    )
    op.create_index(
        "ix_library_labels_org_user",
        "library_labels",
        ["org_id", "user_id"],
    )

    op.create_table(
        "library_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("item_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "folder_id",
            sa.String(length=36),
            sa.ForeignKey("library_folders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("stored_name", sa.String(length=255), nullable=True),
        sa.Column("relative_path", sa.String(length=1024), nullable=True),
        sa.Column("text_excerpt", sa.Text(), nullable=True),
        sa.Column("excerpt_status", sa.String(length=32), nullable=True),
        sa.Column("content_text", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "item_type IN ('file', 'document')",
            name="ck_library_items_item_type",
        ),
    )
    op.create_index(
        "ix_library_items_org_user_updated",
        "library_items",
        ["org_id", "user_id", "updated_at"],
    )
    op.create_index(
        "ix_library_items_org_user_folder",
        "library_items",
        ["org_id", "user_id", "folder_id"],
    )
    op.create_index(
        "ix_library_items_org_user_favorite",
        "library_items",
        ["org_id", "user_id", "is_favorite"],
    )

    op.create_table(
        "library_item_labels",
        sa.Column(
            "item_id",
            sa.String(length=36),
            sa.ForeignKey("library_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "label_id",
            sa.String(length=36),
            sa.ForeignKey("library_labels.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.UniqueConstraint("item_id", "label_id", name="uq_library_item_label"),
    )

    op.add_column(
        "chat_attachments",
        sa.Column(
            "library_item_id",
            sa.String(length=36),
            sa.ForeignKey("library_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_chat_attachments_library_item_id",
        "chat_attachments",
        ["library_item_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_attachments_library_item_id", table_name="chat_attachments"
    )
    op.drop_column("chat_attachments", "library_item_id")

    op.drop_table("library_item_labels")
    op.drop_index(
        "ix_library_items_org_user_favorite", table_name="library_items"
    )
    op.drop_index(
        "ix_library_items_org_user_folder", table_name="library_items"
    )
    op.drop_index(
        "ix_library_items_org_user_updated", table_name="library_items"
    )
    op.drop_table("library_items")
    op.drop_index("ix_library_labels_org_user", table_name="library_labels")
    op.drop_table("library_labels")
    op.drop_index(
        "ix_library_folders_org_user_parent", table_name="library_folders"
    )
    op.drop_table("library_folders")
