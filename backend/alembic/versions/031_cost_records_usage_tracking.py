"""Extend cost_records for unified AI usage tracking and historical attribution.

Revision ID: 031
Revises: 030
Create Date: 2026-08-01
"""

import sqlalchemy as sa

from alembic import op

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Allow non-chat AI calls (embeddings, scraping, helpers).
    op.alter_column("cost_records", "chat_id", existing_type=sa.String(36), nullable=True)
    op.alter_column("cost_records", "turn_id", existing_type=sa.String(36), nullable=True)

    op.add_column("cost_records", sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("cost_records", sa.Column("provider", sa.String(64), nullable=True))
    op.add_column("cost_records", sa.Column("operation", sa.String(64), nullable=True))
    op.add_column("cost_records", sa.Column("status", sa.String(32), nullable=True))
    op.add_column("cost_records", sa.Column("request_id", sa.String(128), nullable=True))
    op.add_column("cost_records", sa.Column("idempotency_key", sa.String(191), nullable=True))
    op.add_column("cost_records", sa.Column("tokens_reasoning", sa.Integer(), nullable=True))
    op.add_column("cost_records", sa.Column("tokens_cached_input", sa.Integer(), nullable=True))
    op.add_column("cost_records", sa.Column("latency_ms", sa.Integer(), nullable=True))
    op.add_column("cost_records", sa.Column("cost_source", sa.String(32), nullable=True))
    op.add_column("cost_records", sa.Column("error_code", sa.String(64), nullable=True))
    op.add_column(
        "cost_records",
        sa.Column("mission_id", sa.String(36), sa.ForeignKey("scraping_missions.id"), nullable=True),
    )
    op.add_column(
        "cost_records",
        sa.Column(
            "execution_id",
            sa.String(36),
            sa.ForeignKey("scraping_executions.id"),
            nullable=True,
        ),
    )

    # Backfill attribution and defaults without rewriting costs.
    op.execute(
        """
        UPDATE cost_records AS c
        SET user_id = ch.created_by
        FROM chats AS ch
        WHERE c.chat_id = ch.id
          AND c.user_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE cost_records AS c
        SET user_id = vl.user_id
        FROM verdict_lessons AS vl
        WHERE c.user_id IS NULL
          AND c.kind IN ('lesson', 'brain')
          AND (
            (c.turn_id IS NOT NULL AND c.turn_id = vl.turn_id)
            OR (c.chat_id IS NOT NULL AND c.chat_id = vl.chat_id AND vl.user_id IS NOT NULL)
          )
        """
    )
    op.execute(
        """
        UPDATE cost_records
        SET
          provider = COALESCE(provider, 'openrouter'),
          operation = COALESCE(operation, kind),
          status = COALESCE(status, 'succeeded'),
          cost_source = COALESCE(cost_source, 'unknown'),
          idempotency_key = COALESCE(idempotency_key, 'legacy:' || id)
        WHERE provider IS NULL
           OR operation IS NULL
           OR status IS NULL
           OR cost_source IS NULL
           OR idempotency_key IS NULL
        """
    )

    op.create_index("ix_cost_records_user_recorded", "cost_records", ["user_id", "recorded_at"])
    op.create_index("ix_cost_records_org_recorded", "cost_records", ["org_id", "recorded_at"])
    op.create_index("ix_cost_records_kind_recorded", "cost_records", ["kind", "recorded_at"])
    op.create_index("ix_cost_records_status_recorded", "cost_records", ["status", "recorded_at"])
    op.create_index("ix_cost_records_operation", "cost_records", ["operation"])
    op.create_index("ix_cost_records_chat_turn", "cost_records", ["chat_id", "turn_id"])
    op.create_index("ix_cost_records_mission_execution", "cost_records", ["mission_id", "execution_id"])
    op.create_index(
        "uq_cost_records_idempotency_key",
        "cost_records",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_cost_records_idempotency_key", table_name="cost_records")
    op.drop_index("ix_cost_records_mission_execution", table_name="cost_records")
    op.drop_index("ix_cost_records_chat_turn", table_name="cost_records")
    op.drop_index("ix_cost_records_operation", table_name="cost_records")
    op.drop_index("ix_cost_records_status_recorded", table_name="cost_records")
    op.drop_index("ix_cost_records_kind_recorded", table_name="cost_records")
    op.drop_index("ix_cost_records_org_recorded", table_name="cost_records")
    op.drop_index("ix_cost_records_user_recorded", table_name="cost_records")

    op.drop_column("cost_records", "execution_id")
    op.drop_column("cost_records", "mission_id")
    op.drop_column("cost_records", "error_code")
    op.drop_column("cost_records", "cost_source")
    op.drop_column("cost_records", "latency_ms")
    op.drop_column("cost_records", "tokens_cached_input")
    op.drop_column("cost_records", "tokens_reasoning")
    op.drop_column("cost_records", "idempotency_key")
    op.drop_column("cost_records", "request_id")
    op.drop_column("cost_records", "status")
    op.drop_column("cost_records", "operation")
    op.drop_column("cost_records", "provider")
    op.drop_column("cost_records", "user_id")

    # Restore NOT NULL only where values exist (historical chat/turn rows).
    op.execute("UPDATE cost_records SET chat_id = chat_id WHERE chat_id IS NOT NULL")
    # Cannot safely restore NOT NULL if any nulls remain from new kinds — leave nullable on downgrade
    # only if empty; for safety keep nullable after downgrade of new columns.
    # Re-tighten for rows that always had chat/turn historically:
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM cost_records WHERE chat_id IS NULL) THEN
            ALTER TABLE cost_records ALTER COLUMN chat_id SET NOT NULL;
          END IF;
          IF NOT EXISTS (SELECT 1 FROM cost_records WHERE turn_id IS NULL) THEN
            ALTER TABLE cost_records ALTER COLUMN turn_id SET NOT NULL;
          END IF;
        END $$;
        """
    )
