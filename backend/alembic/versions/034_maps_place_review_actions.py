"""Add maps place review actions table for Maps census admin Phase 4.

Revision ID: 034
Revises: 033
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maps_place_review_actions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "place_id",
            sa.String(length=36),
            sa.ForeignKey("maps_places.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("maps_census_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_user_id",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=True),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_maps_place_review_actions_place_id",
        "maps_place_review_actions",
        ["place_id"],
        unique=False,
    )
    op.create_index(
        "ix_maps_place_review_actions_run_id",
        "maps_place_review_actions",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        "ix_maps_place_review_actions_created_at",
        "maps_place_review_actions",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_maps_place_review_actions_created_at", table_name="maps_place_review_actions")
    op.drop_index("ix_maps_place_review_actions_run_id", table_name="maps_place_review_actions")
    op.drop_index("ix_maps_place_review_actions_place_id", table_name="maps_place_review_actions")
    op.drop_table("maps_place_review_actions")
