"""Repair stamped databases missing the Phase 1 blueprint schema.

Revision ID: 024
Revises: 023

This is deliberately idempotent and non-destructive: it restores objects from
021/022 only when absent.  Downgrade is a no-op because those objects belong to
the earlier revisions and may have existed before this repair ran.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def _add_missing_columns(table: str, columns: list[sa.Column]) -> None:
    existing = _columns(table)
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def upgrade() -> None:
    _add_missing_columns(
        "scraping_missions",
        [
            sa.Column("country_iso3", sa.String(length=3), nullable=True),
            sa.Column("continent", sa.String(length=32), nullable=True),
        ],
    )
    if "ix_scraping_missions_country_iso3" not in _indexes("scraping_missions"):
        op.create_index("ix_scraping_missions_country_iso3", "scraping_missions", ["country_iso3"])

    _add_missing_columns(
        "scraping_blueprints",
        [
            sa.Column("country_name_snapshot", sa.String(length=120), nullable=True),
            sa.Column("country_iso3_snapshot", sa.String(length=3), nullable=True),
            sa.Column("continent_snapshot", sa.String(length=32), nullable=True),
            sa.Column("provider", sa.String(length=64), nullable=True),
            sa.Column("provider_model_id", sa.String(length=128), nullable=True),
            sa.Column("prompt_template_version", sa.String(length=64), nullable=True),
            sa.Column("rendered_prompt_snapshot", sa.Text(), nullable=True),
            sa.Column("human_readable_blueprint", sa.Text(), nullable=True),
            sa.Column("structured_blueprint", sa.JSON(), nullable=True),
            sa.Column("citations", sa.JSON(), nullable=True),
            sa.Column("revision_request", sa.Text(), nullable=True),
            sa.Column("generation_error", sa.Text(), nullable=True),
            sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("discarded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("provider_operation_id", sa.String(length=255), nullable=True),
            sa.Column("provider_execution_metadata", sa.JSON(), nullable=True),
        ],
    )
    for name, column in [
        ("ix_scraping_blueprints_provider", "provider"),
        ("ix_scraping_blueprints_prompt_template_version", "prompt_template_version"),
        ("ix_scraping_blueprints_provider_operation", "provider_operation_id"),
    ]:
        if name not in _indexes("scraping_blueprints"):
            op.create_index(name, "scraping_blueprints", [column])


def downgrade() -> None:
    """No-op: never reintroduce the stamped-schema drift repaired here."""
