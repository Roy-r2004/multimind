"""Add recall-upgrade schema for Maps census lifecycle and eligibility.

Revision ID: 031
Revises: 030
Create Date: 2026-08-01
"""

from alembic import op
import sqlalchemy as sa

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None

CELL_REGION_FK = "fk_maps_census_cells_region_id"


def _has_text(column):
    return sa.func.length(sa.func.trim(sa.func.coalesce(column, ""))) > 0


def _backfill_maps_places() -> None:
    bind = op.get_bind()
    maps_places = sa.table(
        "maps_places",
        sa.column("is_relevant", sa.Boolean()),
        sa.column("verification_verdict", sa.String(length=20)),
        sa.column("international_phone_number", sa.String(length=64)),
        sa.column("raw_website", sa.String(length=512)),
        sa.column("official_website", sa.String(length=512)),
        sa.column("lifecycle_status", sa.String(length=40)),
        sa.column("client_eligibility", sa.String(length=20)),
        sa.column("contact_status", sa.String(length=20)),
        sa.column("discovery_sources", sa.JSON()),
    )
    phone_present = _has_text(maps_places.c.international_phone_number)
    website_present = sa.or_(
        _has_text(maps_places.c.official_website),
        _has_text(maps_places.c.raw_website),
    )
    bind.execute(
        maps_places.update().values(
            lifecycle_status=sa.case(
                (
                    sa.and_(
                        maps_places.c.is_relevant.is_(True),
                        maps_places.c.verification_verdict == "confirmed",
                    ),
                    "confirmed_eligible",
                ),
                (
                    sa.and_(
                        maps_places.c.is_relevant.is_(True),
                        maps_places.c.verification_verdict == "unknown",
                    ),
                    "needs_review",
                ),
                (
                    sa.and_(
                        maps_places.c.is_relevant.is_(False),
                        maps_places.c.verification_verdict == "contradicted",
                    ),
                    "contradicted",
                ),
                (
                    sa.and_(
                        maps_places.c.is_relevant.is_(False),
                        maps_places.c.verification_verdict == "unknown",
                    ),
                    "needs_review",
                ),
                (maps_places.c.is_relevant.is_(True), "plausible"),
                (maps_places.c.is_relevant.is_(False), "unrelated"),
                else_="discovered",
            ),
            client_eligibility=sa.case(
                (
                    sa.and_(
                        maps_places.c.is_relevant.is_(True),
                        maps_places.c.verification_verdict == "confirmed",
                    ),
                    "eligible",
                ),
                (maps_places.c.verification_verdict == "unknown", "review"),
                (maps_places.c.is_relevant.is_(True), "review"),
                else_="excluded",
            ),
            contact_status=sa.case(
                (sa.and_(phone_present, website_present), "complete"),
                (phone_present, "phone_only"),
                (website_present, "website_only"),
                else_="missing",
            ),
            discovery_sources=["google_places"],
        )
    )


def upgrade() -> None:
    op.create_table(
        "maps_census_regions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(length=36),
            sa.ForeignKey("maps_census_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("region_name", sa.String(length=160), nullable=False),
        sa.Column("cells_planned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cells_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_places_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_unique_places_last_window", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plausible_providers_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "new_plausible_providers_last_window",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("duplicate_rate", sa.Float(), nullable=True),
        sa.Column("query_languages_used", sa.JSON(), nullable=True),
        sa.Column("provider_terms_used", sa.JSON(), nullable=True),
        sa.Column("saturation_status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_maps_census_regions_run_id", "maps_census_regions", ["run_id"])
    op.create_index(
        "ix_maps_census_regions_saturation_status",
        "maps_census_regions",
        ["saturation_status"],
    )

    with op.batch_alter_table("maps_census_runs") as batch:
        batch.add_column(sa.Column("country_profile", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column(
                "country_profile_status",
                sa.String(length=20),
                nullable=False,
                server_default="pending",
            )
        )
        batch.add_column(sa.Column("country_profile_error", sa.Text(), nullable=True))
        batch.add_column(sa.Column("funnel_metrics", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("saturation_summary", sa.JSON(), nullable=True))

    with op.batch_alter_table("maps_census_cells") as batch:
        batch.add_column(sa.Column("region_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("query_family", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("query_language", sa.String(length=32), nullable=True))
        batch.add_column(
            sa.Column("new_unique_places", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("new_plausible_places", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_foreign_key(
            CELL_REGION_FK,
            "maps_census_regions",
            ["region_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_maps_census_cells_region_id", ["region_id"], unique=False)

    with op.batch_alter_table("maps_places") as batch:
        batch.add_column(
            sa.Column("lifecycle_status", sa.String(length=40), nullable=False, server_default="discovered")
        )
        batch.add_column(
            sa.Column("client_eligibility", sa.String(length=20), nullable=False, server_default="excluded")
        )
        batch.add_column(sa.Column("operator_type", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("ownership_status", sa.String(length=40), nullable=True))
        batch.add_column(sa.Column("funding_type", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("facility_type", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("care_setting", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("organization_scope", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("operator_name", sa.String(length=512), nullable=True))
        batch.add_column(sa.Column("contact_status", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("addiction_focus_confirmed", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("medical_detox", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("residential_accommodation", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("operating_status", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("website_languages", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("classification_evidence", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("discovery_sources", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("source_record_ids", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("registry_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("classification_confidence", sa.Numeric(5, 4), nullable=True))
        batch.create_index("ix_maps_places_lifecycle_status", ["lifecycle_status"], unique=False)
        batch.create_index("ix_maps_places_client_eligibility", ["client_eligibility"], unique=False)
        batch.create_index(
            "ix_maps_places_run_lifecycle_status",
            ["run_id", "lifecycle_status"],
            unique=False,
        )
        batch.create_index(
            "ix_maps_places_run_client_eligibility",
            ["run_id", "client_eligibility"],
            unique=False,
        )

    _backfill_maps_places()


def downgrade() -> None:
    with op.batch_alter_table("maps_places") as batch:
        batch.drop_index("ix_maps_places_run_client_eligibility")
        batch.drop_index("ix_maps_places_run_lifecycle_status")
        batch.drop_index("ix_maps_places_client_eligibility")
        batch.drop_index("ix_maps_places_lifecycle_status")
        batch.drop_column("classification_confidence")
        batch.drop_column("registry_id")
        batch.drop_column("source_record_ids")
        batch.drop_column("discovery_sources")
        batch.drop_column("classification_evidence")
        batch.drop_column("website_languages")
        batch.drop_column("operating_status")
        batch.drop_column("residential_accommodation")
        batch.drop_column("medical_detox")
        batch.drop_column("addiction_focus_confirmed")
        batch.drop_column("contact_status")
        batch.drop_column("operator_name")
        batch.drop_column("organization_scope")
        batch.drop_column("care_setting")
        batch.drop_column("facility_type")
        batch.drop_column("funding_type")
        batch.drop_column("ownership_status")
        batch.drop_column("operator_type")
        batch.drop_column("client_eligibility")
        batch.drop_column("lifecycle_status")

    with op.batch_alter_table("maps_census_cells") as batch:
        batch.drop_index("ix_maps_census_cells_region_id")
        batch.drop_constraint(CELL_REGION_FK, type_="foreignkey")
        batch.drop_column("new_plausible_places")
        batch.drop_column("new_unique_places")
        batch.drop_column("query_language")
        batch.drop_column("query_family")
        batch.drop_column("region_id")

    with op.batch_alter_table("maps_census_runs") as batch:
        batch.drop_column("saturation_summary")
        batch.drop_column("funnel_metrics")
        batch.drop_column("country_profile_error")
        batch.drop_column("country_profile_status")
        batch.drop_column("country_profile")

    op.drop_index("ix_maps_census_regions_saturation_status", table_name="maps_census_regions")
    op.drop_index("ix_maps_census_regions_run_id", table_name="maps_census_regions")
    op.drop_table("maps_census_regions")
