"""Persist exact rendered-text locations for selection bookmarks.

Revision ID: 058
Revises: 057
"""

import sqlalchemy as sa
from alembic import op

revision = "058"
down_revision = "057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_verdict_pins", sa.Column("selection_locator", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chat_verdict_pins") as batch:
        batch.drop_column("selection_locator")
