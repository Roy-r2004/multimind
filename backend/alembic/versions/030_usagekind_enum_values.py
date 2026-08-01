"""Add missing UsageKind PostgreSQL enum labels for cost_records.

Revision ID: 030
Revises: 029
Create Date: 2026-08-01

SQLAlchemy ``Enum(UsageKind)`` persists member *names* (e.g. EMBEDDING), not
values (embedding). Migration 029 did not extend the native ``usagekind`` type,
so inserts for new kinds fail with:

  invalid input value for enum usagekind: "EMBEDDING"

This revision adds every missing label idempotently. Existing labels and
cost_records rows are preserved. Enum labels cannot be removed safely on
PostgreSQL, so downgrade is a no-op.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None

# Exact capitalization emitted by SQLAlchemy Enum(UsageKind) (member names).
REQUIRED_USAGEKIND_LABELS = (
    "ANSWER",
    "VERDICT",
    "INSURANCE",
    "LESSON",
    "BRAIN",
    "EMBEDDING",
    "SCRAPING",
    "BLUEPRINT",
    "EXTRACTION",
    "CLASSIFICATION",
    "PLANNER",
    "DOCUMENT",
    "HELPER",
    "OTHER",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for label in REQUIRED_USAGEKIND_LABELS:
        # IF NOT EXISTS keeps re-runs / partial applies safe.
        # Quote the label as a SQL string literal; labels are fixed constants.
        op.execute(text(f"ALTER TYPE usagekind ADD VALUE IF NOT EXISTS '{label}'"))


def downgrade() -> None:
    # PostgreSQL cannot drop individual enum values without recreating the type
    # and rewriting dependent columns/rows. Preserve historical cost_records.
    return
