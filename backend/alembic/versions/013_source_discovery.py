"""Add real source discovery persistence

Revision ID: 013
Revises: 012
Create Date: 2026-07-17
"""

from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
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
        "scraping_source_discovery_queries",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=True),
        sa.Column("coverage_cell_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("country_name", sa.String(length=120), nullable=False),
        sa.Column("region_code", sa.String(length=32), nullable=True),
        sa.Column("region_name", sa.String(length=160), nullable=False),
        sa.Column("language_code", sa.String(length=16), nullable=False),
        sa.Column("language_name", sa.String(length=120), nullable=False),
        sa.Column("source_category", sa.String(length=120), nullable=False),
        sa.Column("query_text", sa.String(length=512), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("metadata_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["coverage_cell_id"], ["scraping_coverage_cells.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["scraping_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("length(trim(query_text)) > 0", name="ck_source_discovery_query_not_blank"),
        sa.CheckConstraint("result_count >= 0", name="ck_source_discovery_query_result_count"),
    )
    op.create_index("ix_source_discovery_queries_org", "scraping_source_discovery_queries", ["organization_id"])
    op.create_index("ix_source_discovery_queries_execution", "scraping_source_discovery_queries", ["execution_id"])
    op.create_index("ix_source_discovery_queries_coverage", "scraping_source_discovery_queries", ["coverage_cell_id"])
    op.create_index("ix_source_discovery_queries_task", "scraping_source_discovery_queries", ["task_id"])
    op.create_index("ix_source_discovery_queries_provider", "scraping_source_discovery_queries", ["provider"])
    op.create_index("ix_source_discovery_queries_status", "scraping_source_discovery_queries", ["status"])
    op.create_index(
        "ix_source_discovery_queries_context",
        "scraping_source_discovery_queries",
        ["organization_id", "execution_id", "coverage_cell_id", "provider", "source_category"],
    )

    op.create_table(
        "scraping_source_candidates",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=True),
        sa.Column("coverage_cell_id", sa.String(length=36), nullable=True),
        sa.Column("discovery_query_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_result_id", sa.String(length=255), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("canonical_url", sa.String(length=2048), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("snippet", sa.String(length=1000), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("country_name", sa.String(length=120), nullable=False),
        sa.Column("region_code", sa.String(length=32), nullable=True),
        sa.Column("region_name", sa.String(length=160), nullable=False),
        sa.Column("language_code", sa.String(length=16), nullable=False),
        sa.Column("language_name", sa.String(length=120), nullable=False),
        sa.Column("source_category", sa.String(length=120), nullable=False),
        sa.Column("initial_relevance_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("initial_trust_tier", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["coverage_cell_id"], ["scraping_coverage_cells.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["discovery_query_id"], ["scraping_source_discovery_queries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "discovery_query_id", "canonical_url", name="uq_source_candidate_query_url"),
        sa.CheckConstraint("rank >= 1", name="ck_source_candidate_rank"),
        sa.CheckConstraint(
            "initial_relevance_score >= 0 AND initial_relevance_score <= 1",
            name="ck_source_candidate_relevance_score",
        ),
        sa.CheckConstraint(
            "(lower(url) LIKE 'http://%' OR lower(url) LIKE 'https://%') AND "
            "(lower(canonical_url) LIKE 'http://%' OR lower(canonical_url) LIKE 'https://%')",
            name="ck_source_candidate_http_urls",
        ),
    )
    op.create_index("ix_source_candidates_org", "scraping_source_candidates", ["organization_id"])
    op.create_index("ix_source_candidates_execution", "scraping_source_candidates", ["execution_id"])
    op.create_index("ix_source_candidates_coverage", "scraping_source_candidates", ["coverage_cell_id"])
    op.create_index("ix_source_candidates_query", "scraping_source_candidates", ["discovery_query_id"])
    op.create_index("ix_source_candidates_provider", "scraping_source_candidates", ["provider"])
    op.create_index("ix_source_candidates_domain", "scraping_source_candidates", ["domain"])
    op.create_index("ix_source_candidates_status", "scraping_source_candidates", ["status"])
    op.create_index("ix_source_candidates_canonical_url", "scraping_source_candidates", ["canonical_url"])
    op.create_index(
        "ix_source_candidates_context_url",
        "scraping_source_candidates",
        ["organization_id", "execution_id", "coverage_cell_id", "canonical_url"],
    )


def downgrade() -> None:
    op.drop_index("ix_source_candidates_context_url", table_name="scraping_source_candidates")
    op.drop_index("ix_source_candidates_canonical_url", table_name="scraping_source_candidates")
    op.drop_index("ix_source_candidates_status", table_name="scraping_source_candidates")
    op.drop_index("ix_source_candidates_domain", table_name="scraping_source_candidates")
    op.drop_index("ix_source_candidates_provider", table_name="scraping_source_candidates")
    op.drop_index("ix_source_candidates_query", table_name="scraping_source_candidates")
    op.drop_index("ix_source_candidates_coverage", table_name="scraping_source_candidates")
    op.drop_index("ix_source_candidates_execution", table_name="scraping_source_candidates")
    op.drop_index("ix_source_candidates_org", table_name="scraping_source_candidates")
    op.drop_table("scraping_source_candidates")

    op.drop_index("ix_source_discovery_queries_context", table_name="scraping_source_discovery_queries")
    op.drop_index("ix_source_discovery_queries_status", table_name="scraping_source_discovery_queries")
    op.drop_index("ix_source_discovery_queries_provider", table_name="scraping_source_discovery_queries")
    op.drop_index("ix_source_discovery_queries_task", table_name="scraping_source_discovery_queries")
    op.drop_index("ix_source_discovery_queries_coverage", table_name="scraping_source_discovery_queries")
    op.drop_index("ix_source_discovery_queries_execution", table_name="scraping_source_discovery_queries")
    op.drop_index("ix_source_discovery_queries_org", table_name="scraping_source_discovery_queries")
    op.drop_table("scraping_source_discovery_queries")
