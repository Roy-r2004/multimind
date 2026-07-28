"""Phase 5A directory expansion and retrieval persistence foundation.

Revision ID: 031
Revises: 030
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def _owner_fk(local: str, remote: str, name: str, *, ondelete: str = "RESTRICT"):
    return sa.ForeignKeyConstraint(
        [local, "organization_id", "execution_id"],
        [f"{remote}.id", f"{remote}.organization_id", f"{remote}.execution_id"],
        name=name, ondelete=ondelete,
    )


def upgrade() -> None:
    with op.batch_alter_table("scraping_crawl_edges") as batch_op:
        batch_op.create_unique_constraint(
            "uq_crawl_edge_id_org_exec", ["id", "organization_id", "execution_id"])
    with op.batch_alter_table("scraping_source_documents") as batch_op:
        batch_op.create_unique_constraint(
            "uq_source_document_id_org_exec", ["id", "organization_id", "execution_id"])

    op.create_table(
        "scraping_phase5_work_jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("source_candidate_id", sa.String(36)),
        sa.Column("crawl_node_id", sa.String(36), nullable=False),
        sa.Column("crawl_edge_id", sa.String(36)),
        sa.Column("discovery_query_id", sa.String(36)),
        sa.Column("work_kind", sa.String(40), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text()),
        sa.Column("source_classification", sa.String(64), nullable=False),
        sa.Column("selected_tool", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("claim_token", sa.String(36)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer()),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_category", sa.String(80)),
        sa.Column("last_error_message", sa.String(500)),
        sa.Column("provider_request_id", sa.String(255)),
        sa.Column("provider_result_status", sa.String(80)),
        sa.Column("operational_metadata_json", sa.JSON(), nullable=False, server_default="{}",
                  comment="Sanitized Phase 5 operational allowlist; never public or raw provider data"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        _owner_fk("crawl_node_id", "scraping_crawl_nodes", "fk_phase5_job_node_org_exec", ondelete="CASCADE"),
        _owner_fk("source_candidate_id", "scraping_source_candidates", "fk_phase5_job_candidate_org_exec"),
        _owner_fk("crawl_edge_id", "scraping_crawl_edges", "fk_phase5_job_edge_org_exec"),
        _owner_fk("discovery_query_id", "scraping_source_discovery_queries", "fk_phase5_job_query_org_exec"),
        sa.UniqueConstraint("organization_id", "execution_id", "fingerprint", name="uq_phase5_job_org_exec_fingerprint"),
        sa.UniqueConstraint("id", "organization_id", "execution_id", name="uq_phase5_job_id_org_exec"),
        sa.CheckConstraint("work_kind IN ('directory_expansion','http_retrieval','firecrawl_retrieval','playwright_retrieval')", name="ck_phase5_job_work_kind"),
        sa.CheckConstraint("status IN ('pending','running','succeeded','retry_scheduled','blocked','rejected','failed','cancelled')", name="ck_phase5_job_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_phase5_job_attempt_count"),
        sa.CheckConstraint("max_attempts IS NULL OR max_attempts >= 1", name="ck_phase5_job_max_attempts"),
        sa.CheckConstraint("length(trim(fingerprint)) = 64", name="ck_phase5_job_fingerprint_len"),
        sa.CheckConstraint("lease_expires_at IS NULL OR claimed_at IS NULL OR lease_expires_at > claimed_at", name="ck_phase5_job_lease_after_claim"),
        sa.CheckConstraint(
            "(work_kind = 'directory_expansion' AND selected_tool = 'directory_expansion') OR "
            "(work_kind = 'http_retrieval' AND selected_tool = 'http') OR "
            "(work_kind = 'firecrawl_retrieval' AND selected_tool = 'firecrawl') OR "
            "(work_kind = 'playwright_retrieval' AND selected_tool = 'playwright')",
            name="ck_phase5_job_kind_tool"),
        sa.CheckConstraint(
            "(status = 'running' AND claim_token IS NOT NULL AND claimed_at IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'running' AND claim_token IS NULL AND claimed_at IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_phase5_job_claim_state"),
        sa.CheckConstraint(
            "(canonical_url IS NULL AND status = 'rejected' AND "
            "last_error_category IS NOT NULL) OR canonical_url IS NOT NULL",
            name="ck_phase5_job_unsafe_terminal"),
    )
    op.create_index("ix_phase5_jobs_pending_claim", "scraping_phase5_work_jobs",
                    ["organization_id", "execution_id", "status", "next_retry_at", "requested_at"])
    op.create_index("ix_phase5_jobs_retry_schedule", "scraping_phase5_work_jobs",
                    ["status", "next_retry_at"])
    op.create_index("ix_phase5_jobs_running_lease", "scraping_phase5_work_jobs",
                    ["status", "lease_expires_at"])

    op.create_table(
        "scraping_phase5_retrieval_results",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("work_job_id", sa.String(36), nullable=False),
        sa.Column("requested_url", sa.Text(), nullable=False),
        sa.Column("final_url", sa.Text()),
        sa.Column("http_status", sa.Integer()),
        sa.Column("content_type", sa.String(255)),
        sa.Column("content_length", sa.Integer()),
        sa.Column("response_fingerprint", sa.String(64)),
        sa.Column("result_fingerprint", sa.String(64), nullable=False),
        sa.Column("resource_role", sa.String(64), nullable=False),
        sa.Column("result_ordinal", sa.Integer(), nullable=False),
        sa.Column("retrieval_method", sa.String(64), nullable=False),
        sa.Column("cache_status", sa.String(40)),
        sa.Column("redirect_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_storage_reference", sa.String(1000)),
        sa.Column("source_document_id", sa.String(36)),
        sa.Column("parent_crawl_edge_id", sa.String(36)),
        sa.Column("provider_request_id", sa.String(255)),
        sa.Column("provider_result_status", sa.String(80)),
        sa.Column("failure_category", sa.String(80)),
        sa.Column("operational_metadata_json", sa.JSON(), nullable=False, server_default="{}",
                  comment="Sanitized Phase 5 operational allowlist; never public or raw provider data"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        _owner_fk("work_job_id", "scraping_phase5_work_jobs", "fk_phase5_retrieval_job_org_exec", ondelete="CASCADE"),
        _owner_fk("source_document_id", "scraping_source_documents", "fk_phase5_retrieval_document_org_exec"),
        _owner_fk("parent_crawl_edge_id", "scraping_crawl_edges", "fk_phase5_retrieval_edge_org_exec"),
        sa.UniqueConstraint("organization_id", "execution_id", "work_job_id",
                            "result_fingerprint",
                            name="uq_phase5_retrieval_resource"),
        sa.CheckConstraint("redirect_count >= 0", name="ck_phase5_retrieval_redirect_count"),
        sa.CheckConstraint("content_length IS NULL OR content_length >= 0", name="ck_phase5_retrieval_content_length"),
        sa.CheckConstraint(
            "retrieval_method IN ('http_retrieval','firecrawl_retrieval','playwright_retrieval')",
            name="ck_phase5_retrieval_method"),
        sa.CheckConstraint("result_ordinal >= 0", name="ck_phase5_retrieval_result_ordinal"),
        sa.CheckConstraint("length(trim(result_fingerprint)) = 64",
                           name="ck_phase5_retrieval_result_fingerprint_len"),
    )
    op.create_index("ix_phase5_retrieval_org_exec", "scraping_phase5_retrieval_results", ["organization_id", "execution_id"])
    op.create_index("ix_phase5_retrieval_response_fingerprint", "scraping_phase5_retrieval_results", ["response_fingerprint"])

    op.create_table(
        "scraping_directory_observations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("work_job_id", sa.String(36), nullable=False),
        sa.Column("observation_fingerprint", sa.String(64), nullable=False),
        sa.Column("displayed_facility_name", sa.String(500)),
        sa.Column("listing_page_url", sa.Text(), nullable=False),
        sa.Column("profile_url", sa.Text()),
        sa.Column("official_website_url", sa.Text()),
        sa.Column("displayed_address", sa.Text()),
        sa.Column("displayed_phone", sa.String(120)),
        sa.Column("displayed_region", sa.String(160)),
        sa.Column("displayed_city", sa.String(160)),
        sa.Column("directory_source", sa.String(255), nullable=False),
        sa.Column("listing_rank", sa.Integer()),
        sa.Column("raw_excerpt", sa.Text()),
        sa.Column("structured_payload_reference", sa.String(1000)),
        sa.Column("parent_directory_node_id", sa.String(36), nullable=False),
        sa.Column("emitted_profile_node_id", sa.String(36)),
        sa.Column("emitted_website_node_id", sa.String(36)),
        sa.Column("extraction_method", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        _owner_fk("work_job_id", "scraping_phase5_work_jobs", "fk_directory_observation_job_org_exec", ondelete="CASCADE"),
        _owner_fk("parent_directory_node_id", "scraping_crawl_nodes", "fk_directory_observation_parent_node_org_exec", ondelete="CASCADE"),
        _owner_fk("emitted_profile_node_id", "scraping_crawl_nodes", "fk_directory_observation_profile_node_org_exec"),
        _owner_fk("emitted_website_node_id", "scraping_crawl_nodes", "fk_directory_observation_website_node_org_exec"),
        sa.UniqueConstraint("organization_id", "execution_id", "observation_fingerprint", name="uq_directory_observation_org_exec_fingerprint"),
        sa.CheckConstraint("listing_rank IS NULL OR listing_rank >= 1", name="ck_directory_observation_rank"),
        sa.CheckConstraint("length(trim(observation_fingerprint)) = 64", name="ck_directory_observation_fingerprint_len"),
    )
    op.create_index("ix_directory_observations_org_exec", "scraping_directory_observations", ["organization_id", "execution_id"])
    op.create_index("ix_directory_observations_parent", "scraping_directory_observations", ["parent_directory_node_id"])


def downgrade() -> None:
    op.drop_index("ix_directory_observations_parent", table_name="scraping_directory_observations")
    op.drop_index("ix_directory_observations_org_exec", table_name="scraping_directory_observations")
    op.drop_table("scraping_directory_observations")
    op.drop_index("ix_phase5_retrieval_response_fingerprint", table_name="scraping_phase5_retrieval_results")
    op.drop_index("ix_phase5_retrieval_org_exec", table_name="scraping_phase5_retrieval_results")
    op.drop_table("scraping_phase5_retrieval_results")
    op.drop_index("ix_phase5_jobs_running_lease", table_name="scraping_phase5_work_jobs")
    op.drop_index("ix_phase5_jobs_retry_schedule", table_name="scraping_phase5_work_jobs")
    op.drop_index("ix_phase5_jobs_pending_claim", table_name="scraping_phase5_work_jobs")
    op.drop_table("scraping_phase5_work_jobs")
    with op.batch_alter_table("scraping_crawl_edges") as batch_op:
        batch_op.drop_constraint("uq_crawl_edge_id_org_exec", type_="unique")
    with op.batch_alter_table("scraping_source_documents") as batch_op:
        batch_op.drop_constraint("uq_source_document_id_org_exec", type_="unique")
