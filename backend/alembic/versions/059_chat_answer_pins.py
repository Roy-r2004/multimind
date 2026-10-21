"""Pin single-AI answers without creating a Verdict.

Single-AI turns never persist a Verdict, and chat_verdict_pins.verdict_id is a
required foreign key. A separate table keeps Council verdict pins unchanged
while answer pins stay tied to the chat and model answer (and therefore the turn).

Revision ID: 059
Revises: 058
"""

import sqlalchemy as sa
from alembic import op

revision = "059"
down_revision = "058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_answer_pins",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("chat_id", sa.String(length=36), nullable=False),
        sa.Column("model_answer_id", sa.String(length=36), nullable=False),
        sa.Column(
            "pin_type",
            sa.String(length=16),
            nullable=False,
            server_default="answer",
        ),
        sa.Column("selected_text", sa.Text(), nullable=True),
        sa.Column("selected_html", sa.Text(), nullable=True),
        sa.Column("selection_locator", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "pin_type IN ('answer', 'selection')",
            name="ck_chat_answer_pin_type",
        ),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_answer_id"], ["model_answers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_answer_pins_chat_id", "chat_answer_pins", ["chat_id"])
    op.create_index(
        "ix_chat_answer_pins_model_answer_id",
        "chat_answer_pins",
        ["model_answer_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_answer_pins_model_answer_id", table_name="chat_answer_pins")
    op.drop_index("ix_chat_answer_pins_chat_id", table_name="chat_answer_pins")
    op.drop_table("chat_answer_pins")
