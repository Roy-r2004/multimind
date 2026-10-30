"""Add gosom_health_state — persisted, DB-backed counters for detecting a
frozen Gosom internal dispatcher across watchdog relaunches (the in-memory
CircuitBreaker is re-created fresh on every run_census() call and cannot see
across them).

Singleton table: exactly one row is expected, created lazily by
gosom_health_monitor.get_or_create_state() on first use.

Revision ID: gosom063_health_state
Revises: gosom062_geo_boundary_match

NOTE: keep revision ids <= 32 chars (alembic_version.version_num VARCHAR(32)).
"""

import sqlalchemy as sa
from alembic import op

revision = "gosom063_health_state"
down_revision = "gosom062_geo_boundary_match"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gosom_health_state",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("consecutive_timeouts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_successes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_timeout_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restart_in_progress", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("restart_in_progress_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_restart_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_restart_reason", sa.Text(), nullable=True),
        sa.Column("restart_history", sa.JSON(), nullable=True),
        sa.Column("consecutive_failed_restarts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("restart_exhausted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("restart_exhausted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restart_exhausted_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("gosom_health_state")
