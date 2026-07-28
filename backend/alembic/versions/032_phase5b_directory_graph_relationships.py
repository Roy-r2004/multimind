"""Phase 5B representation-bound directory expansion state and graph types.

Revision ID: 032
Revises: 031
"""

import sqlalchemy as sa
from alembic import op

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None

_OLD = (
    "relationship_type IN ('directory_to_profile', 'profile_to_official_site', "
    "'official_site_to_contact_page', 'official_site_to_program_page', "
    "'official_site_to_location_page', 'official_site_to_licensing_page', "
    "'official_site_to_evidence_page', 'related_source', 'discovered_link')"
)
_NEW = (
    "relationship_type IN ('directory_to_profile', 'directory_to_official_site', "
    "'profile_to_official_site', 'pagination', 'load_more', 'structured_api', "
    "'official_site_to_contact_page', 'official_site_to_program_page', "
    "'official_site_to_location_page', 'official_site_to_licensing_page', "
    "'official_site_to_evidence_page', 'related_source', 'discovered_link')"
)


def upgrade() -> None:
    legacy = op.get_bind().exec_driver_sql(
        "SELECT count(*) FROM scraping_phase5_work_jobs "
        "WHERE work_kind = 'directory_expansion'"
    ).scalar()
    if legacy:
        raise RuntimeError(
            "Cannot upgrade 032 with unbound directory_expansion jobs; "
            "review and remove or recreate them against durable retrieval inputs.")
    with op.batch_alter_table("scraping_phase5_retrieval_results") as batch_op:
        batch_op.create_unique_constraint(
            "uq_phase5_retrieval_id_org_exec",
            ["id", "organization_id", "execution_id"])
    with op.batch_alter_table("scraping_phase5_work_jobs") as batch_op:
        batch_op.add_column(sa.Column("input_retrieval_result_id", sa.String(36)))
        batch_op.add_column(sa.Column("input_source_document_id", sa.String(36)))
        batch_op.add_column(sa.Column("input_content_fingerprint", sa.String(64)))
        batch_op.add_column(sa.Column("input_retrieval_method", sa.String(40)))
        batch_op.add_column(sa.Column("action_state_fingerprint", sa.String(64)))
        batch_op.add_column(sa.Column("next_entry_ordinal", sa.Integer(),
                                      nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("entries_completed", sa.Integer(),
                                      nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("expansion_completed", sa.Boolean(),
                                      nullable=False, server_default=sa.text("false")))
        batch_op.add_column(sa.Column("last_processed_slice_count", sa.Integer(),
                                      nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("expansion_parser_version", sa.String(80)))
        batch_op.add_column(sa.Column("parser_state_fingerprint", sa.String(64)))
        batch_op.add_column(sa.Column("expansion_outcome", sa.String(80)))
        batch_op.add_column(sa.Column("requires_managed_rendering", sa.Boolean(),
                                      nullable=False, server_default=sa.text("false")))
        batch_op.add_column(sa.Column("requires_browser_interaction", sa.Boolean(),
                                      nullable=False, server_default=sa.text("false")))
        batch_op.add_column(sa.Column(
            "continuation_markers_json", sa.JSON(), nullable=False,
            server_default="[]",
            comment="Sanitized Phase 5B continuation markers; safe observed/canonical URLs only"))
        batch_op.create_foreign_key(
            "fk_phase5_job_input_retrieval_org_exec",
            "scraping_phase5_retrieval_results",
            ["input_retrieval_result_id", "organization_id", "execution_id"],
            ["id", "organization_id", "execution_id"], ondelete="RESTRICT")
        batch_op.create_foreign_key(
            "fk_phase5_job_input_document_org_exec", "scraping_source_documents",
            ["input_source_document_id", "organization_id", "execution_id"],
            ["id", "organization_id", "execution_id"], ondelete="RESTRICT")
        batch_op.create_check_constraint(
            "ck_phase5_job_expansion_cursor",
            "next_entry_ordinal >= 0 AND entries_completed >= 0 AND "
            "last_processed_slice_count >= 0")
        batch_op.create_check_constraint(
            "ck_phase5_job_action_state_fingerprint",
            "action_state_fingerprint IS NULL OR "
            "length(trim(action_state_fingerprint)) = 64")
        batch_op.create_check_constraint(
            "ck_phase5_job_directory_input",
            "work_kind <> 'directory_expansion' OR "
            "(input_retrieval_result_id IS NOT NULL AND "
            "input_source_document_id IS NOT NULL AND "
            "input_content_fingerprint IS NOT NULL AND "
            "length(trim(input_content_fingerprint)) = 64 AND "
            "input_retrieval_method IN "
            "('http_retrieval','firecrawl_retrieval','playwright_retrieval'))")
        batch_op.create_index(
            "ix_phase5_jobs_input_retrieval", ["input_retrieval_result_id"])
    with op.batch_alter_table("scraping_crawl_edges") as batch_op:
        batch_op.drop_constraint("ck_crawl_edge_relationship_type", type_="check")
        batch_op.create_check_constraint("ck_crawl_edge_relationship_type", _NEW)


def downgrade() -> None:
    # Existing Phase 5B-only edges deliberately make downgrade fail rather than
    # silently relabeling or deleting provenance.
    bind = op.get_bind()
    edge_count = bind.exec_driver_sql(
        "SELECT count(*) FROM scraping_crawl_edges WHERE relationship_type IN "
        "('directory_to_official_site','pagination','load_more','structured_api')"
    ).scalar()
    job_count = bind.exec_driver_sql(
        "SELECT count(*) FROM scraping_phase5_work_jobs "
        "WHERE work_kind = 'directory_expansion' OR action_state_fingerprint IS NOT NULL"
    ).scalar()
    if edge_count or job_count:
        raise RuntimeError(
            "Cannot downgrade 032 while Phase 5B-F provenance rows exist.")
    with op.batch_alter_table("scraping_crawl_edges") as batch_op:
        batch_op.drop_constraint("ck_crawl_edge_relationship_type", type_="check")
        batch_op.create_check_constraint("ck_crawl_edge_relationship_type", _OLD)
    with op.batch_alter_table("scraping_phase5_work_jobs") as batch_op:
        batch_op.drop_index("ix_phase5_jobs_input_retrieval")
        batch_op.drop_constraint("ck_phase5_job_directory_input", type_="check")
        batch_op.drop_constraint("ck_phase5_job_expansion_cursor", type_="check")
        batch_op.drop_constraint(
            "ck_phase5_job_action_state_fingerprint", type_="check")
        batch_op.drop_constraint(
            "fk_phase5_job_input_document_org_exec", type_="foreignkey")
        batch_op.drop_constraint(
            "fk_phase5_job_input_retrieval_org_exec", type_="foreignkey")
        for column in (
            "continuation_markers_json",
            "requires_browser_interaction", "requires_managed_rendering",
            "expansion_outcome", "parser_state_fingerprint",
            "expansion_parser_version", "last_processed_slice_count",
            "expansion_completed", "entries_completed", "next_entry_ordinal",
            "input_retrieval_method", "input_content_fingerprint",
            "action_state_fingerprint",
            "input_source_document_id", "input_retrieval_result_id",
        ):
            batch_op.drop_column(column)
    with op.batch_alter_table("scraping_phase5_retrieval_results") as batch_op:
        batch_op.drop_constraint("uq_phase5_retrieval_id_org_exec", type_="unique")
