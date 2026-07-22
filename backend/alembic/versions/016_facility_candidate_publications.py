"""Add facility candidate publication audit table

Revision ID: 016
Revises: 015
Create Date: 2026-07-21
"""

from alembic import op
import sqlalchemy as sa

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def timestamp_columns() -> list[sa.Column]:
    return [
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
    ]


def id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), nullable=False)


def upgrade() -> None:
    op.create_table(
        "scraping_facility_candidate_publications",
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("facility_candidate_id", sa.String(length=36), nullable=False),
        sa.Column("final_facility_id", sa.String(length=36), nullable=True),
        sa.Column("normalization_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("metadata_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["facility_candidate_id"],
            ["scraping_facility_candidates.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["final_facility_id"],
            ["rehabilitation_facilities.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "facility_candidate_id",
            name="uq_facility_candidate_publication_candidate",
        ),
        sa.CheckConstraint(
            "status != 'published' OR final_facility_id IS NOT NULL",
            name="ck_facility_candidate_publication_published_facility",
        ),
    )
    for name, cols in {
        "ix_facility_candidate_publications_org": ["organization_id"],
        "ix_facility_candidate_publications_execution": ["execution_id"],
        "ix_facility_candidate_publications_candidate": ["facility_candidate_id"],
        "ix_facility_candidate_publications_facility": ["final_facility_id"],
        "ix_facility_candidate_publications_status": ["status"],
    }.items():
        op.create_index(name, "scraping_facility_candidate_publications", cols)


def downgrade() -> None:
    for name in [
        "ix_facility_candidate_publications_status",
        "ix_facility_candidate_publications_facility",
        "ix_facility_candidate_publications_candidate",
        "ix_facility_candidate_publications_execution",
        "ix_facility_candidate_publications_org",
    ]:
        op.drop_index(name, table_name="scraping_facility_candidate_publications")
    op.drop_table("scraping_facility_candidate_publications")
