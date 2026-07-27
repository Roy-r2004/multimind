"""Add immutable blueprint snapshot and frozen execution-plan fields.

Revision ID: 026
Revises: 025

New fields are nullable so legacy and historical mission-campaign rows remain valid.
No backfill is performed.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scraping_executions") as batch_op:
        batch_op.add_column(sa.Column("blueprint_snapshot_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("frozen_execution_plan_json", sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column("execution_plan_schema_version", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(sa.Column("execution_plan_hash", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("execution_plan_compiled_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("scraping_executions") as batch_op:
        batch_op.drop_column("execution_plan_compiled_at")
        batch_op.drop_column("execution_plan_hash")
        batch_op.drop_column("execution_plan_schema_version")
        batch_op.drop_column("frozen_execution_plan_json")
        batch_op.drop_column("blueprint_snapshot_json")
