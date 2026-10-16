"""Durable processed documents and document-processing costs.

Revision ID: 060
Revises: 059

Organization-scoped SHA-256 rows, page sections, retrieval chunks, and nullable
cost-record chat/turn links so OCR and embedding charges can be stored before
a chat turn exists.
"""

import sqlalchemy as sa

from alembic import op

revision = "060"
down_revision = "059"
branch_labels = None
depends_on = None

ENUM_TYPE = "usagekind"
ENUM_LABEL = "DOCUMENT_PROCESSING"
ADD_VALUE_SQL = "ALTER TYPE usagekind ADD VALUE IF NOT EXISTS 'DOCUMENT_PROCESSING'"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        exists = bind.execute(
            sa.text(
                """
                SELECT 1
                FROM pg_type
                WHERE typname = :typname
                  AND typtype = 'e'
                """
            ),
            {"typname": ENUM_TYPE},
        ).scalar()
        if exists:
            with op.get_context().autocommit_block():
                op.execute(sa.text(ADD_VALUE_SQL))

    op.add_column("chat_attachments", sa.Column("content_sha256", sa.String(length=64), nullable=True))
    op.add_column("library_items", sa.Column("content_sha256", sa.String(length=64), nullable=True))
    op.alter_column("cost_records", "chat_id", existing_type=sa.String(length=36), nullable=True)
    op.alter_column("cost_records", "turn_id", existing_type=sa.String(length=36), nullable=True)

    op.create_table(
        "processed_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("pages_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pages_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unread_pages", sa.JSON(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "content_sha256", name="uq_processed_documents_org_sha"),
    )
    op.create_index(
        "ix_processed_documents_org_status",
        "processed_documents",
        ["org_id", "status"],
    )
    op.create_table(
        "processed_sections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("section_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("worksheet", sa.String(length=255), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["processed_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "section_index", name="uq_processed_sections_doc_index"),
    )
    op.create_index("ix_processed_sections_org", "processed_sections", ["org_id"])
    op.create_table(
        "processed_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["processed_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_processed_chunks_doc_index"),
    )
    op.create_index("ix_processed_chunks_org", "processed_chunks", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_processed_chunks_org", table_name="processed_chunks")
    op.drop_table("processed_chunks")
    op.drop_index("ix_processed_sections_org", table_name="processed_sections")
    op.drop_table("processed_sections")
    op.drop_index("ix_processed_documents_org_status", table_name="processed_documents")
    op.drop_table("processed_documents")
    op.drop_column("library_items", "content_sha256")
    op.drop_column("chat_attachments", "content_sha256")
    # PostgreSQL cannot drop a single enum label. Leave DOCUMENT_PROCESSING in place.
    # Restoring NOT NULL on cost_records would fail once null document costs exist.
