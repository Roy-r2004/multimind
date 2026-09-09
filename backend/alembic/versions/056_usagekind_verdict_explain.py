"""Add VERDICT_EXPLAIN to the PostgreSQL usagekind enum.

Revision ID: 056
Revises: 055
Create Date: 2026-09-09

SQLAlchemy CostRecord.kind uses Enum(UsageKind), which on PostgreSQL is the
native type ``usagekind`` with member *names* (e.g. VERDICT, CHAT_MEMORY).
Migration 055 created verdict_simple_explanations but did not extend this enum.
Python already has UsageKind.VERDICT_EXPLAIN; inserting a CostRecord then fails
with InvalidTextRepresentationError until the database enum includes that label.
"""

import sqlalchemy as sa
from alembic import op

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None

ENUM_TYPE = "usagekind"
ENUM_LABEL = "VERDICT_EXPLAIN"
ADD_VALUE_SQL = "ALTER TYPE usagekind ADD VALUE IF NOT EXISTS 'VERDICT_EXPLAIN'"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    exists = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM pg_type
            WHERE typname = :typname
              AND typtype = 'e'
            """
        ),
        {"typname": ENUM_TYPE},
    ).scalar()
    if not exists:
        return
    # PG 12+ allows ADD VALUE in a transaction; autocommit keeps this usable
    # immediately if a later statement in the same migration needed the label.
    with op.get_context().autocommit_block():
        op.execute(sa.text(ADD_VALUE_SQL))


def downgrade() -> None:
    # PostgreSQL cannot drop a single enum label without recreating the type and
    # rewriting dependent columns. This repo does not recreate enums on downgrade
    # (see 054's no-op). Leave VERDICT_EXPLAIN in place.
    return
