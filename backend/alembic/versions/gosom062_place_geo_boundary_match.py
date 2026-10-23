"""Add maps_places.geo_boundary_match — per-place geographic result
validation (Universal AI-Assisted Geographic Discovery task).

Coordinate-based search targeting is a discovery STRATEGY, not proof of
membership: a result whose coordinates fall inside a state/province's
bounding RECTANGLE is not necessarily inside its real boundary. This
column records, per discovered place, whether its coordinates (when the
provider supplied any) were actually checked against the run's verified
polygon (``maps_geo_boundaries.contains_point``) and what that check
found — distinct from "no polygon was available to check against" and
"the provider gave no coordinates at all", so neither case is ever
silently folded into a false "geographically confirmed" claim.

Values: "inside_polygon", "outside_polygon", "no_polygon_available",
"no_coordinates" — written once at discovery time, nullable for every
place discovered before this migration. Purely additive; does not touch
Phase 2 classification/eligibility columns or logic.

Revision ID: gosom062_place_geo_boundary_match
Revises: gosom061_pagination_token_text
"""

import sqlalchemy as sa
from alembic import op

revision = "gosom062_place_geo_boundary_match"
down_revision = "gosom061_pagination_token_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_places",
        sa.Column("geo_boundary_match", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("maps_places", "geo_boundary_match")
