"""Add a persisted first_attempted_at to maps_census_cells so the absolute
per-cell deadline (20 minutes, independent of heartbeat/retries) survives a
worker crash and restart.

Same long-lived-branch numbering note as gosom059_exec_token: this is
branch-local revision "gosom060", chained off "gosom059_exec_token" — not
the bare "060" (main has its own unrelated 060_processed_documents chained
off a different 059). Whoever merges this branch must add a merge revision
joining the two histories, not renumber either side.

Revision ID: gosom060_cell_deadline
Revises: gosom059_exec_token
"""

import sqlalchemy as sa
from alembic import op

revision = "gosom060_cell_deadline"
down_revision = "gosom059_exec_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_census_cells",
        sa.Column("first_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("maps_census_cells") as batch:
        batch.drop_column("first_attempted_at")
