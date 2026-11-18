"""Protected critical-facts ledger — new table, plus one Turn column.

Revision ID: 065
Revises: 064

Adds ``chat_critical_facts``: a small, separately-protected ledger of
explicitly-grounded facts/decisions/requirements/preferences/open-tasks/
corrections/cancellations, extracted alongside the existing rolling-memory
summarizer call. It exists specifically because the free-text rolling
summary (``Chat.rolling_memory``) was measured, via a real GPT-4.1
evaluation, to silently lose an unguessable planted fact 100% of the time
beyond 20 exchanges — this table gives important facts a storage lifecycle
that is never subject to further lossy re-summarization once persisted.

Design notes (see the Phase 2 architecture review in the accompanying
conversation for the full reasoning):

- ``fact_slot_id`` groups facts about "the same logical topic" within one
  chat. There is deliberately NO stored "superseded" status or
  superseded-by pointer: "the current fact for a slot" is a read-time
  computation (latest fact in the slot whose ``source_turn_id`` points to a
  non-soft-deleted ``Turn`` and whose own ``status`` is not ``retracted``).
  This is what makes deletion/restoration of a source turn correctly
  resurface or re-hide a fact automatically, with no bespoke invalidation
  code — it reuses the exact same ``turns.deleted_at`` soft-delete pattern
  the rest of chat memory already relies on.
- ``category`` includes ``cancellation``: an explicit "no current value for
  this slot" marker, distinct from deleting a correction (which lets an
  older value resurface) — a cancellation is just a normal row in the
  slot's timeline that happens to assert "nothing is required here".
- ``source_span`` is the verbatim quoted substring the extraction step
  claims supports this fact; application code independently verifies this
  against the turn's actual stored text before ever persisting a row — see
  ``chat_memory_service.py``'s validation logic. This table makes no
  database-level guarantee of that; it is enforced in code at write time.
- The unique constraint on (chat_id, fact_slot_id, source_turn_id) is the
  idempotency guard for the backfill/recovery path (Turn.facts_extraction_status
  below): retrying extraction for a turn that already partially succeeded
  cannot create duplicate rows.

``Turn.facts_extraction_status`` (nullable: NULL | 'ok' | 'failed') tracks
extraction outcome independently of the rolling-memory watermark, so a
successful memory merge whose fact extraction failed is explicitly
recorded and recoverable later without ever re-running the already-succeeded
memory merge.

Both are additive and NULL/absent for every chat/turn that predates this
feature — nothing here changes the meaning of an existing row.
"""

import sqlalchemy as sa
from alembic import op

revision = "065"
down_revision = "064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_critical_facts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("chat_id", sa.String(length=36), sa.ForeignKey("chats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("org_id", sa.String(length=36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fact_slot_id", sa.String(length=128), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("speaker", sa.String(length=16), nullable=False),
        sa.Column("source_turn_id", sa.String(length=36), sa.ForeignKey("turns.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_span", sa.Text(), nullable=False),
        sa.Column("grounding", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="normal"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "chat_id", "fact_slot_id", "source_turn_id", name="uq_chat_critical_facts_chat_slot_turn"
        ),
    )
    op.create_index(
        "ix_chat_critical_facts_chat_slot_status",
        "chat_critical_facts",
        ["chat_id", "fact_slot_id", "status"],
    )
    op.create_index(
        "ix_chat_critical_facts_source_turn",
        "chat_critical_facts",
        ["source_turn_id"],
    )

    with op.batch_alter_table("turns") as batch:
        batch.add_column(sa.Column("facts_extraction_status", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("turns") as batch:
        batch.drop_column("facts_extraction_status")
    op.drop_index("ix_chat_critical_facts_source_turn", table_name="chat_critical_facts")
    op.drop_index("ix_chat_critical_facts_chat_slot_status", table_name="chat_critical_facts")
    op.drop_table("chat_critical_facts")
