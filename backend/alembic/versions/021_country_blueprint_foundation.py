"""Add country metadata and versioned Gemini blueprint foundations.

Revision ID: 021
Revises: 020
Create Date: 2026-07-25
"""

import sqlalchemy as sa

from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("scraping_missions") as batch_op:
        batch_op.add_column(sa.Column("country_iso3", sa.String(length=3), nullable=True))
        batch_op.add_column(sa.Column("continent", sa.String(length=32), nullable=True))
        batch_op.create_index("ix_scraping_missions_country_iso3", ["country_iso3"])
    with op.batch_alter_table("scraping_blueprints") as batch_op:
        batch_op.add_column(sa.Column("country_name_snapshot", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("country_iso3_snapshot", sa.String(length=3), nullable=True))
        batch_op.add_column(sa.Column("continent_snapshot", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("provider", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("provider_model_id", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("prompt_template_version", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("rendered_prompt_snapshot", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("human_readable_blueprint", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("structured_blueprint", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("citations", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("revision_request", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("generation_error", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_scraping_blueprints_provider", ["provider"])
        batch_op.create_index("ix_scraping_blueprints_prompt_template_version", ["prompt_template_version"])


def downgrade() -> None:
    with op.batch_alter_table("scraping_blueprints") as batch_op:
        batch_op.drop_index("ix_scraping_blueprints_prompt_template_version")
        batch_op.drop_index("ix_scraping_blueprints_provider")
        for column in (
            "discarded_at", "completed_at", "started_at", "queued_at", "generation_error",
            "revision_request", "citations", "structured_blueprint", "human_readable_blueprint",
            "rendered_prompt_snapshot", "prompt_template_version", "provider_model_id", "provider",
            "continent_snapshot", "country_iso3_snapshot", "country_name_snapshot",
        ):
            batch_op.drop_column(column)
    with op.batch_alter_table("scraping_missions") as batch_op:
        batch_op.drop_index("ix_scraping_missions_country_iso3")
        batch_op.drop_column("continent")
        batch_op.drop_column("country_iso3")
