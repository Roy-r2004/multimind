"""Isolated AI Debate Mode persistence schema.

Revision ID: 061
Revises: gosom066_run_discovery_provider

Adds three fully new, additive tables (debate_sessions, debate_challenges,
debate_responses) for an optional peer-review pass that runs strictly after
an existing Council Turn's Verdict is complete. Nothing here alters turns,
model_answers, verdicts, or any Challenge/Lessons table, and no column is
added to any of them.

Also extends the existing PostgreSQL ``usagekind`` native enum with a new
``DEBATE`` label so CostRecord rows for debate LLM calls can reuse the
existing cost ledger (see migrations 056/060 for the precedent of this
ALTER TYPE pattern — SQLite has no native enum type so this is skipped
there; CostRecord.kind uses Enum(UsageKind) without native_enum=False).
"""

import sqlalchemy as sa
from alembic import op

revision = "061"
down_revision = "gosom066_run_discovery_provider"
branch_labels = None
depends_on = None

ENUM_TYPE = "usagekind"
ENUM_LABEL = "DEBATE"
ADD_VALUE_SQL = "ALTER TYPE usagekind ADD VALUE IF NOT EXISTS 'DEBATE'"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        exists = bind.execute(
            sa.text(
                """
                SELECT 1
                FROM pg_type
                WHERE typname = :typname
                  AND typtype = 'e'
                """
            ),
            {"typname": ENUM_TYPE},
        ).scalar()
        if exists:
            with op.get_context().autocommit_block():
                op.execute(sa.text(ADD_VALUE_SQL))

    op.create_table(
        "debate_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("turn_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("model_ids", sa.JSON(), nullable=False),
        sa.Column("max_rounds", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("agreements", sa.JSON(), nullable=True),
        sa.Column("unresolved_disagreements", sa.JSON(), nullable=True),
        sa.Column("corrections", sa.JSON(), nullable=True),
        sa.Column("risks", sa.JSON(), nullable=True),
        sa.Column("final_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["turn_id"], ["turns.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_debate_sessions_org_id", "debate_sessions", ["org_id"])
    op.create_index("ix_debate_sessions_turn_id", "debate_sessions", ["turn_id"])
    op.create_index("ix_debate_sessions_status", "debate_sessions", ["status"])
    op.create_index(
        "uq_debate_sessions_active_turn",
        "debate_sessions",
        ["turn_id"],
        unique=True,
        postgresql_where=sa.text("status in ('pending', 'running')"),
        sqlite_where=sa.text("status in ('pending', 'running')"),
    )

    op.create_table(
        "debate_challenges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("debate_session_id", sa.String(length=36), nullable=False),
        sa.Column("author_model_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("challenges", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["debate_session_id"], ["debate_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "debate_session_id", "author_model_id", name="uq_debate_challenge_session_author"
        ),
    )
    op.create_index("ix_debate_challenges_org_id", "debate_challenges", ["org_id"])
    op.create_index("ix_debate_challenges_session_id", "debate_challenges", ["debate_session_id"])

    op.create_table(
        "debate_responses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("debate_session_id", sa.String(length=36), nullable=False),
        sa.Column("author_model_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("responses", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("tokens_input", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["debate_session_id"], ["debate_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "debate_session_id", "author_model_id", name="uq_debate_response_session_author"
        ),
    )
    op.create_index("ix_debate_responses_org_id", "debate_responses", ["org_id"])
    op.create_index("ix_debate_responses_session_id", "debate_responses", ["debate_session_id"])


def downgrade() -> None:
    op.drop_index("ix_debate_responses_session_id", table_name="debate_responses")
    op.drop_index("ix_debate_responses_org_id", table_name="debate_responses")
    op.drop_table("debate_responses")

    op.drop_index("ix_debate_challenges_session_id", table_name="debate_challenges")
    op.drop_index("ix_debate_challenges_org_id", table_name="debate_challenges")
    op.drop_table("debate_challenges")

    op.drop_index("uq_debate_sessions_active_turn", table_name="debate_sessions")
    op.drop_index("ix_debate_sessions_status", table_name="debate_sessions")
    op.drop_index("ix_debate_sessions_turn_id", table_name="debate_sessions")
    op.drop_index("ix_debate_sessions_org_id", table_name="debate_sessions")
    op.drop_table("debate_sessions")

    # PostgreSQL cannot drop a single enum label without recreating the type
    # and rewriting dependent columns (see 056/060's same no-op). Leave DEBATE
    # in place; it is inert if no code ever writes CostRecord.kind=DEBATE.
