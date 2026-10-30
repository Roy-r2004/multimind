"""Add maps_census_runs.discovery_provider.

Lets each Maps census run choose its discovery source (google_places or
gosom) so the two can run side by side in one deployment. NULL keeps the
MAPS_DISCOVERY_PROVIDER setting for every run created before this column.

Revision ID: gosom066_run_discovery_provider
Revises: gosom065_crawl_cache_seed_path
"""

import sqlalchemy as sa
from alembic import op

revision = "gosom066_run_discovery_provider"
down_revision = "gosom065_crawl_cache_seed_path"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_census_runs",
        sa.Column("discovery_provider", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("maps_census_runs", "discovery_provider")
