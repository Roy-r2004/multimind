"""Widen scraping_blueprints.status for ready_for_review.

Revision ID: 025
Revises: 024

Migration 008 created status as a non-native enum whose longest value was
``superseded`` (10 chars), so PostgreSQL stored VARCHAR(10). Later lifecycle
values such as ``ready_for_review`` (16 chars) cannot be persisted until the
column is widened.

Downgrade intentionally leaves VARCHAR(32). Shrinking would risk truncating
existing ready_for_review / discarded rows.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None

_TARGET_LENGTH = 32


def upgrade() -> None:
    op.alter_column(
        "scraping_blueprints",
        "status",
        existing_type=sa.String(length=10),
        type_=sa.String(length=_TARGET_LENGTH),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Non-destructive: do not shrink VARCHAR(32) back to VARCHAR(10).
    return
