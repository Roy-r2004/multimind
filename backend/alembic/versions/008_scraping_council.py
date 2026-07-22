"""Add Scraping Council missions and blueprints

Revision ID: 008
Revises: 007
Create Date: 2026-07-12
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


mission_status = sa.Enum(
    "draft",
    "blueprint_generating",
    "awaiting_approval",
    "approved",
    "rejected",
    "failed",
    "cancelled",
    name="scrapingmissionstatus",
    native_enum=False,
)

blueprint_status = sa.Enum(
    "generating",
    "draft",
    "approved",
    "rejected",
    "superseded",
    "failed",
    name="scrapingblueprintstatus",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "scraping_missions",
        sa.Column("org_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("model_set_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("original_prompt", sa.Text(), nullable=False),
        sa.Column("status", mission_status, nullable=False),
        sa.Column("active_blueprint_id", sa.String(length=36), nullable=True),
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
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scraping_missions_org_id", "scraping_missions", ["org_id"])
    op.create_index("ix_scraping_missions_created_by", "scraping_missions", ["created_by"])
    op.create_index("ix_scraping_missions_status", "scraping_missions", ["status"])
    op.create_index("ix_scraping_missions_updated_at", "scraping_missions", ["updated_at"])

    op.create_table(
        "scraping_blueprints",
        sa.Column("mission_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", blueprint_status, nullable=False),
        sa.Column("blueprint_json", sa.JSON(), nullable=True),
        sa.Column("model_set_id", sa.String(length=64), nullable=False),
        sa.Column("judge_model_id", sa.String(length=64), nullable=True),
        sa.Column("approved_by", sa.String(length=36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by", sa.String(length=36), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("change_instructions", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["mission_id"], ["scraping_missions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rejected_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mission_id", "version", name="uq_scraping_blueprint_mission_version"),
    )
    op.create_index("ix_scraping_blueprints_mission_id", "scraping_blueprints", ["mission_id"])
    op.create_index("ix_scraping_blueprints_status", "scraping_blueprints", ["status"])
    op.create_index("ix_scraping_blueprints_created_at", "scraping_blueprints", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_scraping_blueprints_created_at", table_name="scraping_blueprints")
    op.drop_index("ix_scraping_blueprints_status", table_name="scraping_blueprints")
    op.drop_index("ix_scraping_blueprints_mission_id", table_name="scraping_blueprints")
    op.drop_table("scraping_blueprints")

    op.drop_index("ix_scraping_missions_updated_at", table_name="scraping_missions")
    op.drop_index("ix_scraping_missions_status", table_name="scraping_missions")
    op.drop_index("ix_scraping_missions_created_by", table_name="scraping_missions")
    op.drop_index("ix_scraping_missions_org_id", table_name="scraping_missions")
    op.drop_table("scraping_missions")
