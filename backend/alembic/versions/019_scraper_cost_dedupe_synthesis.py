"""Add scraper cost caps, usage records, synthesis, and merge audit fields

Revision ID: 019
Revises: 018
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "scraping_default_run_cap_usd",
            sa.Numeric(10, 4),
            server_default="10.0000",
            nullable=False,
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "scraping_blueprint_approval_required",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.add_column("scraping_executions", sa.Column("max_cost_usd", sa.Numeric(10, 4), nullable=True))
    op.add_column(
        "scraping_executions",
        sa.Column("estimated_cost_usd", sa.Numeric(10, 4), server_default="0.0000", nullable=False),
    )
    op.add_column(
        "scraping_executions",
        sa.Column("actual_cost_usd", sa.Numeric(10, 4), server_default="0.0000", nullable=False),
    )
    op.add_column(
        "scraping_executions",
        sa.Column("budget_status", sa.String(length=40), server_default="within_budget", nullable=False),
    )
    op.add_column(
        "scraping_executions",
        sa.Column("budget_limit_reached_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "scraping_executions",
        sa.Column("usage_breakdown_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.add_column("scraping_executions", sa.Column("result_summary_json", sa.JSON(), nullable=True))
    op.add_column("scraping_executions", sa.Column("result_synthesis", sa.Text(), nullable=True))
    op.create_index("ix_scraping_executions_budget_status", "scraping_executions", ["budget_status"])

    with op.batch_alter_table("rehabilitation_facilities") as batch_op:
        batch_op.add_column(
            sa.Column("merged_into_facility_id", sa.String(length=36), nullable=True)
        )
        batch_op.add_column(
            sa.Column("merge_metadata_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False)
        )
        batch_op.create_foreign_key(
            "fk_rehab_facilities_merged_into_facility_id",
            "rehabilitation_facilities",
            ["merged_into_facility_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_rehab_facilities_merged_into",
        "rehabilitation_facilities",
        ["merged_into_facility_id"],
    )

    op.create_table(
        "scraping_usage_cost_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=256), nullable=True),
        sa.Column("operation_type", sa.String(length=80), nullable=False),
        sa.Column("request_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(10, 6), server_default="0.000000", nullable=False),
        sa.Column("actual_cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("currency", sa.String(length=3), server_default="USD", nullable=False),
        sa.Column("usage_source", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=180), nullable=False),
        sa.Column("metadata_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_scraping_usage_cost_idempotency"),
    )
    op.create_index("ix_scraping_usage_cost_org", "scraping_usage_cost_records", ["organization_id"])
    op.create_index("ix_scraping_usage_cost_execution", "scraping_usage_cost_records", ["execution_id"])
    op.create_index("ix_scraping_usage_cost_stage", "scraping_usage_cost_records", ["stage"])
    op.create_index("ix_scraping_usage_cost_provider", "scraping_usage_cost_records", ["provider"])
    op.create_index("ix_scraping_usage_cost_created", "scraping_usage_cost_records", ["created_at"])


def downgrade() -> None:
    for name in [
        "ix_scraping_usage_cost_created",
        "ix_scraping_usage_cost_provider",
        "ix_scraping_usage_cost_stage",
        "ix_scraping_usage_cost_execution",
        "ix_scraping_usage_cost_org",
    ]:
        op.drop_index(name, table_name="scraping_usage_cost_records")
    op.drop_table("scraping_usage_cost_records")

    op.drop_index("ix_rehab_facilities_merged_into", table_name="rehabilitation_facilities")
    with op.batch_alter_table("rehabilitation_facilities") as batch_op:
        batch_op.drop_constraint("fk_rehab_facilities_merged_into_facility_id", type_="foreignkey")
        batch_op.drop_column("merge_metadata_json")
        batch_op.drop_column("merged_into_facility_id")

    op.drop_index("ix_scraping_executions_budget_status", table_name="scraping_executions")
    op.drop_column("scraping_executions", "result_synthesis")
    op.drop_column("scraping_executions", "result_summary_json")
    op.drop_column("scraping_executions", "usage_breakdown_json")
    op.drop_column("scraping_executions", "budget_limit_reached_at")
    op.drop_column("scraping_executions", "budget_status")
    op.drop_column("scraping_executions", "actual_cost_usd")
    op.drop_column("scraping_executions", "estimated_cost_usd")
    op.drop_column("scraping_executions", "max_cost_usd")
    op.drop_column("organizations", "scraping_blueprint_approval_required")
    op.drop_column("organizations", "scraping_default_run_cap_usd")
