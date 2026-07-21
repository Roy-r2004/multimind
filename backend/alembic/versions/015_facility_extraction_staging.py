"""Add facility extraction staging persistence

Revision ID: 015
Revises: 014
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa

revision = "015"
down_revision = "014"
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
        "scraping_source_document_texts",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("source_candidate_id", sa.String(length=36), nullable=True),
        sa.Column("coverage_cell_id", sa.String(length=36), nullable=True),
        sa.Column("parser_version", sa.String(length=40), nullable=False),
        sa.Column("source_content_hash", sa.String(length=64), nullable=False),
        sa.Column("prepared_text_hash", sa.String(length=64), nullable=False),
        sa.Column("detected_language", sa.String(length=16), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("prepared_text", sa.Text(), nullable=False),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("original_character_count", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("preparation_status", sa.String(length=40), nullable=False),
        sa.Column("failure_classification", sa.String(length=80), nullable=True),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["scraping_source_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_candidate_id"], ["scraping_source_candidates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["coverage_cell_id"], ["scraping_coverage_cells.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "source_document_id",
            "parser_version",
            "source_content_hash",
            name="uq_source_document_text_version",
        ),
        sa.CheckConstraint("character_count >= 0", name="ck_source_document_text_character_count"),
        sa.CheckConstraint("original_character_count >= 0", name="ck_source_document_text_original_character_count"),
    )
    for name, cols in {
        "ix_source_document_texts_org": ["organization_id"],
        "ix_source_document_texts_execution": ["execution_id"],
        "ix_source_document_texts_document": ["source_document_id"],
        "ix_source_document_texts_candidate": ["source_candidate_id"],
        "ix_source_document_texts_coverage": ["coverage_cell_id"],
        "ix_source_document_texts_hash": ["prepared_text_hash"],
        "ix_source_document_texts_status": ["preparation_status"],
    }.items():
        op.create_index(name, "scraping_source_document_texts", cols)

    op.create_table(
        "scraping_source_document_chunks",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("prepared_text_id", sa.String(length=36), nullable=False),
        sa.Column("coverage_cell_id", sa.String(length=36), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("character_start", sa.Integer(), nullable=False),
        sa.Column("character_end", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("chunk_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        id_column(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["scraping_source_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prepared_text_id"], ["scraping_source_document_texts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["coverage_cell_id"], ["scraping_coverage_cells.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prepared_text_id", "chunk_index", name="uq_source_document_chunk_index"),
        sa.UniqueConstraint("prepared_text_id", "chunk_hash", name="uq_source_document_chunk_hash"),
        sa.CheckConstraint("chunk_index >= 0", name="ck_source_document_chunk_index"),
        sa.CheckConstraint("character_start >= 0", name="ck_source_document_chunk_start"),
        sa.CheckConstraint("character_end > character_start", name="ck_source_document_chunk_end"),
    )
    for name, cols in {
        "ix_source_document_chunks_org": ["organization_id"],
        "ix_source_document_chunks_execution": ["execution_id"],
        "ix_source_document_chunks_document": ["source_document_id"],
        "ix_source_document_chunks_prepared": ["prepared_text_id"],
        "ix_source_document_chunks_coverage": ["coverage_cell_id"],
    }.items():
        op.create_index(name, "scraping_source_document_chunks", cols)

    op.create_table(
        "scraping_facility_extraction_attempts",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("prepared_text_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=36), nullable=False),
        sa.Column("coverage_cell_id", sa.String(length=36), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("input_character_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_candidate_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failure_classification", sa.String(length=80), nullable=True),
        sa.Column("safe_error_message", sa.String(length=500), nullable=True),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("metadata_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        id_column(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["scraping_source_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prepared_text_id"], ["scraping_source_document_texts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chunk_id"], ["scraping_source_document_chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["coverage_cell_id"], ["scraping_coverage_cells.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_facility_extraction_attempt_idempotency"),
        sa.CheckConstraint("attempt_number >= 1", name="ck_facility_extraction_attempt_number"),
        sa.CheckConstraint("input_character_count >= 0", name="ck_facility_extraction_attempt_input_character_count"),
        sa.CheckConstraint("output_candidate_count >= 0", name="ck_facility_extraction_attempt_output_candidate_count"),
    )
    for name, cols in {
        "ix_facility_extraction_attempts_org": ["organization_id"],
        "ix_facility_extraction_attempts_execution": ["execution_id"],
        "ix_facility_extraction_attempts_document": ["source_document_id"],
        "ix_facility_extraction_attempts_chunk": ["chunk_id"],
        "ix_facility_extraction_attempts_coverage": ["coverage_cell_id"],
        "ix_facility_extraction_attempts_status": ["status"],
        "ix_facility_extraction_attempts_context": ["organization_id", "execution_id", "chunk_id", "status"],
    }.items():
        op.create_index(name, "scraping_facility_extraction_attempts", cols)

    op.create_table(
        "scraping_facility_candidates",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("coverage_cell_id", sa.String(length=36), nullable=True),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("prepared_text_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=36), nullable=False),
        sa.Column("extraction_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("raw_name", sa.String(length=255), nullable=False),
        sa.Column("raw_payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("model_confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column("staging_status", sa.String(length=40), nullable=False),
        sa.Column("candidate_fingerprint", sa.String(length=64), nullable=False),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["coverage_cell_id"], ["scraping_coverage_cells.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_document_id"], ["scraping_source_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prepared_text_id"], ["scraping_source_document_texts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chunk_id"], ["scraping_source_document_chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["extraction_attempt_id"],
            ["scraping_facility_extraction_attempts.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "extraction_attempt_id",
            "candidate_fingerprint",
            name="uq_facility_candidate_attempt_fingerprint",
        ),
        sa.CheckConstraint("length(trim(raw_name)) > 0", name="ck_facility_candidate_raw_name"),
        sa.CheckConstraint(
            "model_confidence IS NULL OR (model_confidence >= 0 AND model_confidence <= 1)",
            name="ck_facility_candidate_model_confidence",
        ),
    )
    for name, cols in {
        "ix_facility_candidates_org": ["organization_id"],
        "ix_facility_candidates_execution": ["execution_id"],
        "ix_facility_candidates_coverage": ["coverage_cell_id"],
        "ix_facility_candidates_document": ["source_document_id"],
        "ix_facility_candidates_chunk": ["chunk_id"],
        "ix_facility_candidates_attempt": ["extraction_attempt_id"],
        "ix_facility_candidates_status": ["staging_status"],
        "ix_facility_candidates_fingerprint": ["candidate_fingerprint"],
    }.items():
        op.create_index(name, "scraping_facility_candidates", cols)

    op.create_table(
        "scraping_facility_candidate_evidence",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("facility_candidate_id", sa.String(length=36), nullable=False),
        sa.Column("source_document_id", sa.String(length=36), nullable=False),
        sa.Column("prepared_text_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=36), nullable=False),
        sa.Column("field_name", sa.String(length=120), nullable=False),
        sa.Column("raw_value", sa.JSON(), nullable=True),
        sa.Column("evidence_quote", sa.String(length=1000), nullable=False),
        sa.Column("quote_start", sa.Integer(), nullable=False),
        sa.Column("quote_end", sa.Integer(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("verification_status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        id_column(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["facility_candidate_id"], ["scraping_facility_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["scraping_source_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["prepared_text_id"], ["scraping_source_document_texts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chunk_id"], ["scraping_source_document_chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "facility_candidate_id",
            "field_name",
            "evidence_hash",
            name="uq_facility_candidate_evidence_field_hash",
        ),
        sa.CheckConstraint("quote_start >= 0", name="ck_facility_candidate_evidence_quote_start"),
        sa.CheckConstraint("quote_end > quote_start", name="ck_facility_candidate_evidence_quote_end"),
        sa.CheckConstraint("length(evidence_quote) <= 1000", name="ck_facility_candidate_evidence_quote_length"),
    )
    for name, cols in {
        "ix_facility_candidate_evidence_org": ["organization_id"],
        "ix_facility_candidate_evidence_execution": ["execution_id"],
        "ix_facility_candidate_evidence_candidate": ["facility_candidate_id"],
        "ix_facility_candidate_evidence_document": ["source_document_id"],
        "ix_facility_candidate_evidence_chunk": ["chunk_id"],
        "ix_facility_candidate_evidence_field": ["field_name"],
        "ix_facility_candidate_evidence_status": ["verification_status"],
    }.items():
        op.create_index(name, "scraping_facility_candidate_evidence", cols)


def downgrade() -> None:
    for name in [
        "ix_facility_candidate_evidence_status",
        "ix_facility_candidate_evidence_field",
        "ix_facility_candidate_evidence_chunk",
        "ix_facility_candidate_evidence_document",
        "ix_facility_candidate_evidence_candidate",
        "ix_facility_candidate_evidence_execution",
        "ix_facility_candidate_evidence_org",
    ]:
        op.drop_index(name, table_name="scraping_facility_candidate_evidence")
    op.drop_table("scraping_facility_candidate_evidence")

    for name in [
        "ix_facility_candidates_fingerprint",
        "ix_facility_candidates_status",
        "ix_facility_candidates_attempt",
        "ix_facility_candidates_chunk",
        "ix_facility_candidates_document",
        "ix_facility_candidates_coverage",
        "ix_facility_candidates_execution",
        "ix_facility_candidates_org",
    ]:
        op.drop_index(name, table_name="scraping_facility_candidates")
    op.drop_table("scraping_facility_candidates")

    for name in [
        "ix_facility_extraction_attempts_context",
        "ix_facility_extraction_attempts_status",
        "ix_facility_extraction_attempts_coverage",
        "ix_facility_extraction_attempts_chunk",
        "ix_facility_extraction_attempts_document",
        "ix_facility_extraction_attempts_execution",
        "ix_facility_extraction_attempts_org",
    ]:
        op.drop_index(name, table_name="scraping_facility_extraction_attempts")
    op.drop_table("scraping_facility_extraction_attempts")

    for name in [
        "ix_source_document_chunks_coverage",
        "ix_source_document_chunks_prepared",
        "ix_source_document_chunks_document",
        "ix_source_document_chunks_execution",
        "ix_source_document_chunks_org",
    ]:
        op.drop_index(name, table_name="scraping_source_document_chunks")
    op.drop_table("scraping_source_document_chunks")

    for name in [
        "ix_source_document_texts_status",
        "ix_source_document_texts_hash",
        "ix_source_document_texts_coverage",
        "ix_source_document_texts_candidate",
        "ix_source_document_texts_document",
        "ix_source_document_texts_execution",
        "ix_source_document_texts_org",
    ]:
        op.drop_index(name, table_name="scraping_source_document_texts")
    op.drop_table("scraping_source_document_texts")
