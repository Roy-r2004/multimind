"""Add asynchronous blueprint generation lifecycle metadata.

Revision ID: 022
Revises: 021
"""

import sqlalchemy as sa

from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scraping_blueprints") as batch_op:
        batch_op.add_column(sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("provider_operation_id", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("provider_execution_metadata", sa.JSON(), nullable=True))
        batch_op.create_index("ix_scraping_blueprints_provider_operation", ["provider_operation_id"])


def downgrade() -> None:
    with op.batch_alter_table("scraping_blueprints") as batch_op:
        batch_op.drop_index("ix_scraping_blueprints_provider_operation")
        batch_op.drop_column("provider_execution_metadata")
        batch_op.drop_column("provider_operation_id")
        batch_op.drop_column("failed_at")
