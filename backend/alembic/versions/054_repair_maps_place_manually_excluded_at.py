"""Repair maps_places.manually_excluded_at if revision 041 never ran on this DB.

Revision 041 adds this column and sits in the ancestry of 053. A database can
still be stamped at 053 without the column if 041 was inserted into the chain
after this database had already migrated past that point (042+). Alembic then
treats 041 as applied because it is an ancestor of the stamp, so upgrade head
is a no-op.

This revision is idempotent: it adds the column only when it is missing.

Revision ID: 054
Revises: 053
Create Date: 2026-09-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None

COLUMN = "manually_excluded_at"


def upgrade() -> None:
    inspector = inspect(op.get_bind())
    columns = {col["name"] for col in inspector.get_columns("maps_places")}
    if COLUMN in columns:
        return
    op.add_column(
        "maps_places",
        sa.Column(COLUMN, sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Leave the column in place. On a healthy database it belongs to 041, which
    # remains in the ancestry after this repair is reversed.
    return
