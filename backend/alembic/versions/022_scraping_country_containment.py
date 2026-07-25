"""Country containment + location/contact gate stubs (Phases A + B schema)

Revision ID: 022
Revises: 021
Create Date: 2026-07-25
"""

from alembic import op
import sqlalchemy as sa

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rehabilitation_facilities",
        sa.Column(
            "country_containment_status",
            sa.String(length=40),
            nullable=False,
            server_default="legacy_unassessed",
        ),
    )
    op.add_column(
        "rehabilitation_facilities",
        sa.Column("country_containment_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "rehabilitation_facilities",
        sa.Column("country_containment_signals_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "rehabilitation_facilities",
        sa.Column(
            "publication_class",
            sa.String(length=40),
            nullable=False,
            server_default="review_required",
        ),
    )
    op.create_index(
        "ix_rehab_facilities_country_containment_status",
        "rehabilitation_facilities",
        ["country_containment_status"],
    )
    op.create_index(
        "ix_rehab_facilities_publication_class",
        "rehabilitation_facilities",
        ["publication_class"],
    )

    op.add_column(
        "rehabilitation_facility_locations",
        sa.Column(
            "country_containment_status",
            sa.String(length=40),
            nullable=False,
            server_default="legacy_unassessed",
        ),
    )
    op.add_column(
        "rehabilitation_facility_locations",
        sa.Column("country_containment_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "rehabilitation_facility_locations",
        sa.Column("country_containment_signals_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "rehabilitation_facility_locations",
        sa.Column(
            "location_completeness_status",
            sa.String(length=40),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "rehabilitation_facility_locations",
        sa.Column("location_gap_reason", sa.String(length=80), nullable=True),
    )

    op.add_column(
        "rehabilitation_facility_contacts",
        sa.Column("location_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "rehabilitation_facility_contacts",
        sa.Column(
            "contact_discovery_status",
            sa.String(length=60),
            nullable=False,
            server_default="found_unverified",
        ),
    )
    op.create_foreign_key(
        "fk_rehab_contacts_location_id",
        "rehabilitation_facility_contacts",
        "rehabilitation_facility_locations",
        ["location_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_rehab_contacts_location_id",
        "rehabilitation_facility_contacts",
        ["location_id"],
    )
    op.create_index(
        "ix_rehab_contacts_discovery_status",
        "rehabilitation_facility_contacts",
        ["contact_discovery_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_rehab_contacts_discovery_status", table_name="rehabilitation_facility_contacts")
    op.drop_index("ix_rehab_contacts_location_id", table_name="rehabilitation_facility_contacts")
    op.drop_constraint("fk_rehab_contacts_location_id", "rehabilitation_facility_contacts", type_="foreignkey")
    op.drop_column("rehabilitation_facility_contacts", "contact_discovery_status")
    op.drop_column("rehabilitation_facility_contacts", "location_id")

    op.drop_column("rehabilitation_facility_locations", "location_gap_reason")
    op.drop_column("rehabilitation_facility_locations", "location_completeness_status")
    op.drop_column("rehabilitation_facility_locations", "country_containment_signals_json")
    op.drop_column("rehabilitation_facility_locations", "country_containment_reason")
    op.drop_column("rehabilitation_facility_locations", "country_containment_status")

    op.drop_index("ix_rehab_facilities_publication_class", table_name="rehabilitation_facilities")
    op.drop_index("ix_rehab_facilities_country_containment_status", table_name="rehabilitation_facilities")
    op.drop_column("rehabilitation_facilities", "publication_class")
    op.drop_column("rehabilitation_facilities", "country_containment_signals_json")
    op.drop_column("rehabilitation_facilities", "country_containment_reason")
    op.drop_column("rehabilitation_facilities", "country_containment_status")
