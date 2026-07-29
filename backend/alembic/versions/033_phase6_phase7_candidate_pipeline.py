"""Phase 6 durable extraction work and Phase 7 candidate decisions.

Revision ID: 033
Revises: 032
"""

from alembic import op
import sqlalchemy as sa

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def _owner_fk(local: str, remote: str, name: str, *, ondelete: str = "RESTRICT"):
    return sa.ForeignKeyConstraint(
        [local, "organization_id", "execution_id"],
        [f"{remote}.id", f"{remote}.organization_id", f"{remote}.execution_id"],
        name=name, ondelete=ondelete,
    )


def upgrade() -> None:
    with op.batch_alter_table("scraping_source_document_chunks") as batch:
        batch.create_unique_constraint("uq_source_document_chunk_owner", ["id", "organization_id", "execution_id"])
        batch.add_column(sa.Column("retrieval_result_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("crawl_node_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("original_url", sa.String(2048), nullable=True))
        batch.add_column(sa.Column("representation_provenance", sa.JSON(), server_default=sa.text("'{}'"), nullable=False))
        batch.create_foreign_key("fk_chunk_retrieval_result", "scraping_phase5_retrieval_results",
                                 ["retrieval_result_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_chunk_crawl_node", "scraping_crawl_nodes",
                                 ["crawl_node_id"], ["id"], ondelete="SET NULL")
    with op.batch_alter_table("scraping_facility_candidates") as batch:
        batch.create_unique_constraint("uq_facility_candidate_owner", ["id", "organization_id", "execution_id"])
        batch.add_column(sa.Column("directory_observation_id", sa.String(36), nullable=True))
        batch.create_foreign_key("fk_candidate_directory_observation", "scraping_directory_observations",
                                 ["directory_observation_id"], ["id"], ondelete="SET NULL")
    with op.batch_alter_table("scraping_facility_candidate_evidence") as batch:
        batch.add_column(sa.Column("retrieval_result_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("crawl_node_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("source_url", sa.String(2048), nullable=True))
        batch.create_foreign_key("fk_evidence_retrieval_result", "scraping_phase5_retrieval_results",
                                 ["retrieval_result_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_evidence_crawl_node", "scraping_crawl_nodes",
                                 ["crawl_node_id"], ["id"], ondelete="SET NULL")

    op.create_table(
        "scraping_facility_phase_work_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("work_kind", sa.String(40), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("source_document_id", sa.String(36)),
        sa.Column("chunk_id", sa.String(36)),
        sa.Column("facility_candidate_id", sa.String(36)),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("claim_token", sa.String(64)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("failure_classification", sa.String(80)),
        sa.Column("safe_error_message", sa.String(500)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        _owner_fk("source_document_id", "scraping_source_documents", "fk_facility_job_document", ondelete="CASCADE"),
        _owner_fk("chunk_id", "scraping_source_document_chunks", "fk_facility_job_chunk", ondelete="CASCADE"),
        _owner_fk("facility_candidate_id", "scraping_facility_candidates", "fk_facility_job_candidate", ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "execution_id", "fingerprint", name="uq_facility_phase_job_fingerprint"),
        sa.UniqueConstraint("id", "organization_id", "execution_id", name="uq_facility_phase_job_owner"),
        sa.CheckConstraint("length(fingerprint) = 64", name="ck_facility_phase_job_fingerprint"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_facility_phase_job_attempt_count"),
        sa.CheckConstraint("max_attempts >= 1", name="ck_facility_phase_job_max_attempts"),
        sa.CheckConstraint("work_kind IN ('prepare_document','extract_chunk','verify_candidate','deduplicate_candidate')", name="ck_facility_phase_job_kind"),
        sa.CheckConstraint("status IN ('pending','running','retry_scheduled','succeeded','failed','cancelled')", name="ck_facility_phase_job_status"),
    )
    op.create_index("ix_facility_phase_jobs_claim", "scraping_facility_phase_work_jobs",
                    ["organization_id", "execution_id", "status", "next_retry_at"])
    op.create_index("ix_facility_phase_jobs_lease", "scraping_facility_phase_work_jobs",
                    ["status", "lease_expires_at"])

    op.create_table(
        "scraping_facility_candidate_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("facility_candidate_id", sa.String(36), nullable=False),
        sa.Column("canonical_candidate_id", sa.String(36)),
        sa.Column("requested_country_code", sa.String(2), nullable=False),
        sa.Column("country_decision", sa.String(40), nullable=False),
        sa.Column("country_reason", sa.String(255), nullable=False),
        sa.Column("country_evidence_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("normalized_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("identity_fingerprint", sa.String(64), nullable=False),
        sa.Column("final_status", sa.String(24), nullable=False),
        sa.Column("final_reason", sa.String(255), nullable=False),
        sa.Column("algorithm_version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        _owner_fk("facility_candidate_id", "scraping_facility_candidates", "fk_decision_candidate", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["canonical_candidate_id"], ["scraping_facility_candidates.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("organization_id", "execution_id", "facility_candidate_id", name="uq_facility_candidate_decision"),
        sa.CheckConstraint("country_decision IN ('inside_requested_country','outside_requested_country','uncertain')", name="ck_facility_candidate_country_decision"),
        sa.CheckConstraint("final_status IN ('accepted','needs_review','rejected')", name="ck_facility_candidate_final_status"),
    )
    op.create_index("ix_facility_candidate_decisions_status", "scraping_facility_candidate_decisions",
                    ["organization_id", "execution_id", "final_status"])
    op.create_index("ix_facility_candidate_identity", "scraping_facility_candidate_decisions",
                    ["organization_id", "execution_id", "identity_fingerprint"])

    op.create_table(
        "scraping_facility_candidate_duplicates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("left_candidate_id", sa.String(36), nullable=False),
        sa.Column("right_candidate_id", sa.String(36), nullable=False),
        sa.Column("relationship", sa.String(32), nullable=False),
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        sa.Column("reasons_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("algorithm_version", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        _owner_fk("left_candidate_id", "scraping_facility_candidates", "fk_duplicate_left", ondelete="CASCADE"),
        _owner_fk("right_candidate_id", "scraping_facility_candidates", "fk_duplicate_right", ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "execution_id", "left_candidate_id", "right_candidate_id",
                            name="uq_facility_candidate_duplicate_pair"),
        sa.CheckConstraint("left_candidate_id < right_candidate_id", name="ck_facility_candidate_duplicate_order"),
        sa.CheckConstraint("relationship IN ('probable_duplicate','distinct_branch')", name="ck_facility_candidate_duplicate_relationship"),
    )
    op.create_index("ix_facility_candidate_duplicates_execution", "scraping_facility_candidate_duplicates",
                    ["organization_id", "execution_id"])


def downgrade() -> None:
    bind = op.get_bind()
    durable_rows = sum(bind.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
                       for table in (
                           "scraping_facility_phase_work_jobs",
                           "scraping_facility_candidate_decisions",
                           "scraping_facility_candidate_duplicates",
                       ))
    provenance_rows = bind.execute(sa.text(
        "SELECT count(*) FROM scraping_source_document_chunks "
        "WHERE retrieval_result_id IS NOT NULL OR crawl_node_id IS NOT NULL "
        "OR original_url IS NOT NULL OR representation_provenance::text <> '{}'"
    )).scalar_one()
    evidence_rows = bind.execute(sa.text(
        "SELECT count(*) FROM scraping_facility_candidate_evidence "
        "WHERE retrieval_result_id IS NOT NULL OR crawl_node_id IS NOT NULL OR source_url IS NOT NULL"
    )).scalar_one()
    linked_candidates = bind.execute(sa.text(
        "SELECT count(*) FROM scraping_facility_candidates "
        "WHERE directory_observation_id IS NOT NULL"
    )).scalar_one()
    if durable_rows or provenance_rows or evidence_rows or linked_candidates:
        raise RuntimeError(
            "Refusing downgrade 033: Package A work or provenance exists; export or remove it explicitly first."
        )
    op.drop_table("scraping_facility_candidate_duplicates")
    op.drop_table("scraping_facility_candidate_decisions")
    op.drop_table("scraping_facility_phase_work_jobs")
    with op.batch_alter_table("scraping_facility_candidate_evidence") as batch:
        batch.drop_constraint("fk_evidence_crawl_node", type_="foreignkey")
        batch.drop_constraint("fk_evidence_retrieval_result", type_="foreignkey")
        batch.drop_column("source_url")
        batch.drop_column("crawl_node_id")
        batch.drop_column("retrieval_result_id")
    with op.batch_alter_table("scraping_facility_candidates") as batch:
        batch.drop_constraint("fk_candidate_directory_observation", type_="foreignkey")
        batch.drop_column("directory_observation_id")
        batch.drop_constraint("uq_facility_candidate_owner", type_="unique")
    with op.batch_alter_table("scraping_source_document_chunks") as batch:
        batch.drop_constraint("fk_chunk_crawl_node", type_="foreignkey")
        batch.drop_constraint("fk_chunk_retrieval_result", type_="foreignkey")
        batch.drop_column("representation_provenance")
        batch.drop_column("original_url")
        batch.drop_column("crawl_node_id")
        batch.drop_column("retrieval_result_id")
        batch.drop_constraint("uq_source_document_chunk_owner", type_="unique")
