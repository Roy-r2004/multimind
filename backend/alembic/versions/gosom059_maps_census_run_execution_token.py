"""Add a fencing token to maps_census_runs so a stale-heartbeat watchdog can
never let two concurrent run_census() executions both reach completion.

This is a long-lived feature branch (feature/gosom-maps-provider) that
diverged from main before main's own "059"/"060" migrations existed.
``revision`` below is deliberately NOT the bare number "059" — main already
has a different 059 (``059_chat_answer_pins``) chained off the same "058",
and two files both declaring ``revision = "059"`` would collide the moment
this branch and main share an alembic/versions/ directory. Using a
collision-proof id lets this migration coexist with main's history as a
second head off "058"; whoever merges this branch must add a trivial merge
revision (``down_revision = (main's current head, "gosom059_exec_token")``)
to join the two heads — do not renumber main's migrations to make room.

Revision ID: gosom059_exec_token
Revises: 058
"""

import sqlalchemy as sa
from alembic import op

revision = "gosom059_exec_token"
down_revision = "058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "maps_census_runs",
        sa.Column("execution_token", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("maps_census_runs") as batch:
        batch.drop_column("execution_token")
