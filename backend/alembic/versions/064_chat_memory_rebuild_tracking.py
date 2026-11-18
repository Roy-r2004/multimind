"""Durable chat-memory rebuild tracking — additive columns.

Revision ID: 064
Revises: 063

Adds two nullable columns to ``chats`` so a full rolling-memory rebuild
(triggered by delete/restore/regenerate invalidating memory) can be done by a
durable, resumable background worker instead of synchronously inside the
HTTP request — see app.services.chat_memory_service and app.chat_memory.worker.

- ``rolling_memory_rebuild_requested_at`` (DateTime): set when
  ``invalidate_memory`` runs; the durable signal that a rebuild is owed,
  independent of ``rolling_memory_through_turn_id`` (which is also NULL for
  an ordinary brand-new chat that never needed a rebuild at all — these two
  states must be distinguishable so a periodic recovery sweep knows which
  chats actually need attention). Cleared only once a rebuild pass finds no
  more compactable turns remaining.
- ``rolling_memory_rebuild_claimed_at`` (DateTime): set atomically by
  whichever worker job claims this chat for processing (mirrors the existing
  ``DebateSession.execution_claimed_at`` single-execution-claim pattern in
  app.services.debate_service), so two worker deliveries for the same chat
  can never run overlapping merge passes and duplicate paid LLM calls.
  Cleared when that job's bounded pass finishes (success or failure) so the
  next job can claim it; a claim older than the configured stale threshold
  is also reclaimable, so a crashed/killed worker never strands a chat.

Both are NULL for every chat that predates this feature, and NULL is also the
normal steady state for a chat that has never needed a rebuild — nothing here
changes the meaning of an existing row's ``rolling_memory``/
``rolling_memory_through_turn_id``, and nothing here touches turns,
model_answers, verdicts, or any Challenge/Lessons table.
"""

import sqlalchemy as sa
from alembic import op

revision = "064"
down_revision = "063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.add_column(
            sa.Column(
                "rolling_memory_rebuild_requested_at", sa.DateTime(timezone=True), nullable=True
            )
        )
        batch.add_column(
            sa.Column(
                "rolling_memory_rebuild_claimed_at", sa.DateTime(timezone=True), nullable=True
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("chats") as batch:
        batch.drop_column("rolling_memory_rebuild_claimed_at")
        batch.drop_column("rolling_memory_rebuild_requested_at")
