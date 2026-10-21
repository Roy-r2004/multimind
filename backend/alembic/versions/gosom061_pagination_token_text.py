"""Widen maps_census_cells.pagination_resume_token from VARCHAR(2048) to TEXT.

Google's Places nextPageToken has no documented length cap and has been
observed over 2048 characters in dense result sets (verified in production
data from Alabama, Michigan, Colorado, and Florida). A VARCHAR(2048) column
raised a DataError on commit whenever a long token arrived — which rolled
back the whole transaction (including that page's already-flushed new
places) and, via the normal retry/terminal-failure path, eventually marked
the affected cell FAILED even though Google Places had returned valid
results.

TEXT in PostgreSQL has no length limit and the same storage/performance
characteristics as VARCHAR for values that fit either (Postgres stores both
the same way internally) — purely additive in effect, no data conversion
needed; every existing token (all comfortably under 2048 chars) round-trips
unchanged.

Revision ID: gosom061_pagination_token_text
Revises: merge_main_060_gosom060
"""

import sqlalchemy as sa
from alembic import op

revision = "gosom061_pagination_token_text"
down_revision = "merge_main_060_gosom060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "maps_census_cells",
        "pagination_resume_token",
        existing_type=sa.String(length=2048),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Reverting to VARCHAR(2048) would truncate/reject any token saved
    # since the upgrade that exceeds 2048 characters — this is the one
    # direction that is NOT purely additive, so it is deliberately not
    # offered as a silent, lossy automatic downgrade.
    raise NotImplementedError(
        "Downgrading pagination_resume_token to VARCHAR(2048) would silently "
        "truncate or reject any token longer than 2048 characters already "
        "stored since the upgrade. If you must revert, first verify (and "
        "handle) any existing rows where length(pagination_resume_token) > "
        "2048, then alter the column manually."
    )
