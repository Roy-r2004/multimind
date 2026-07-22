"""Add secure source retrieval persistence

Revision ID: 014
Revises: 013
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), nullable=False)


def upgrade() -> None:
    op.create_table(
        "scraping_source_retrieval_attempts",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("source_candidate_id", sa.String(length=36), nullable=False),
        sa.Column("coverage_cell_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("requested_url", sa.String(length=2048), nullable=False),
        sa.Column("final_url", sa.String(length=2048), nullable=True),
        sa.Column("redirect_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("declared_content_length", sa.Integer(), nullable=True),
        sa.Column("bytes_received", sa.Integer(), nullable=True),
        sa.Column("robots_status", sa.String(length=40), nullable=True),
        sa.Column("failure_classification", sa.String(length=80), nullable=True),
        sa.Column("safe_error_message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("metadata_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_candidate_id"], ["scraping_source_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["coverage_cell_id"], ["scraping_coverage_cells.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["scraping_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_source_retrieval_attempt_idempotency",
        ),
        sa.CheckConstraint("redirect_count >= 0", name="ck_source_retrieval_attempt_redirect_count"),
        sa.CheckConstraint(
            "bytes_received IS NULL OR bytes_received >= 0",
            name="ck_source_retrieval_attempt_bytes_received",
        ),
    )
    op.create_index("ix_source_retrieval_attempts_org", "scraping_source_retrieval_attempts", ["organization_id"])
    op.create_index(
        "ix_source_retrieval_attempts_execution",
        "scraping_source_retrieval_attempts",
        ["execution_id"],
    )
    op.create_index(
        "ix_source_retrieval_attempts_candidate",
        "scraping_source_retrieval_attempts",
        ["source_candidate_id"],
    )
    op.create_index(
        "ix_source_retrieval_attempts_coverage",
        "scraping_source_retrieval_attempts",
        ["coverage_cell_id"],
    )
    op.create_index("ix_source_retrieval_attempts_task", "scraping_source_retrieval_attempts", ["task_id"])
    op.create_index("ix_source_retrieval_attempts_status", "scraping_source_retrieval_attempts", ["status"])
    op.create_index(
        "ix_source_retrieval_attempts_context",
        "scraping_source_retrieval_attempts",
        ["organization_id", "execution_id", "source_candidate_id", "status"],
    )

    op.create_table(
        "scraping_source_documents",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("source_candidate_id", sa.String(length=36), nullable=False),
        sa.Column("retrieval_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("final_url", sa.String(length=2048), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("charset", sa.String(length=80), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("retrieval_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_candidate_id"], ["scraping_source_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["retrieval_attempt_id"],
            ["scraping_source_retrieval_attempts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "source_candidate_id",
            "content_sha256",
            name="uq_source_document_candidate_hash",
        ),
        sa.UniqueConstraint("retrieval_attempt_id", name="uq_source_document_retrieval_attempt"),
        sa.CheckConstraint("byte_size >= 0", name="ck_source_document_byte_size"),
    )
    op.create_index("ix_source_documents_org", "scraping_source_documents", ["organization_id"])
    op.create_index("ix_source_documents_execution", "scraping_source_documents", ["execution_id"])
    op.create_index("ix_source_documents_candidate", "scraping_source_documents", ["source_candidate_id"])
    op.create_index("ix_source_documents_attempt", "scraping_source_documents", ["retrieval_attempt_id"])
    op.create_index("ix_source_documents_hash", "scraping_source_documents", ["content_sha256"])
    op.create_index("ix_source_documents_retrieved", "scraping_source_documents", ["retrieval_timestamp"])
    op.create_index(
        "ix_source_documents_context",
        "scraping_source_documents",
        ["organization_id", "execution_id", "source_candidate_id", "retrieval_timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_documents_context", table_name="scraping_source_documents")
    op.drop_index("ix_source_documents_retrieved", table_name="scraping_source_documents")
    op.drop_index("ix_source_documents_hash", table_name="scraping_source_documents")
    op.drop_index("ix_source_documents_attempt", table_name="scraping_source_documents")
    op.drop_index("ix_source_documents_candidate", table_name="scraping_source_documents")
    op.drop_index("ix_source_documents_execution", table_name="scraping_source_documents")
    op.drop_index("ix_source_documents_org", table_name="scraping_source_documents")
    op.drop_table("scraping_source_documents")

    op.drop_index("ix_source_retrieval_attempts_context", table_name="scraping_source_retrieval_attempts")
    op.drop_index("ix_source_retrieval_attempts_status", table_name="scraping_source_retrieval_attempts")
    op.drop_index("ix_source_retrieval_attempts_task", table_name="scraping_source_retrieval_attempts")
    op.drop_index("ix_source_retrieval_attempts_coverage", table_name="scraping_source_retrieval_attempts")
    op.drop_index("ix_source_retrieval_attempts_candidate", table_name="scraping_source_retrieval_attempts")
    op.drop_index("ix_source_retrieval_attempts_execution", table_name="scraping_source_retrieval_attempts")
    op.drop_index("ix_source_retrieval_attempts_org", table_name="scraping_source_retrieval_attempts")
    op.drop_table("scraping_source_retrieval_attempts")
