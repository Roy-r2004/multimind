"""Package B publication, export, and finalization lifecycle.

Revision ID: 035
Revises: 034
"""

from alembic import op
import sqlalchemy as sa

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None

OLD_KINDS = (
    "'prepare_document','extract_chunk','verify_candidate',"
    "'deduplicate_candidate'"
)
NEW_KINDS = (
    OLD_KINDS
    + ",'publish_candidate','generate_execution_export','finalize_execution'"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_facility_phase_job_kind",
        "scraping_facility_phase_work_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_facility_phase_job_kind",
        "scraping_facility_phase_work_jobs",
        f"work_kind IN ({NEW_KINDS})",
    )
    op.create_table(
        "scraping_execution_exports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("organization_id", sa.String(36), nullable=False),
        sa.Column("execution_id", sa.String(36), nullable=False),
        sa.Column("export_kind", sa.String(32), nullable=False, server_default="xlsx"),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("filename", sa.String(255)),
        sa.Column("content_type", sa.String(120)),
        sa.Column("artifact_sha256", sa.String(64)),
        sa.Column("artifact_bytes", sa.LargeBinary()),
        sa.Column("failure_classification", sa.String(80)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "organization_id", "execution_id", "export_kind",
            name="uq_scraping_execution_export_kind",
        ),
        sa.UniqueConstraint(
            "id", "organization_id", "execution_id",
            name="uq_scraping_execution_export_owner",
        ),
        sa.CheckConstraint(
            "status IN ('pending','succeeded','failed')",
            name="ck_scraping_execution_export_status",
        ),
        sa.CheckConstraint(
            "status != 'succeeded' OR "
            "(artifact_sha256 IS NOT NULL AND artifact_bytes IS NOT NULL "
            "AND filename IS NOT NULL AND completed_at IS NOT NULL)",
            name="ck_scraping_execution_export_succeeded",
        ),
    )
    op.create_index(
        "ix_scraping_execution_exports_execution",
        "scraping_execution_exports",
        ["organization_id", "execution_id", "status"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    package_b_jobs = bind.execute(sa.text(
        "SELECT count(*) FROM scraping_facility_phase_work_jobs "
        "WHERE work_kind IN "
        "('publish_candidate','generate_execution_export','finalize_execution')"
    )).scalar_one()
    exports = bind.execute(sa.text(
        "SELECT count(*) FROM scraping_execution_exports"
    )).scalar_one()
    if package_b_jobs or exports:
        raise RuntimeError(
            "Refusing migration 035 downgrade: Package B jobs or exports exist."
        )
    op.drop_index(
        "ix_scraping_execution_exports_execution",
        table_name="scraping_execution_exports",
    )
    op.drop_table("scraping_execution_exports")
    op.drop_constraint(
        "ck_facility_phase_job_kind",
        "scraping_facility_phase_work_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_facility_phase_job_kind",
        "scraping_facility_phase_work_jobs",
        f"work_kind IN ({OLD_KINDS})",
    )
