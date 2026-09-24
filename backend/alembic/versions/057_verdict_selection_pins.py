"""Extend Verdict pins with selected content.

Revision ID: 057
Revises: 056
"""

import sqlalchemy as sa
from alembic import op

revision = "057"
down_revision = "056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_verdict_pins") as batch:
        batch.add_column(sa.Column("pin_type", sa.String(16), nullable=False, server_default="verdict"))
        batch.add_column(sa.Column("selected_text", sa.Text(), nullable=True))
        batch.add_column(sa.Column("selected_html", sa.Text(), nullable=True))
        batch.drop_constraint("uq_chat_verdict_pin", type_="unique")
        batch.create_check_constraint("ck_chat_verdict_pin_type", "pin_type IN ('verdict', 'selection')")


def downgrade() -> None:
    # Selection pins have no representation in the previous schema.
    op.execute(sa.text("DELETE FROM chat_verdict_pins WHERE pin_type = 'selection'"))
    with op.batch_alter_table("chat_verdict_pins") as batch:
        batch.drop_constraint("ck_chat_verdict_pin_type", type_="check")
        batch.drop_column("selected_html")
        batch.drop_column("selected_text")
        batch.drop_column("pin_type")
        batch.create_unique_constraint("uq_chat_verdict_pin", ["chat_id", "verdict_id"])
