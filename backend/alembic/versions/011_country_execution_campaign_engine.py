"""Add country-aware scraping execution campaigns

Revision ID: 011
Revises: 010
Create Date: 2026-07-14
"""

from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


execution_status = sa.Enum(
    "queued",
    "running",
    "cancel_requested",
    "completed",
    "failed",
    "cancelled",
    name="scrapingexecutionstatus",
    native_enum=False,
)

execution_agent_status = sa.Enum(
    "waiting",
    "queued",
    "running",
    "completed",
    "failed",
    "cancelled",
    name="scrapingexecutionagentstatus",
    native_enum=False,
)

coverage_status = sa.Enum(
    "not_started",
    "queued",
    "in_progress",
    "covered",
    "covered_no_results",
    "partially_covered",
    "blocked",
    "human_review_required",
    "failed",
    "cancelled",
    name="scrapingcoveragestatus",
    native_enum=False,
)

task_status = sa.Enum(
    "queued",
    "blocked",
    "running",
    "completed",
    "failed",
    "cancelled",
    name="scrapingtaskstatus",
    native_enum=False,
)


def upgrade() -> None:
    op.add_column(
        "scraping_missions",
        sa.Column("country_code", sa.String(length=2), nullable=True),
    )
    op.add_column(
        "scraping_missions", sa.Column("country_name", sa.String(length=120), nullable=True)
    )

    op.create_table(
        "scraping_executions",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("mission_id", sa.String(length=36), nullable=False),
        sa.Column("blueprint_id", sa.String(length=36), nullable=False),
        sa.Column("team_plan_id", sa.String(length=36), nullable=False),
        sa.Column("execution_type", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", execution_status, nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("country_name", sa.String(length=120), nullable=False),
        sa.Column("country_profile_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("last_event_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sources_discovered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_extracted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_verified", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicates_detected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_sources", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("coverage_debt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.String(length=36), nullable=False),
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
        sa.ForeignKeyConstraint(["blueprint_id"], ["scraping_blueprints.id"]),
        sa.ForeignKeyConstraint(["mission_id"], ["scraping_missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["team_plan_id"], ["scraping_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_scraping_executions_organization_id", "scraping_executions", ["organization_id"]
    )
    op.create_index("ix_scraping_executions_mission_id", "scraping_executions", ["mission_id"])
    op.create_index("ix_scraping_executions_blueprint_id", "scraping_executions", ["blueprint_id"])
    op.create_index("ix_scraping_executions_team_plan_id", "scraping_executions", ["team_plan_id"])
    op.create_index("ix_scraping_executions_status", "scraping_executions", ["status"])
    op.create_index("ix_scraping_executions_created_at", "scraping_executions", ["created_at"])
    op.create_index(
        "uq_scraping_executions_active_team_plan",
        "scraping_executions",
        ["team_plan_id"],
        unique=True,
        postgresql_where=sa.text("status in ('queued', 'running', 'cancel_requested')"),
        sqlite_where=sa.text("status in ('queued', 'running', 'cancel_requested')"),
    )

    op.create_table(
        "scraping_execution_agents",
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("team_agent_id", sa.String(length=36), nullable=False),
        sa.Column("status", execution_agent_status, nullable=False),
        sa.Column("current_task_id", sa.String(length=36), nullable=True),
        sa.Column("current_action", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
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
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_agent_id"], ["scraping_run_agents.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", "team_agent_id", name="uq_execution_agent_team_agent"),
    )
    op.create_index(
        "ix_scraping_execution_agents_execution_id",
        "scraping_execution_agents",
        ["execution_id"],
    )
    op.create_index(
        "ix_scraping_execution_agents_team_agent_id",
        "scraping_execution_agents",
        ["team_agent_id"],
    )
    op.create_index(
        "ix_scraping_execution_agents_status", "scraping_execution_agents", ["status"]
    )

    op.create_table(
        "scraping_coverage_cells",
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("region_code", sa.String(length=32), nullable=True),
        sa.Column("region_name", sa.Text(), nullable=False),
        sa.Column("language_code", sa.String(length=16), nullable=True),
        sa.Column("language_name", sa.Text(), nullable=False),
        sa.Column("source_category", sa.Text(), nullable=False),
        sa.Column("status", coverage_status, nullable=False),
        sa.Column("assigned_execution_agent_id", sa.String(length=36), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
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
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["assigned_execution_agent_id"],
            ["scraping_execution_agents.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "execution_id",
            "region_name",
            "language_name",
            "source_category",
            name="uq_scraping_coverage_cell_matrix",
        ),
    )
    op.create_index(
        "ix_scraping_coverage_cells_execution_id", "scraping_coverage_cells", ["execution_id"]
    )
    op.create_index("ix_scraping_coverage_cells_status", "scraping_coverage_cells", ["status"])
    op.create_index(
        "ix_scraping_coverage_cells_assigned_agent",
        "scraping_coverage_cells",
        ["assigned_execution_agent_id"],
    )

    op.create_table(
        "scraping_tasks",
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("execution_agent_id", sa.String(length=36), nullable=False),
        sa.Column("coverage_cell_id", sa.String(length=36), nullable=True),
        sa.Column("parent_task_id", sa.String(length=36), nullable=True),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", task_status, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("current_action", sa.String(length=255), nullable=True),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("output_json", sa.JSON(), nullable=False),
        sa.Column("dependency_task_ids_json", sa.JSON(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["coverage_cell_id"], ["scraping_coverage_cells.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["execution_agent_id"], ["scraping_execution_agents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_task_id"], ["scraping_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scraping_tasks_execution_id", "scraping_tasks", ["execution_id"])
    op.create_index(
        "ix_scraping_tasks_execution_agent_id", "scraping_tasks", ["execution_agent_id"]
    )
    op.create_index("ix_scraping_tasks_coverage_cell_id", "scraping_tasks", ["coverage_cell_id"])
    op.create_index("ix_scraping_tasks_status", "scraping_tasks", ["status"])
    op.create_index("ix_scraping_tasks_task_type", "scraping_tasks", ["task_type"])
    op.create_index("ix_scraping_tasks_priority", "scraping_tasks", ["priority"])

    op.create_table(
        "scraping_events",
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("execution_agent_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("coverage_cell_id", sa.String(length=36), nullable=True),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["coverage_cell_id"], ["scraping_coverage_cells.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["execution_agent_id"], ["scraping_execution_agents.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_id"], ["scraping_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", "sequence_number", name="uq_scraping_event_sequence"),
    )
    op.create_index(
        "ix_scraping_events_execution_sequence",
        "scraping_events",
        ["execution_id", "sequence_number"],
    )
    op.create_index(
        "ix_scraping_events_execution_agent_id", "scraping_events", ["execution_agent_id"]
    )
    op.create_index("ix_scraping_events_task_id", "scraping_events", ["task_id"])
    op.create_index("ix_scraping_events_event_type", "scraping_events", ["event_type"])
    op.create_index("ix_scraping_events_created_at", "scraping_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_scraping_events_created_at", table_name="scraping_events")
    op.drop_index("ix_scraping_events_event_type", table_name="scraping_events")
    op.drop_index("ix_scraping_events_task_id", table_name="scraping_events")
    op.drop_index("ix_scraping_events_execution_agent_id", table_name="scraping_events")
    op.drop_index("ix_scraping_events_execution_sequence", table_name="scraping_events")
    op.drop_table("scraping_events")

    op.drop_index("ix_scraping_tasks_priority", table_name="scraping_tasks")
    op.drop_index("ix_scraping_tasks_task_type", table_name="scraping_tasks")
    op.drop_index("ix_scraping_tasks_status", table_name="scraping_tasks")
    op.drop_index("ix_scraping_tasks_coverage_cell_id", table_name="scraping_tasks")
    op.drop_index("ix_scraping_tasks_execution_agent_id", table_name="scraping_tasks")
    op.drop_index("ix_scraping_tasks_execution_id", table_name="scraping_tasks")
    op.drop_table("scraping_tasks")

    op.drop_index("ix_scraping_coverage_cells_assigned_agent", table_name="scraping_coverage_cells")
    op.drop_index("ix_scraping_coverage_cells_status", table_name="scraping_coverage_cells")
    op.drop_index("ix_scraping_coverage_cells_execution_id", table_name="scraping_coverage_cells")
    op.drop_table("scraping_coverage_cells")

    op.drop_index("ix_scraping_execution_agents_status", table_name="scraping_execution_agents")
    op.drop_index(
        "ix_scraping_execution_agents_team_agent_id", table_name="scraping_execution_agents"
    )
    op.drop_index(
        "ix_scraping_execution_agents_execution_id", table_name="scraping_execution_agents"
    )
    op.drop_table("scraping_execution_agents")

    op.drop_index("uq_scraping_executions_active_team_plan", table_name="scraping_executions")
    op.drop_index("ix_scraping_executions_created_at", table_name="scraping_executions")
    op.drop_index("ix_scraping_executions_status", table_name="scraping_executions")
    op.drop_index("ix_scraping_executions_team_plan_id", table_name="scraping_executions")
    op.drop_index("ix_scraping_executions_blueprint_id", table_name="scraping_executions")
    op.drop_index("ix_scraping_executions_mission_id", table_name="scraping_executions")
    op.drop_index("ix_scraping_executions_organization_id", table_name="scraping_executions")
    op.drop_table("scraping_executions")

    op.drop_column("scraping_missions", "country_name")
    op.drop_column("scraping_missions", "country_code")
