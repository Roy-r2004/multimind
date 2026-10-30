"""Key maps_website_crawl_cache by (domain, seed_path), not domain alone.

Root cause fix for the Phase 2 pipeline-wide branch-content-pollution bug:
every official-site crawl was cached under ``normalized_domain`` alone, so
once ANY branch of a multi-location domain was crawled, every OTHER branch
resolved to a different path on that same domain silently reused that
first branch's crawled pages for the remainder of the cache TTL — even
though each branch's own ``official_website`` path differed. This is how
A-klinikkasäätiön Päihdesairaala's official_website correctly resolved to
its own ``/toimipisteet/paihdesairaala/`` page, yet the AI only ever saw
a sibling branch's homepage/service pages cached earlier for
``www.a-klinikka.fi``.

Existing cache rows are backfilled with seed_path='/' (treated as a
homepage-only cache bucket); they simply miss on the next lookup for any
non-root seed path and get re-fetched, which is safe and not a data loss
of anything authoritative (crawl cache is a disposable optimization).

Revision ID: gosom065_crawl_cache_seed_path
Revises: gosom064_inpatient_treatment
"""

import sqlalchemy as sa
from alembic import op

revision = "gosom065_crawl_cache_seed_path"
down_revision = "gosom064_inpatient_treatment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("maps_website_crawl_cache") as batch:
        batch.add_column(
            sa.Column("seed_path", sa.String(length=1024), nullable=False, server_default="/")
        )
        batch.drop_constraint("uq_maps_website_crawl_cache_domain", type_="unique")
        batch.create_unique_constraint(
            "uq_maps_website_crawl_cache_domain_path",
            ["normalized_domain", "seed_path"],
        )
        batch.create_index(
            "ix_maps_website_crawl_cache_domain",
            ["normalized_domain"],
        )


def downgrade() -> None:
    with op.batch_alter_table("maps_website_crawl_cache") as batch:
        batch.drop_index("ix_maps_website_crawl_cache_domain")
        batch.drop_constraint("uq_maps_website_crawl_cache_domain_path", type_="unique")
        batch.create_unique_constraint(
            "uq_maps_website_crawl_cache_domain", ["normalized_domain"]
        )
        batch.drop_column("seed_path")
