"""Add My Playbooks tables for personal user+org operating profiles.

Revision ID: 048
Revises: 047
Create Date: 2026-08-17

New tables only. Does not alter chats, turns, Brain, or user deletion behavior.
"""

from alembic import op
import sqlalchemy as sa

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playbooks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "org_id",
            sa.String(length=36),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="not_generated",
        ),
        sa.Column(
            "injection_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("core_summary", sa.Text(), nullable=True),
        sa.Column(
            "extraction_version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "playbook_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_success_run_id", sa.String(length=36), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("org_id", "user_id", name="uq_playbook_org_user"),
    )

    op.create_table(
        "playbook_observations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "playbook_id",
            sa.String(length=36),
            sa.ForeignKey("playbooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=True),
        sa.Column("observation", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="active",
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "evidence_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "superseded_by_id",
            sa.String(length=36),
            sa.ForeignKey("playbook_observations.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_corrected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "user_excluded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
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
    )
    op.create_index(
        "ix_playbook_observations_playbook_category_status",
        "playbook_observations",
        ["playbook_id", "category", "status"],
    )

    op.create_table(
        "playbook_observation_sources",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "observation_id",
            sa.String(length=36),
            sa.ForeignKey("playbook_observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chat_id",
            sa.String(length=36),
            sa.ForeignKey("chats.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "turn_id",
            sa.String(length=36),
            sa.ForeignKey("turns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_kind", sa.String(length=64), nullable=False),
        sa.Column("epistemic_role", sa.String(length=64), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_playbook_observation_sources_observation_id",
        "playbook_observation_sources",
        ["observation_id"],
    )

    op.create_table(
        "playbook_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "playbook_id",
            sa.String(length=36),
            sa.ForeignKey("playbooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "processed_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "total_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "warning_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index(
        "ix_playbook_runs_playbook_created_at",
        "playbook_runs",
        ["playbook_id", "created_at"],
    )

    op.create_table(
        "playbook_source_states",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "playbook_id",
            sa.String(length=36),
            sa.ForeignKey("playbooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "processed_run_id",
            sa.String(length=36),
            sa.ForeignKey("playbook_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="processed",
        ),
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
        sa.UniqueConstraint(
            "playbook_id",
            "source_type",
            "source_id",
            name="uq_playbook_source_state",
        ),
    )

    op.create_table(
        "playbook_excluded_sources",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "playbook_id",
            sa.String(length=36),
            sa.ForeignKey("playbooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chat_id",
            sa.String(length=36),
            sa.ForeignKey("chats.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "turn_id",
            sa.String(length=36),
            sa.ForeignKey("turns.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "chat_id IS NOT NULL OR turn_id IS NOT NULL",
            name="ck_playbook_excluded_source_present",
        ),
    )
    op.create_index(
        "uq_playbook_excluded_chat",
        "playbook_excluded_sources",
        ["playbook_id", "chat_id"],
        unique=True,
        postgresql_where=sa.text("turn_id IS NULL AND chat_id IS NOT NULL"),
        sqlite_where=sa.text("turn_id IS NULL AND chat_id IS NOT NULL"),
    )
    op.create_index(
        "uq_playbook_excluded_turn",
        "playbook_excluded_sources",
        ["playbook_id", "turn_id"],
        unique=True,
        postgresql_where=sa.text("turn_id IS NOT NULL"),
        sqlite_where=sa.text("turn_id IS NOT NULL"),
    )

    with op.batch_alter_table("playbooks") as batch:
        batch.create_foreign_key(
            "fk_playbooks_last_success_run_id",
            "playbook_runs",
            ["last_success_run_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("playbooks") as batch:
        batch.drop_constraint("fk_playbooks_last_success_run_id", type_="foreignkey")

    op.drop_index("uq_playbook_excluded_turn", table_name="playbook_excluded_sources")
    op.drop_index("uq_playbook_excluded_chat", table_name="playbook_excluded_sources")
    op.drop_table("playbook_excluded_sources")
    op.drop_table("playbook_source_states")
    op.drop_index("ix_playbook_runs_playbook_created_at", table_name="playbook_runs")
    op.drop_table("playbook_runs")
    op.drop_index(
        "ix_playbook_observation_sources_observation_id",
        table_name="playbook_observation_sources",
    )
    op.drop_table("playbook_observation_sources")
    op.drop_index(
        "ix_playbook_observations_playbook_category_status",
        table_name="playbook_observations",
    )
    op.drop_table("playbook_observations")
    op.drop_table("playbooks")
