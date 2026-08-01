"""Add missing UsageKind PostgreSQL enum labels for cost_records.

Revision ID: 032
Revises: 031
Create Date: 2026-08-01

SQLAlchemy ``Enum(UsageKind)`` persists member *names* (e.g. EMBEDDING), not
values (embedding). Migration 031 did not extend the native ``usagekind`` type,
so inserts for new kinds fail with:

  invalid input value for enum usagekind: "EMBEDDING"

This revision adds every missing label idempotently. Existing labels and
cost_records rows are preserved. Enum labels cannot be removed safely on
PostgreSQL, so downgrade is a no-op.
"""

from __future__ import annotations

from sqlalchemy import text

from alembic import op

revision = "032"
down_revision = "031"
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

    # Fresh alembic-only DBs never created the native type (cost_records.kind is
    # VARCHAR in 001). Older local DBs already have usagekind from SQLAlchemy.
    type_exists = bind.execute(
        text("SELECT 1 FROM pg_type WHERE typname = 'usagekind'")
    ).scalar()
    if not type_exists:
        labels_sql = ", ".join(f"'{label}'" for label in REQUIRED_USAGEKIND_LABELS)
        op.execute(text(f"CREATE TYPE usagekind AS ENUM ({labels_sql})"))
        return

    for label in REQUIRED_USAGEKIND_LABELS:
        # IF NOT EXISTS keeps re-runs / partial applies safe.
        # Quote the label as a SQL string literal; labels are fixed constants.
        op.execute(text(f"ALTER TYPE usagekind ADD VALUE IF NOT EXISTS '{label}'"))


def downgrade() -> None:
    # PostgreSQL cannot drop individual enum values without recreating the type
    # and rewriting dependent columns/rows. Preserve historical cost_records.
    return
