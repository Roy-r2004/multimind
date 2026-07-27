"""Add typed clarification and resolved-plan fields for Step 2.

Revision ID: 027
Revises: 026

All new columns are nullable so historical and Step 1-only rows remain valid.
No backfill is performed.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scraping_executions") as batch_op:
        batch_op.add_column(sa.Column("clarification_status", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("clarification_schema_version", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(sa.Column("clarification_requests_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("clarification_decisions_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("resolved_execution_plan_json", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("resolved_execution_plan_hash", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("clarification_model_slug_snapshot", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(sa.Column("clarification_attempt_count", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("clarification_started_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("clarification_completed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("clarification_error_code", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("clarification_provider_operation_id", sa.String(length=255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("clarification_provider_metadata_json", sa.JSON(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("scraping_executions") as batch_op:
        batch_op.drop_column("clarification_provider_metadata_json")
        batch_op.drop_column("clarification_provider_operation_id")
        batch_op.drop_column("clarification_error_code")
        batch_op.drop_column("clarification_completed_at")
        batch_op.drop_column("clarification_started_at")
        batch_op.drop_column("clarification_attempt_count")
        batch_op.drop_column("clarification_model_slug_snapshot")
        batch_op.drop_column("resolved_execution_plan_hash")
        batch_op.drop_column("resolved_execution_plan_json")
        batch_op.drop_column("clarification_decisions_json")
        batch_op.drop_column("clarification_requests_json")
        batch_op.drop_column("clarification_schema_version")
        batch_op.drop_column("clarification_status")
