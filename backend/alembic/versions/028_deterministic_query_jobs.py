"""Widen discovery queries for persistent Step 3 deterministic jobs.

Revision ID: 028
Revises: 027

Makes provider/requested_at/region_name nullable for queued plan-backed jobs,
adds Step 3 identity/lifecycle columns, backfills historical rows safely, and
changes query_text to Text so unbounded v2 Cartesian compositions are not
silently truncated.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None

SCOPE_CHECK = (
    "("
    "(scope_level = 'countrywide' AND region_name IS NULL AND important_city IS NULL) OR "
    "(scope_level = 'region' AND region_name IS NOT NULL AND important_city IS NULL) OR "
    "(scope_level = 'city' AND region_name IS NOT NULL AND important_city IS NOT NULL)"
    ")"
)

# Historical rows may leave both hash fields NULL. Plan-backed rows must carry
# fingerprint, plan hash, and execution identity together (no partial population).
PLAN_BACKED_PROVENANCE_CHECK = (
    "("
    "(query_job_fingerprint IS NULL AND plan_hash_snapshot IS NULL) OR "
    "(query_job_fingerprint IS NOT NULL AND plan_hash_snapshot IS NOT NULL "
    "AND execution_id IS NOT NULL)"
    ")"
)


def upgrade() -> None:
    with op.batch_alter_table("scraping_source_discovery_queries") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=sa.String(length=64),
            nullable=True,
        )
        batch_op.alter_column(
            "requested_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
        batch_op.alter_column(
            "region_name",
            existing_type=sa.String(length=160),
            nullable=True,
        )
        batch_op.alter_column(
            "query_text",
            existing_type=sa.String(length=512),
            type_=sa.Text(),
            existing_nullable=False,
        )
        batch_op.add_column(sa.Column("purpose", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("priority", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("discovery_round", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("generation_ordinal", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("query_job_fingerprint", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("plan_hash_snapshot", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("scope_level", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("important_city", sa.String(length=160), nullable=True))

    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        purpose_expr = "NULLIF(TRIM(metadata_json->>'purpose'), '')"
    else:
        purpose_expr = "NULLIF(TRIM(CAST(json_extract(metadata_json, '$.purpose') AS CHAR)), '')"

    op.execute(
        sa.text(
            f"""
            UPDATE scraping_source_discovery_queries
            SET
              purpose = COALESCE({purpose_expr}, 'legacy_source_discovery'),
              priority = 500,
              discovery_round = 1,
              generation_ordinal = 0,
              scope_level = CASE
                WHEN region_name IS NULL OR TRIM(region_name) = '' THEN 'countrywide'
                ELSE 'region'
              END,
              important_city = NULL,
              plan_hash_snapshot = NULL,
              query_job_fingerprint = NULL
            WHERE purpose IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE scraping_source_discovery_queries
            SET region_name = NULL
            WHERE scope_level = 'countrywide'
            """
        )
    )

    with op.batch_alter_table("scraping_source_discovery_queries") as batch_op:
        batch_op.alter_column("purpose", existing_type=sa.String(length=80), nullable=False)
        batch_op.alter_column("priority", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("discovery_round", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("generation_ordinal", existing_type=sa.Integer(), nullable=False)
        batch_op.alter_column("scope_level", existing_type=sa.String(length=32), nullable=False)
        batch_op.create_check_constraint(
            "ck_source_discovery_query_scope_level",
            SCOPE_CHECK,
        )
        batch_op.create_check_constraint(
            "ck_source_discovery_query_discovery_round",
            "discovery_round >= 1",
        )
        batch_op.create_check_constraint(
            "ck_source_discovery_query_priority",
            "priority >= 0",
        )
        batch_op.create_check_constraint(
            "ck_source_discovery_query_generation_ordinal",
            "generation_ordinal >= 0",
        )
        batch_op.create_check_constraint(
            "ck_source_discovery_query_plan_backed_provenance",
            PLAN_BACKED_PROVENANCE_CHECK,
        )
        batch_op.create_index(
            "ix_source_discovery_queries_round",
            ["execution_id", "discovery_round", "priority", "generation_ordinal"],
            unique=False,
        )
        # Unique index permits multiple historical NULL fingerprints.
        batch_op.create_unique_constraint(
            "uq_source_discovery_query_fingerprint",
            ["organization_id", "execution_id", "query_job_fingerprint"],
        )


def _downgrade_preflight() -> None:
    """Fail closed before any destructive schema restore when Step 3B rows exist."""
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        length_expr = "char_length(query_text)"
    else:
        length_expr = "length(query_text)"

    incompatible = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*) FROM scraping_source_discovery_queries
            WHERE provider IS NULL
               OR requested_at IS NULL
               OR region_name IS NULL
               OR query_job_fingerprint IS NOT NULL
               OR {length_expr} > 512
            """
        )
    ).scalar()
    if int(incompatible or 0) > 0:
        raise RuntimeError(
            "Cannot downgrade migration 028: scraping_source_discovery_queries contains "
            "rows incompatible with the pre-028 schema (Step 3B plan-backed fingerprints, "
            "NULL provider, NULL requested_at, NULL region_name, or query_text longer than "
            "512 characters). No schema changes were applied."
        )


def downgrade() -> None:
    _downgrade_preflight()

    with op.batch_alter_table("scraping_source_discovery_queries") as batch_op:
        batch_op.drop_constraint("uq_source_discovery_query_fingerprint", type_="unique")
        batch_op.drop_index("ix_source_discovery_queries_round")
        batch_op.drop_constraint(
            "ck_source_discovery_query_plan_backed_provenance", type_="check"
        )
        batch_op.drop_constraint("ck_source_discovery_query_generation_ordinal", type_="check")
        batch_op.drop_constraint("ck_source_discovery_query_priority", type_="check")
        batch_op.drop_constraint("ck_source_discovery_query_discovery_round", type_="check")
        batch_op.drop_constraint("ck_source_discovery_query_scope_level", type_="check")
        batch_op.drop_column("important_city")
        batch_op.drop_column("scope_level")
        batch_op.drop_column("plan_hash_snapshot")
        batch_op.drop_column("query_job_fingerprint")
        batch_op.drop_column("generation_ordinal")
        batch_op.drop_column("discovery_round")
        batch_op.drop_column("priority")
        batch_op.drop_column("purpose")
        batch_op.alter_column(
            "query_text",
            existing_type=sa.Text(),
            type_=sa.String(length=512),
            existing_nullable=False,
        )
        batch_op.alter_column(
            "region_name",
            existing_type=sa.String(length=160),
            nullable=False,
        )
        batch_op.alter_column(
            "provider",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.alter_column(
            "requested_at",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )
