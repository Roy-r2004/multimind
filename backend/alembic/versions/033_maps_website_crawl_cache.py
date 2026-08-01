"""Add website crawl cache table for Maps census Phase 3.

Revision ID: 033
Revises: 032
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "maps_website_crawl_cache",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("normalized_domain", sa.String(length=255), nullable=False),
        sa.Column("pages", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_domain", name="uq_maps_website_crawl_cache_domain"),
    )
    op.create_index(
        "ix_maps_website_crawl_cache_expires_at",
        "maps_website_crawl_cache",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_maps_website_crawl_cache_expires_at", table_name="maps_website_crawl_cache")
    op.drop_table("maps_website_crawl_cache")
