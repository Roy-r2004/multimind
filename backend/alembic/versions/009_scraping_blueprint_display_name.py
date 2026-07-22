"""Add display name to scraping blueprints

Revision ID: 009
Revises: 008
Create Date: 2026-07-13
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scraping_blueprints",
        sa.Column("display_name", sa.String(length=160), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scraping_blueprints", "display_name")
