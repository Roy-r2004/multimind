"""Add facility country validation audit fields

Revision ID: 018
Revises: 017
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


TABLE = "scraping_facility_candidate_publications"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column("target_country_code", sa.String(length=2), nullable=True))
    op.add_column(TABLE, sa.Column("detected_country_code", sa.String(length=2), nullable=True))
    op.add_column(TABLE, sa.Column("country_validation_outcome", sa.String(length=40), nullable=True))
    op.add_column(TABLE, sa.Column("country_validation_confidence", sa.Numeric(5, 4), nullable=True))
    op.add_column(
        TABLE,
        sa.Column(
            "country_validation_reasons_json",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
    )
    op.add_column(TABLE, sa.Column("country_validation_reason", sa.String(length=500), nullable=True))
    op.add_column(
        TABLE,
        sa.Column("country_validation_decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column(
            "country_validation_auto_decided",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE scraping_facility_candidate_publications
            SET
                target_country_code = (
                    SELECT scraping_executions.country_code
                    FROM scraping_executions
                    WHERE scraping_executions.id = scraping_facility_candidate_publications.execution_id
                ),
                detected_country_code = (
                    SELECT rehabilitation_facilities.country_code
                    FROM rehabilitation_facilities
                    WHERE rehabilitation_facilities.id =
                        scraping_facility_candidate_publications.final_facility_id
                ),
                country_validation_outcome = 'needs_review',
                country_validation_confidence = 0.0000,
                country_validation_reason = 'legacy_publication_not_country_validated',
                country_validation_decided_at = COALESCE(completed_at, published_at, updated_at, created_at),
                country_validation_auto_decided = FALSE
            """
        )
    )

    op.create_index(
        "ix_facility_candidate_publications_country_validation",
        TABLE,
        ["country_validation_outcome"],
    )
    op.create_index(
        "ix_facility_candidate_publications_target_country",
        TABLE,
        ["target_country_code"],
    )
    op.create_index(
        "ix_facility_candidate_publications_detected_country",
        TABLE,
        ["detected_country_code"],
    )


def downgrade() -> None:
    for name in [
        "ix_facility_candidate_publications_detected_country",
        "ix_facility_candidate_publications_target_country",
        "ix_facility_candidate_publications_country_validation",
    ]:
        op.drop_index(name, table_name=TABLE)
    op.drop_column(TABLE, "country_validation_auto_decided")
    op.drop_column(TABLE, "country_validation_decided_at")
    op.drop_column(TABLE, "country_validation_reason")
    op.drop_column(TABLE, "country_validation_reasons_json")
    op.drop_column(TABLE, "country_validation_confidence")
    op.drop_column(TABLE, "country_validation_outcome")
    op.drop_column(TABLE, "detected_country_code")
    op.drop_column(TABLE, "target_country_code")
