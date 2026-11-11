"""Debate Mode execution lifecycle — Phase 4 additive corrections.

Revision ID: 062
Revises: 061

Three additive, backward-compatible changes to the Phase 1 debate schema,
all required for correctness (not convenience), so this migration is not
optional:

1. ``debate_sessions.unchallenged_model_ids`` + ``uncertainties`` — Phase 3's
   findings reducer conflated "nobody challenged this model" with "the panel
   agreed," and never persisted uncertainties at all. Phase 4 fixes the
   reducer (see app/llm/debate_orchestrator.py); this column is where the
   corrected, distinctly-named data lands. ``agreements`` is left in place,
   still nullable, reserved for a future explicit-agreement contract.
2. ``debate_sessions.execution_claimed_at`` — an atomic single-execution
   claim so duplicate ARQ job delivery (or a racing inline task) can never
   run the same debate session's engine twice concurrently.
3. ``debate_challenges``/``debate_responses`` token/cost columns become
   nullable — NULL now means "unknown usage" (the call failed before the
   provider returned anything), which Phase 3's NOT NULL + default-0 columns
   could not distinguish from "the provider genuinely reported zero cost."

No existing row's meaning changes: all new/loosened columns default to NULL,
and nothing here touches turns, model_answers, verdicts, or any
Challenge/Lessons table.
"""

import sqlalchemy as sa
from alembic import op

revision = "062"
down_revision = "061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("debate_sessions", sa.Column("uncertainties", sa.JSON(), nullable=True))
    op.add_column("debate_sessions", sa.Column("unchallenged_model_ids", sa.JSON(), nullable=True))
    op.add_column(
        "debate_sessions", sa.Column("execution_claimed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_debate_sessions_status_started_at", "debate_sessions", ["status", "started_at"]
    )

    with op.batch_alter_table("debate_challenges") as batch:
        batch.alter_column(
            "tokens_input", existing_type=sa.Integer(), nullable=True, server_default=None
        )
        batch.alter_column(
            "tokens_output", existing_type=sa.Integer(), nullable=True, server_default=None
        )
        batch.alter_column("cost_usd", existing_type=sa.Float(), nullable=True, server_default=None)

    with op.batch_alter_table("debate_responses") as batch:
        batch.alter_column(
            "tokens_input", existing_type=sa.Integer(), nullable=True, server_default=None
        )
        batch.alter_column(
            "tokens_output", existing_type=sa.Integer(), nullable=True, server_default=None
        )
        batch.alter_column("cost_usd", existing_type=sa.Float(), nullable=True, server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("debate_responses") as batch:
        batch.alter_column(
            "cost_usd", existing_type=sa.Float(), nullable=False, server_default="0"
        )
        batch.alter_column(
            "tokens_output", existing_type=sa.Integer(), nullable=False, server_default="0"
        )
        batch.alter_column(
            "tokens_input", existing_type=sa.Integer(), nullable=False, server_default="0"
        )

    with op.batch_alter_table("debate_challenges") as batch:
        batch.alter_column(
            "cost_usd", existing_type=sa.Float(), nullable=False, server_default="0"
        )
        batch.alter_column(
            "tokens_output", existing_type=sa.Integer(), nullable=False, server_default="0"
        )
        batch.alter_column(
            "tokens_input", existing_type=sa.Integer(), nullable=False, server_default="0"
        )

    op.drop_index("ix_debate_sessions_status_started_at", table_name="debate_sessions")
    op.drop_column("debate_sessions", "execution_claimed_at")
    op.drop_column("debate_sessions", "unchallenged_model_ids")
    op.drop_column("debate_sessions", "uncertainties")
