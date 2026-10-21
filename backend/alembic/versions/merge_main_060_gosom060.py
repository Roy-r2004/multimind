"""Merge main's migration history with feature/gosom-maps-provider's.

Both branches diverged from "058" independently: main continued as
058 -> 059_chat_answer_pins -> 060_processed_documents, while this feature
branch continued as 058 -> gosom059_exec_token -> gosom060_cell_deadline
(renamed off the bare "059"/"060" specifically to avoid colliding with
main's own same-numbered revisions — see the docstrings on those two files).
Combining the two branches' alembic/versions/ directories therefore produces
two heads ("060" and "gosom060_cell_deadline") with no revision that
depends on both.

This revision does nothing to the schema itself — both parent chains
already applied their own changes independently. It exists solely to give
the combined history a single head, so `alembic upgrade head` is
unambiguous for a database coming from any of: main's history, this
branch's history, or neither (a fresh database).

Revision ID: merge_main_060_gosom060
Revises: 060, gosom060_cell_deadline
"""

from __future__ import annotations

revision = "merge_main_060_gosom060"
down_revision = ("060", "gosom060_cell_deadline")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
