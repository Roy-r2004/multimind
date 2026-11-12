"""Debate Mode AI-synthesized conclusion — additive columns.

Revision ID: 063
Revises: 062

Adds three nullable columns to ``debate_sessions`` for the new optional
synthesis stage (one extra LLM call after Round 2/3 complete, see
app.llm.debate_orchestrator / app.llm.debate_contracts.DebateConclusion):

- ``conclusion`` (JSON): the synthesized recommendation/why/what-changed/
  risks/next-steps, plus its own token/cost usage, as one blob.
- ``conclusion_status`` (VARCHAR, matches the existing non-native-enum
  status columns on this table): completed / failed / skipped.
- ``conclusion_error_message`` (TEXT): set only when conclusion_status is
  failed.

All three are NULL for every debate session that predates this feature —
nothing here changes the meaning of an existing row, and nothing here
touches turns, model_answers, verdicts, or any Challenge/Lessons table.
"""

import sqlalchemy as sa
from alembic import op

revision = "063"
down_revision = "062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("debate_sessions", sa.Column("conclusion", sa.JSON(), nullable=True))
    op.add_column(
        "debate_sessions", sa.Column("conclusion_status", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "debate_sessions", sa.Column("conclusion_error_message", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("debate_sessions", "conclusion_error_message")
    op.drop_column("debate_sessions", "conclusion_status")
    op.drop_column("debate_sessions", "conclusion")
