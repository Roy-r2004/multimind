"""Align organization membership roles with the ORM PostgreSQL enum.

Revision ID: 034
Revises: 033
"""

from __future__ import annotations

import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None

ORGROLE_LABELS = ("OWNER", "ADMIN", "MEMBER", "VIEWER")
_DEFAULT_RE = re.compile(
    r"^'(?P<role>[^']+)'::(?:character varying|text|orgrole)$"
)


def _column_default(bind) -> str | None:
    return bind.execute(sa.text(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='org_memberships' "
        "AND column_name='role'"
    )).scalar_one()


def _normalized_default(default: str | None) -> str | None:
    if default is None:
        return None
    match = _DEFAULT_RE.fullmatch(default.strip())
    if match is None:
        raise RuntimeError(
            "Refusing migration 034: org_memberships.role has an unsupported default."
        )
    normalized = match.group("role").strip().upper()
    if normalized not in ORGROLE_LABELS:
        raise RuntimeError(
            "Refusing migration 034: org_memberships.role has an unsupported default."
        )
    return normalized


def _enum_labels(bind) -> tuple[str, ...] | None:
    rows = bind.execute(sa.text(
        "SELECT e.enumlabel FROM pg_type t "
        "JOIN pg_enum e ON e.enumtypid=t.oid "
        "WHERE t.typname='orgrole' ORDER BY e.enumsortorder"
    )).scalars().all()
    return tuple(rows) if rows else None


def upgrade() -> None:
    bind = op.get_bind()
    unexpected = bind.execute(sa.text(
        "SELECT count(*) FROM org_memberships "
        "WHERE role IS NULL OR upper(btrim(role::text)) "
        "NOT IN ('OWNER','ADMIN','MEMBER','VIEWER')"
    )).scalar_one()
    if unexpected:
        raise RuntimeError(
            "Refusing migration 034: org_memberships.role contains "
            f"{unexpected} unsupported value(s)."
        )

    existing_labels = _enum_labels(bind)
    if existing_labels is not None and existing_labels != ORGROLE_LABELS:
        raise RuntimeError(
            "Refusing migration 034: existing orgrole enum labels are incompatible."
        )
    orgrole = postgresql.ENUM(*ORGROLE_LABELS, name="orgrole")
    if existing_labels is None:
        orgrole.create(bind, checkfirst=False)

    column_type = bind.execute(sa.text(
        "SELECT udt_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='org_memberships' "
        "AND column_name='role'"
    )).scalar_one()
    if column_type == "orgrole":
        return
    if column_type != "varchar":
        raise RuntimeError(
            "Refusing migration 034: org_memberships.role is not VARCHAR or orgrole."
        )

    default = _normalized_default(_column_default(bind))
    if default is not None:
        op.alter_column("org_memberships", "role", server_default=None)
    op.alter_column(
        "org_memberships",
        "role",
        existing_type=sa.String(32),
        type_=orgrole,
        existing_nullable=False,
        postgresql_using="upper(btrim(role))::orgrole",
    )
    if default is not None:
        op.alter_column(
            "org_memberships",
            "role",
            server_default=sa.text(f"'{default}'::orgrole"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    default = _normalized_default(_column_default(bind))
    if default is not None:
        op.alter_column("org_memberships", "role", server_default=None)
    op.alter_column(
        "org_memberships",
        "role",
        existing_type=postgresql.ENUM(*ORGROLE_LABELS, name="orgrole"),
        type_=sa.String(32),
        existing_nullable=False,
        postgresql_using="lower(role::text)",
    )
    if default is not None:
        op.alter_column(
            "org_memberships",
            "role",
            server_default=sa.text(f"'{default.lower()}'::character varying"),
        )

    remaining_dependencies = bind.execute(sa.text(
        "SELECT count(*) FROM pg_attribute a "
        "JOIN pg_type t ON t.oid=a.atttypid "
        "WHERE t.typname='orgrole' AND a.attnum > 0 AND NOT a.attisdropped"
    )).scalar_one()
    if remaining_dependencies == 0:
        postgresql.ENUM(*ORGROLE_LABELS, name="orgrole").drop(
            bind, checkfirst=False)
