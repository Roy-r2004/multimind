"""Add mission campaign lifecycle checkpoints and concurrency guard.

Revision ID: 023
Revises: 022
"""

import sqlalchemy as sa

from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


_ACTIVE_CAMPAIGN_WHERE = (
    "execution_type = 'mission_campaign' and "
    "status in ('queued', 'running', 'pause_requested', 'paused', 'cancel_requested')"
)
_ACTIVE_TEAM_PLAN_WHERE = (
    "status in ('queued', 'running', 'pause_requested', 'paused', 'cancel_requested')"
)


def upgrade() -> None:
    with op.batch_alter_table("scraping_executions") as batch_op:
        batch_op.drop_index("uq_scraping_executions_active_team_plan")
        batch_op.create_index(
            "uq_scraping_executions_active_team_plan",
            ["team_plan_id"],
            unique=True,
            postgresql_where=sa.text(_ACTIVE_TEAM_PLAN_WHERE),
            sqlite_where=sa.text(_ACTIVE_TEAM_PLAN_WHERE),
        )
        batch_op.add_column(sa.Column("pause_requested_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.alter_column("team_plan_id", existing_type=sa.String(length=36), nullable=True)
        batch_op.add_column(
            sa.Column("execution_origin", sa.String(length=32), nullable=False, server_default="legacy_pipeline")
        )
        batch_op.add_column(sa.Column("blueprint_version_snapshot", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("created_by", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key("fk_scraping_executions_created_by", "users", ["created_by"], ["id"])
        for name, type_, default in [
            ("current_stage", sa.String(length=64), None),
            ("current_stage_label", sa.String(length=255), None),
            ("current_provider", sa.String(length=120), None),
            ("current_model", sa.String(length=255), None),
            ("latest_message", sa.Text(), None),
            ("current_region", sa.String(length=255), None),
            ("current_website", sa.Text(), None),
            ("current_page", sa.Text(), None),
            ("budget_status", sa.String(length=32), None),
            ("campaign_budget", sa.Float(), None),
            ("progress_percent", sa.Integer(), "0"),
            ("regions_total", sa.Integer(), "0"),
            ("regions_completed", sa.Integer(), "0"),
            ("candidates_discovered", sa.Integer(), "0"),
            ("websites_queued", sa.Integer(), "0"),
            ("pages_visited", sa.Integer(), "0"),
            ("pdfs_processed", sa.Integer(), "0"),
            ("verified_facilities", sa.Integer(), "0"),
            ("manual_review_count", sa.Integer(), "0"),
            ("excluded_count", sa.Integer(), "0"),
            ("duplicates_merged", sa.Integer(), "0"),
            ("phones_found", sa.Integer(), "0"),
            ("emails_found", sa.Integer(), "0"),
            ("country_mismatches", sa.Integer(), "0"),
            ("provider_request_count", sa.Integer(), "0"),
            ("input_tokens", sa.Integer(), "0"),
            ("output_tokens", sa.Integer(), "0"),
            ("estimated_cost", sa.Float(), "0"),
            ("budget_used", sa.Float(), "0"),
        ]:
            batch_op.add_column(
                sa.Column(name, type_, nullable=default is None, server_default=default)
            )
        batch_op.create_index(
            "uq_scraping_executions_active_mission_campaign",
            ["mission_id"],
            unique=True,
            postgresql_where=sa.text(_ACTIVE_CAMPAIGN_WHERE),
            sqlite_where=sa.text(_ACTIVE_CAMPAIGN_WHERE),
        )


def downgrade() -> None:
    with op.batch_alter_table("scraping_executions") as batch_op:
        batch_op.drop_index("uq_scraping_executions_active_mission_campaign")
        batch_op.drop_constraint("fk_scraping_executions_created_by", type_="foreignkey")
        for name in [
            "budget_used", "estimated_cost", "output_tokens", "input_tokens", "provider_request_count",
            "country_mismatches", "emails_found", "phones_found", "duplicates_merged", "excluded_count",
            "manual_review_count", "verified_facilities", "pdfs_processed", "pages_visited",
            "websites_queued", "candidates_discovered", "regions_completed", "regions_total",
            "progress_percent", "campaign_budget", "budget_status", "current_page", "current_website",
            "current_region", "latest_message", "current_model", "current_provider",
            "current_stage_label", "current_stage", "created_by", "blueprint_version_snapshot",
            "execution_origin",
        ]:
            batch_op.drop_column(name)
        batch_op.alter_column("team_plan_id", existing_type=sa.String(length=36), nullable=False)
        batch_op.drop_index("uq_scraping_executions_active_team_plan")
        batch_op.create_index(
            "uq_scraping_executions_active_team_plan",
            ["team_plan_id"],
            unique=True,
            postgresql_where=sa.text(
                "status in ('queued', 'running', 'cancel_requested')"
            ),
            sqlite_where=sa.text(
                "status in ('queued', 'running', 'cancel_requested')"
            ),
        )
        batch_op.drop_column("resumed_at")
        batch_op.drop_column("paused_at")
        batch_op.drop_column("pause_requested_at")
