"""Phase 4 Slice 1: discovery claim lifecycle + crawl graph schema.

Revision ID: 029
Revises: 028

Adds claim/lease/retry columns on scraping_source_discovery_queries, makes
candidate region_name nullable for countrywide results, and introduces
scraping_crawl_nodes / scraping_crawl_edges with composite org+execution
isolation for candidate→node and edge provenance links.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None

CRAWL_NODE_CLASSIFICATIONS = (
    "official_facility_site",
    "facility_profile",
    "directory",
    "registry",
    "government_source",
    "commercial_listing",
    "pdf",
    "social_profile",
    "supporting_source",
    "irrelevant",
    "unclassified",
)

CRAWL_EDGE_RELATIONSHIP_TYPES = (
    "directory_to_profile",
    "profile_to_official_site",
    "official_site_to_contact_page",
    "official_site_to_program_page",
    "official_site_to_location_page",
    "official_site_to_licensing_page",
    "official_site_to_evidence_page",
    "related_source",
    "discovered_link",
)

_CLASSIFICATION_IN = ", ".join(f"'{v}'" for v in CRAWL_NODE_CLASSIFICATIONS)
_RELATIONSHIP_IN = ", ".join(f"'{v}'" for v in CRAWL_EDGE_RELATIONSHIP_TYPES)

PENDING_ELIGIBILITY_WHERE = "status = 'pending'"
RUNNING_LEASE_WHERE = "status = 'running'"


def upgrade() -> None:
    with op.batch_alter_table("scraping_source_discovery_queries") as batch_op:
        # String(36) matches UUIDPrimaryKeyMixin / UuidFK project convention
        # (no native postgresql.UUID in this schema).
        batch_op.add_column(sa.Column("claim_token", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)
        )
        # Safe machine-readable codes only (e.g. provider_timeout); never raw
        # provider payloads, stack traces, or PII.
        batch_op.add_column(
            sa.Column("last_error_code", sa.String(length=80), nullable=True)
        )
        batch_op.add_column(
            sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_source_discovery_query_attempt_count",
            "attempt_count >= 0",
        )
        batch_op.create_check_constraint(
            "ck_source_discovery_query_lease_after_claim",
            "lease_expires_at IS NULL OR claimed_at IS NULL OR lease_expires_at > claimed_at",
        )
        # Composite FK target for crawl-edge discovery_query provenance.
        batch_op.create_unique_constraint(
            "uq_source_discovery_query_id_org_exec",
            ["id", "organization_id", "execution_id"],
        )
        # Pending claim eligibility: equality on org+execution, ORDER BY
        # priority/generation_ordinal, filter next_attempt_at at query time
        # (partial indexes cannot encode now()).
        batch_op.create_index(
            "ix_source_discovery_queries_pending_claim",
            [
                "organization_id",
                "execution_id",
                "priority",
                "generation_ordinal",
                "next_attempt_at",
            ],
            unique=False,
            postgresql_where=sa.text(PENDING_ELIGIBILITY_WHERE),
            sqlite_where=sa.text(PENDING_ELIGIBILITY_WHERE),
        )
        batch_op.create_index(
            "ix_source_discovery_queries_running_lease",
            ["organization_id", "execution_id", "lease_expires_at"],
            unique=False,
            postgresql_where=sa.text(RUNNING_LEASE_WHERE),
            sqlite_where=sa.text(RUNNING_LEASE_WHERE),
        )

    op.create_table(
        "scraping_crawl_nodes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        # Store sha256_hex(payload_dict) of the canonical URL identity — do not
        # double-canonicalize before hashing.
        sa.Column("canonical_url_hash", sa.String(length=64), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("source_classification", sa.String(length=64), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "execution_id",
            "canonical_url_hash",
            name="uq_crawl_node_org_exec_url_hash",
        ),
        # Composite uniqueness enables composite FKs (org/exec isolation).
        sa.UniqueConstraint(
            "id",
            "organization_id",
            "execution_id",
            name="uq_crawl_node_id_org_exec",
        ),
        sa.CheckConstraint(
            f"source_classification IN ({_CLASSIFICATION_IN})",
            name="ck_crawl_node_source_classification",
        ),
        sa.CheckConstraint(
            "length(trim(canonical_url)) > 0",
            name="ck_crawl_node_canonical_url_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(canonical_url_hash)) = 64",
            name="ck_crawl_node_canonical_url_hash_len",
        ),
    )
    op.create_index(
        "ix_crawl_nodes_org_execution",
        "scraping_crawl_nodes",
        ["organization_id", "execution_id"],
    )
    op.create_index("ix_crawl_nodes_hostname", "scraping_crawl_nodes", ["hostname"])
    op.create_index("ix_crawl_nodes_domain", "scraping_crawl_nodes", ["domain"])
    op.create_index(
        "ix_crawl_nodes_source_classification",
        "scraping_crawl_nodes",
        ["source_classification"],
    )

    with op.batch_alter_table("scraping_source_candidates") as batch_op:
        batch_op.alter_column(
            "region_name",
            existing_type=sa.String(length=160),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column("crawl_node_id", sa.String(length=36), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_source_candidate_id_org_exec",
            ["id", "organization_id", "execution_id"],
        )
        # Linking a node requires a concrete execution (MATCH SIMPLE would
        # otherwise skip composite FK checks when execution_id is NULL).
        batch_op.create_check_constraint(
            "ck_source_candidate_crawl_node_requires_execution",
            "crawl_node_id IS NULL OR execution_id IS NOT NULL",
        )
        # Composite ON DELETE SET NULL would null organization_id/execution_id —
        # forbidden. Use RESTRICT; clear crawl_node_id via UPDATE before node
        # delete. DEFERRABLE so execution-level CASCADE can delete siblings.
        batch_op.create_foreign_key(
            "fk_source_candidates_crawl_node_org_exec",
            "scraping_crawl_nodes",
            ["crawl_node_id", "organization_id", "execution_id"],
            ["id", "organization_id", "execution_id"],
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        )
        batch_op.create_index(
            "ix_source_candidates_crawl_node",
            ["crawl_node_id"],
            unique=False,
        )

    op.create_table(
        "scraping_crawl_edges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("from_node_id", sa.String(length=36), nullable=False),
        sa.Column("to_node_id", sa.String(length=36), nullable=False),
        sa.Column("relationship_type", sa.String(length=64), nullable=False),
        sa.Column("discovery_query_id", sa.String(length=36), nullable=True),
        sa.Column("source_candidate_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["from_node_id", "organization_id", "execution_id"],
            [
                "scraping_crawl_nodes.id",
                "scraping_crawl_nodes.organization_id",
                "scraping_crawl_nodes.execution_id",
            ],
            name="fk_crawl_edges_from_node_org_exec",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["to_node_id", "organization_id", "execution_id"],
            [
                "scraping_crawl_nodes.id",
                "scraping_crawl_nodes.organization_id",
                "scraping_crawl_nodes.execution_id",
            ],
            name="fk_crawl_edges_to_node_org_exec",
            ondelete="CASCADE",
        ),
        # Provenance composite FKs: RESTRICT (not SET NULL) so ownership columns
        # are never nulled. Clear discovery_query_id / source_candidate_id via
        # UPDATE before deleting the referenced row.
        sa.ForeignKeyConstraint(
            ["discovery_query_id", "organization_id", "execution_id"],
            [
                "scraping_source_discovery_queries.id",
                "scraping_source_discovery_queries.organization_id",
                "scraping_source_discovery_queries.execution_id",
            ],
            name="fk_crawl_edges_discovery_query_org_exec",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["source_candidate_id", "organization_id", "execution_id"],
            [
                "scraping_source_candidates.id",
                "scraping_source_candidates.organization_id",
                "scraping_source_candidates.execution_id",
            ],
            name="fk_crawl_edges_source_candidate_org_exec",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "execution_id",
            "from_node_id",
            "to_node_id",
            "relationship_type",
            name="uq_crawl_edge_org_exec_rel",
        ),
        sa.CheckConstraint(
            "from_node_id <> to_node_id",
            name="ck_crawl_edge_no_self_loop",
        ),
        sa.CheckConstraint(
            f"relationship_type IN ({_RELATIONSHIP_IN})",
            name="ck_crawl_edge_relationship_type",
        ),
    )
    op.create_index(
        "ix_crawl_edges_org_execution",
        "scraping_crawl_edges",
        ["organization_id", "execution_id"],
    )
    op.create_index(
        "ix_crawl_edges_from_node", "scraping_crawl_edges", ["from_node_id"]
    )
    op.create_index("ix_crawl_edges_to_node", "scraping_crawl_edges", ["to_node_id"])


def _downgrade_preflight() -> None:
    """Fail closed before restoring NOT NULL region_name when NULL rows exist."""
    bind = op.get_bind()
    null_regions = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM scraping_source_candidates WHERE region_name IS NULL"
        )
    ).scalar()
    if int(null_regions or 0) > 0:
        raise RuntimeError(
            "Cannot downgrade migration 029: scraping_source_candidates contains "
            "NULL region_name rows (countrywide candidates). No schema changes were applied."
        )


def downgrade() -> None:
    _downgrade_preflight()

    op.drop_index("ix_crawl_edges_to_node", table_name="scraping_crawl_edges")
    op.drop_index("ix_crawl_edges_from_node", table_name="scraping_crawl_edges")
    op.drop_index("ix_crawl_edges_org_execution", table_name="scraping_crawl_edges")
    op.drop_table("scraping_crawl_edges")

    with op.batch_alter_table("scraping_source_candidates") as batch_op:
        batch_op.drop_index("ix_source_candidates_crawl_node")
        batch_op.drop_constraint(
            "fk_source_candidates_crawl_node_org_exec", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "ck_source_candidate_crawl_node_requires_execution", type_="check"
        )
        batch_op.drop_constraint("uq_source_candidate_id_org_exec", type_="unique")
        batch_op.drop_column("crawl_node_id")
        batch_op.alter_column(
            "region_name",
            existing_type=sa.String(length=160),
            nullable=False,
        )

    op.drop_index(
        "ix_crawl_nodes_source_classification", table_name="scraping_crawl_nodes"
    )
    op.drop_index("ix_crawl_nodes_domain", table_name="scraping_crawl_nodes")
    op.drop_index("ix_crawl_nodes_hostname", table_name="scraping_crawl_nodes")
    op.drop_index("ix_crawl_nodes_org_execution", table_name="scraping_crawl_nodes")
    op.drop_table("scraping_crawl_nodes")

    with op.batch_alter_table("scraping_source_discovery_queries") as batch_op:
        batch_op.drop_index("ix_source_discovery_queries_running_lease")
        batch_op.drop_index("ix_source_discovery_queries_pending_claim")
        batch_op.drop_constraint("uq_source_discovery_query_id_org_exec", type_="unique")
        batch_op.drop_constraint(
            "ck_source_discovery_query_lease_after_claim", type_="check"
        )
        batch_op.drop_constraint(
            "ck_source_discovery_query_attempt_count", type_="check"
        )
        batch_op.drop_column("last_error_at")
        batch_op.drop_column("last_error_code")
        batch_op.drop_column("next_attempt_at")
        batch_op.drop_column("last_attempt_at")
        batch_op.drop_column("attempt_count")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("claimed_at")
        batch_op.drop_column("claim_token")
