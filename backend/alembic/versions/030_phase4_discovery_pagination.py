"""Phase 4 Slice 7: restart-safe Serper pagination columns.

Revision ID: 030
Revises: 029

Adds page-cursor fields on scraping_source_discovery_queries so one Step 3B
query job can resume mid-pagination after crash/restart. Optional
provider_page_number on candidates for within-page rank provenance.

Existing rows:
- succeeded → pagination_completed=true (legacy single-page completion)
- all others → next_page_number=1, pages_completed=0, pagination_completed=false
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scraping_source_discovery_queries") as batch_op:
        batch_op.add_column(
            sa.Column(
                "next_page_number",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )
        batch_op.add_column(
            sa.Column(
                "pages_completed",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "pagination_completed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
        batch_op.add_column(
            sa.Column("last_page_result_count", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_page_fingerprint", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "pagination_completed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch_op.create_check_constraint(
            "ck_source_discovery_query_next_page_number",
            "next_page_number >= 1",
        )
        batch_op.create_check_constraint(
            "ck_source_discovery_query_pages_completed",
            "pages_completed >= 0",
        )
        batch_op.create_check_constraint(
            "ck_source_discovery_query_last_page_result_count",
            "last_page_result_count IS NULL OR last_page_result_count >= 0",
        )
        batch_op.create_check_constraint(
            "ck_source_discovery_query_last_page_fingerprint_len",
            "last_page_fingerprint IS NULL OR length(trim(last_page_fingerprint)) = 64",
        )

    # Surviving succeeded jobs completed under single-page semantics.
    op.execute(
        sa.text(
            """
            UPDATE scraping_source_discovery_queries
            SET pagination_completed = true,
                pagination_completed_at = COALESCE(completed_at, pagination_completed_at)
            WHERE status = 'succeeded'
            """
        )
    )

    with op.batch_alter_table("scraping_source_candidates") as batch_op:
        batch_op.add_column(
            sa.Column("provider_page_number", sa.Integer(), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_source_candidate_provider_page_number",
            "provider_page_number IS NULL OR provider_page_number >= 1",
        )


def downgrade() -> None:
    with op.batch_alter_table("scraping_source_candidates") as batch_op:
        batch_op.drop_constraint(
            "ck_source_candidate_provider_page_number", type_="check"
        )
        batch_op.drop_column("provider_page_number")

    with op.batch_alter_table("scraping_source_discovery_queries") as batch_op:
        batch_op.drop_constraint(
            "ck_source_discovery_query_last_page_fingerprint_len", type_="check"
        )
        batch_op.drop_constraint(
            "ck_source_discovery_query_last_page_result_count", type_="check"
        )
        batch_op.drop_constraint(
            "ck_source_discovery_query_pages_completed", type_="check"
        )
        batch_op.drop_constraint(
            "ck_source_discovery_query_next_page_number", type_="check"
        )
        batch_op.drop_column("pagination_completed_at")
        batch_op.drop_column("last_page_fingerprint")
        batch_op.drop_column("last_page_result_count")
        batch_op.drop_column("pagination_completed")
        batch_op.drop_column("pages_completed")
        batch_op.drop_column("next_page_number")
