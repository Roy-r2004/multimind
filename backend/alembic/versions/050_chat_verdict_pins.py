"""Replace the scalar chat pin with independent verdict pin records.

Revision ID: 050
Revises: 049
Create Date: 2026-08-27
"""

import uuid

import sqlalchemy as sa

from alembic import op

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_verdict_pins",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("chat_id", sa.String(length=36), nullable=False),
        sa.Column("verdict_id", sa.String(length=36), nullable=False),
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
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["verdict_id"], ["verdicts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id", "verdict_id", name="uq_chat_verdict_pin"),
    )
    op.create_index("ix_chat_verdict_pins_chat_id", "chat_verdict_pins", ["chat_id"])
    op.create_index("ix_chat_verdict_pins_verdict_id", "chat_verdict_pins", ["verdict_id"])

    connection = op.get_bind()
    chats = sa.table(
        "chats",
        sa.column("id", sa.String(length=36)),
        sa.column("pinned_verdict_id", sa.String(length=36)),
    )
    pins = sa.table(
        "chat_verdict_pins",
        sa.column("id", sa.String(length=36)),
        sa.column("chat_id", sa.String(length=36)),
        sa.column("verdict_id", sa.String(length=36)),
    )
    rows = connection.execute(
        sa.select(chats.c.id, chats.c.pinned_verdict_id).where(
            chats.c.pinned_verdict_id.is_not(None)
        )
    )
    legacy_pins = [
        {"id": str(uuid.uuid4()), "chat_id": chat_id, "verdict_id": verdict_id}
        for chat_id, verdict_id in rows
    ]
    if legacy_pins:
        connection.execute(pins.insert(), legacy_pins)

    with op.batch_alter_table("chats") as batch:
        batch.drop_constraint("fk_chats_pinned_verdict_id", type_="foreignkey")
        batch.drop_column("pinned_verdict_id")


def downgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.add_column(
            sa.Column("pinned_verdict_id", sa.String(length=36), nullable=True)
        )
        batch.create_foreign_key(
            "fk_chats_pinned_verdict_id",
            "verdicts",
            ["pinned_verdict_id"],
            ["id"],
            ondelete="SET NULL",
        )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE chats SET pinned_verdict_id = ("
            "SELECT verdict_id FROM chat_verdict_pins "
            "WHERE chat_verdict_pins.chat_id = chats.id "
            "ORDER BY created_at, id LIMIT 1)"
        )
    )
    op.drop_index("ix_chat_verdict_pins_verdict_id", table_name="chat_verdict_pins")
    op.drop_index("ix_chat_verdict_pins_chat_id", table_name="chat_verdict_pins")
    op.drop_table("chat_verdict_pins")
