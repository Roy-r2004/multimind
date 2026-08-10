"""Add chats.model_set_id for next-message council selection.

Revision ID: 046
Revises: 045
Create Date: 2026-08-09

Nullable slug of the model set used for the next turn in this chat.
Historical turns keep their own model_set_id snapshots.
"""

from alembic import op
import sqlalchemy as sa

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.add_column(sa.Column("model_set_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.drop_column("model_set_id")
