"""Add pinned verdict on chats

Revision ID: 018
Revises: 017
Create Date: 2026-07-24
"""

from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.add_column(sa.Column("pinned_verdict_id", sa.String(length=36), nullable=True))
        batch.create_foreign_key(
            "fk_chats_pinned_verdict_id",
            "verdicts",
            ["pinned_verdict_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.drop_constraint("fk_chats_pinned_verdict_id", type_="foreignkey")
        batch.drop_column("pinned_verdict_id")
