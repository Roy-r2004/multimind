"""Add dynamic scraping team planner runs

Revision ID: 010
Revises: 009
Create Date: 2026-07-13
"""

from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


run_status = sa.Enum(
    "planning",
    "planned",
    "running",
    "completed",
    "failed",
    "cancelled",
    name="scrapingrunstatus",
    native_enum=False,
)

agent_status = sa.Enum(
    "planned",
    "waiting",
    "running",
    "completed",
    "failed",
    "cancelled",
    name="scrapingrunagentstatus",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "scraping_runs",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("mission_id", sa.String(length=36), nullable=False),
        sa.Column("blueprint_id", sa.String(length=36), nullable=False),
        sa.Column("model_set_id", sa.String(length=64), nullable=False),
        sa.Column("status", run_status, nullable=False),
        sa.Column("recommended_agent_count", sa.Integer(), nullable=True),
        sa.Column("planner_model_id", sa.String(length=64), nullable=True),
        sa.Column("planner_rationale", sa.Text(), nullable=True),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["blueprint_id"], ["scraping_blueprints.id"]),
        sa.ForeignKeyConstraint(["mission_id"], ["scraping_missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("blueprint_id", name="uq_scraping_runs_blueprint_id"),
    )
    op.create_index("ix_scraping_runs_organization_id", "scraping_runs", ["organization_id"])
    op.create_index("ix_scraping_runs_mission_id", "scraping_runs", ["mission_id"])
    op.create_index("ix_scraping_runs_status", "scraping_runs", ["status"])
    op.create_index("ix_scraping_runs_created_at", "scraping_runs", ["created_at"])

    op.create_table(
        "scraping_run_agents",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("parent_agent_id", sa.String(length=36), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("role", sa.String(length=120), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("assigned_scope", sa.JSON(), nullable=False),
        sa.Column("model_id", sa.String(length=64), nullable=False),
        sa.Column("status", agent_status, nullable=False),
        sa.Column("dependency_agent_ids", sa.JSON(), nullable=False),
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
            ["parent_agent_id"], ["scraping_run_agents.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["scraping_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scraping_run_agents_run_id", "scraping_run_agents", ["run_id"])
    op.create_index(
        "ix_scraping_run_agents_run_sequence",
        "scraping_run_agents",
        ["run_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_scraping_run_agents_run_sequence", table_name="scraping_run_agents")
    op.drop_index("ix_scraping_run_agents_run_id", table_name="scraping_run_agents")
    op.drop_table("scraping_run_agents")

    op.drop_index("ix_scraping_runs_created_at", table_name="scraping_runs")
    op.drop_index("ix_scraping_runs_status", table_name="scraping_runs")
    op.drop_index("ix_scraping_runs_mission_id", table_name="scraping_runs")
    op.drop_index("ix_scraping_runs_organization_id", table_name="scraping_runs")
    op.drop_table("scraping_runs")
