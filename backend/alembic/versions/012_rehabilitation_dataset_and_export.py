"""Add rehabilitation dataset and export tables

Revision ID: 012
Revises: 011
Create Date: 2026-07-15
"""

from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def timestamp_columns() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def id_column() -> sa.Column:
    return sa.Column("id", sa.String(length=36), nullable=False)


def confidence_check(name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint("confidence_score >= 0 AND confidence_score <= 1", name=name)


def upgrade() -> None:
    op.create_table(
        "rehabilitation_facilities",
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("stable_key", sa.String(length=160), nullable=False),
        sa.Column("canonical_name", sa.String(length=255), nullable=False),
        sa.Column("original_language_name", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("facility_type", sa.String(length=80), nullable=False),
        sa.Column("organization_type", sa.String(length=80), nullable=False),
        sa.Column("operational_status", sa.String(length=80), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("country_name", sa.String(length=120), nullable=False),
        sa.Column("primary_region", sa.String(length=160), nullable=True),
        sa.Column("primary_city", sa.String(length=160), nullable=True),
        sa.Column("primary_address", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("primary_website", sa.String(length=512), nullable=True),
        sa.Column("verification_status", sa.String(length=80), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("duplicate_status", sa.String(length=80), nullable=False),
        sa.Column("human_review_status", sa.String(length=80), nullable=False),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", "stable_key", name="uq_rehab_facility_execution_key"),
        confidence_check("ck_rehab_facility_confidence_score"),
    )
    op.create_index("ix_rehab_facilities_execution_id", "rehabilitation_facilities", ["execution_id"])
    op.create_index("ix_rehab_facilities_organization_id", "rehabilitation_facilities", ["organization_id"])
    op.create_index("ix_rehab_facilities_verification_status", "rehabilitation_facilities", ["verification_status"])
    op.create_index("ix_rehab_facilities_country_region", "rehabilitation_facilities", ["country_code", "primary_region"])

    op.create_table(
        "rehabilitation_sources",
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("coverage_cell_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("original_url", sa.String(length=512), nullable=False),
        sa.Column("canonical_url", sa.String(length=512), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("source_category", sa.String(length=120), nullable=False),
        sa.Column("discovery_query", sa.Text(), nullable=True),
        sa.Column("page_title", sa.String(length=255), nullable=True),
        sa.Column("language_code", sa.String(length=16), nullable=True),
        sa.Column("region", sa.String(length=160), nullable=True),
        sa.Column("fetch_status", sa.String(length=80), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.true(), nullable=False),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["coverage_cell_id"], ["scraping_coverage_cells.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["task_id"], ["scraping_tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", "canonical_url", name="uq_rehab_source_execution_url"),
    )
    op.create_index("ix_rehab_sources_execution_id", "rehabilitation_sources", ["execution_id"])
    op.create_index("ix_rehab_sources_coverage_cell_id", "rehabilitation_sources", ["coverage_cell_id"])
    op.create_index("ix_rehab_sources_task_id", "rehabilitation_sources", ["task_id"])
    op.create_index("ix_rehab_sources_fetch_status", "rehabilitation_sources", ["fetch_status"])

    op.create_table(
        "rehabilitation_facility_aliases",
        sa.Column("facility_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("language_code", sa.String(length=16), nullable=True),
        sa.Column("alias_type", sa.String(length=40), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        id_column(),
        sa.ForeignKeyConstraint(["facility_id"], ["rehabilitation_facilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "name", "alias_type", name="uq_rehab_alias_facility_name_type"),
    )
    op.create_index("ix_rehab_aliases_facility_id", "rehabilitation_facility_aliases", ["facility_id"])

    op.create_table(
        "rehabilitation_facility_locations",
        sa.Column("facility_id", sa.String(length=36), nullable=False),
        sa.Column("location_type", sa.String(length=60), nullable=False),
        sa.Column("location_name", sa.String(length=255), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("country_name", sa.String(length=120), nullable=False),
        sa.Column("region", sa.String(length=160), nullable=True),
        sa.Column("district", sa.String(length=160), nullable=True),
        sa.Column("city", sa.String(length=160), nullable=True),
        sa.Column("area", sa.String(length=160), nullable=True),
        sa.Column("full_address", sa.Text(), nullable=True),
        sa.Column("postal_code", sa.String(length=40), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("verification_status", sa.String(length=80), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.true(), nullable=False),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["facility_id"], ["rehabilitation_facilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "location_type", "location_name", name="uq_rehab_location_identity"),
        confidence_check("ck_rehab_location_confidence_score"),
    )
    op.create_index("ix_rehab_locations_facility_id", "rehabilitation_facility_locations", ["facility_id"])

    op.create_table(
        "rehabilitation_facility_contacts",
        sa.Column("facility_id", sa.String(length=36), nullable=False),
        sa.Column("contact_type", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=True),
        sa.Column("value", sa.String(length=512), nullable=False),
        sa.Column("normalized_value", sa.String(length=512), nullable=True),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("available_24_7", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("verification_status", sa.String(length=80), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        id_column(),
        sa.ForeignKeyConstraint(["facility_id"], ["rehabilitation_facilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "contact_type", "value", name="uq_rehab_contact_facility_type_value"),
        confidence_check("ck_rehab_contact_confidence_score"),
    )
    op.create_index("ix_rehab_contacts_facility_id", "rehabilitation_facility_contacts", ["facility_id"])
    op.create_index("ix_rehab_contacts_type", "rehabilitation_facility_contacts", ["contact_type"])

    op.create_table(
        "rehabilitation_facility_attributes",
        sa.Column("facility_id", sa.String(length=36), nullable=False),
        sa.Column("attribute_group", sa.String(length=80), nullable=False),
        sa.Column("attribute_key", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("value_type", sa.String(length=40), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_boolean", sa.Boolean(), nullable=True),
        sa.Column("value_number", sa.Numeric(12, 2), nullable=True),
        sa.Column("value_unit", sa.String(length=40), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("period", sa.String(length=40), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("verification_status", sa.String(length=80), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.true(), nullable=False),
        id_column(),
        *timestamp_columns(),
        sa.ForeignKeyConstraint(["facility_id"], ["rehabilitation_facilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "attribute_group", "attribute_key", name="uq_rehab_attribute_identity"),
        confidence_check("ck_rehab_attribute_confidence_score"),
    )
    op.create_index("ix_rehab_attributes_facility_id", "rehabilitation_facility_attributes", ["facility_id"])
    op.create_index("ix_rehab_attributes_group", "rehabilitation_facility_attributes", ["attribute_group"])

    op.create_table(
        "rehabilitation_facility_staff",
        sa.Column("facility_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=160), nullable=False),
        sa.Column("specialty", sa.String(length=160), nullable=True),
        sa.Column("credentials", sa.String(length=255), nullable=True),
        sa.Column("public_profile_url", sa.String(length=512), nullable=True),
        sa.Column("verification_status", sa.String(length=80), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        id_column(),
        sa.ForeignKeyConstraint(["facility_id"], ["rehabilitation_facilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "name", "role", name="uq_rehab_staff_facility_name_role"),
        confidence_check("ck_rehab_staff_confidence_score"),
    )
    op.create_index("ix_rehab_staff_facility_id", "rehabilitation_facility_staff", ["facility_id"])

    op.create_table(
        "rehabilitation_facility_licenses",
        sa.Column("facility_id", sa.String(length=36), nullable=False),
        sa.Column("record_type", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("issuing_authority", sa.String(length=255), nullable=True),
        sa.Column("identifier", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=80), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("verification_status", sa.String(length=80), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        id_column(),
        sa.ForeignKeyConstraint(["facility_id"], ["rehabilitation_facilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "record_type", "identifier", name="uq_rehab_license_identity"),
        confidence_check("ck_rehab_license_confidence_score"),
    )
    op.create_index("ix_rehab_licenses_facility_id", "rehabilitation_facility_licenses", ["facility_id"])

    op.create_table(
        "rehabilitation_facility_operating_hours",
        sa.Column("facility_id", sa.String(length=36), nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=False),
        sa.Column("opens_at", sa.Time(), nullable=True),
        sa.Column("closes_at", sa.Time(), nullable=True),
        sa.Column("is_closed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_24_hours", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        id_column(),
        sa.ForeignKeyConstraint(["facility_id"], ["rehabilitation_facilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "day_of_week", name="uq_rehab_hours_facility_day"),
    )
    op.create_index("ix_rehab_hours_facility_id", "rehabilitation_facility_operating_hours", ["facility_id"])

    op.create_table(
        "rehabilitation_facility_source_links",
        sa.Column("facility_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("relationship_type", sa.String(length=80), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        id_column(),
        sa.ForeignKeyConstraint(["facility_id"], ["rehabilitation_facilities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["rehabilitation_sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "source_id", "relationship_type", name="uq_rehab_source_link_identity"),
    )
    op.create_index("ix_rehab_source_links_facility_id", "rehabilitation_facility_source_links", ["facility_id"])
    op.create_index("ix_rehab_source_links_source_id", "rehabilitation_facility_source_links", ["source_id"])

    op.create_table(
        "rehabilitation_field_evidence",
        sa.Column("facility_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=True),
        sa.Column("field_path", sa.String(length=255), nullable=False),
        sa.Column("extracted_value", sa.String(length=512), nullable=True),
        sa.Column("evidence_text", sa.String(length=1000), nullable=True),
        sa.Column("page_title", sa.String(length=255), nullable=True),
        sa.Column("source_url_snapshot", sa.String(length=512), nullable=True),
        sa.Column("language_code", sa.String(length=16), nullable=True),
        sa.Column("extraction_method", sa.String(length=80), nullable=False),
        sa.Column("verification_status", sa.String(length=80), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        id_column(),
        sa.ForeignKeyConstraint(["facility_id"], ["rehabilitation_facilities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["rehabilitation_sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "source_id", "field_path", name="uq_rehab_evidence_field_source"),
        confidence_check("ck_rehab_evidence_confidence_score"),
    )
    op.create_index("ix_rehab_evidence_facility_id", "rehabilitation_field_evidence", ["facility_id"])
    op.create_index("ix_rehab_evidence_source_id", "rehabilitation_field_evidence", ["source_id"])

    op.create_table(
        "rehabilitation_possible_duplicates",
        sa.Column("execution_id", sa.String(length=36), nullable=False),
        sa.Column("left_facility_id", sa.String(length=36), nullable=False),
        sa.Column("right_facility_id", sa.String(length=36), nullable=False),
        sa.Column("match_score", sa.Numeric(5, 4), nullable=False),
        sa.Column("matching_reasons", sa.Text(), nullable=True),
        sa.Column("resolution_status", sa.String(length=80), nullable=False),
        sa.Column("resolved_facility_id", sa.String(length=36), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        id_column(),
        sa.ForeignKeyConstraint(["execution_id"], ["scraping_executions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["left_facility_id"], ["rehabilitation_facilities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["right_facility_id"], ["rehabilitation_facilities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["resolved_facility_id"], ["rehabilitation_facilities.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", "left_facility_id", "right_facility_id", name="uq_rehab_duplicate_pair"),
        sa.CheckConstraint("left_facility_id < right_facility_id", name="ck_rehab_duplicate_ordered_pair"),
        sa.CheckConstraint("match_score >= 0 AND match_score <= 1", name="ck_rehab_duplicate_match_score"),
    )
    op.create_index("ix_rehab_duplicates_execution_id", "rehabilitation_possible_duplicates", ["execution_id"])
    op.create_index("ix_rehab_duplicates_left_facility_id", "rehabilitation_possible_duplicates", ["left_facility_id"])
    op.create_index("ix_rehab_duplicates_right_facility_id", "rehabilitation_possible_duplicates", ["right_facility_id"])

    op.create_table(
        "rehabilitation_unresolved_fields",
        sa.Column("facility_id", sa.String(length=36), nullable=False),
        sa.Column("field_path", sa.String(length=255), nullable=False),
        sa.Column("unresolved_status", sa.String(length=80), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("recommended_follow_up", sa.Text(), nullable=True),
        sa.Column("source_id", sa.String(length=36), nullable=True),
        sa.Column("is_mock", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        id_column(),
        sa.ForeignKeyConstraint(["facility_id"], ["rehabilitation_facilities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["rehabilitation_sources.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("facility_id", "field_path", "unresolved_status", name="uq_rehab_unresolved_identity"),
    )
    op.create_index("ix_rehab_unresolved_facility_id", "rehabilitation_unresolved_fields", ["facility_id"])
    op.create_index("ix_rehab_unresolved_source_id", "rehabilitation_unresolved_fields", ["source_id"])
    op.create_index("ix_rehab_unresolved_status", "rehabilitation_unresolved_fields", ["unresolved_status"])


def downgrade() -> None:
    for table in [
        "rehabilitation_unresolved_fields",
        "rehabilitation_possible_duplicates",
        "rehabilitation_field_evidence",
        "rehabilitation_facility_source_links",
        "rehabilitation_facility_operating_hours",
        "rehabilitation_facility_licenses",
        "rehabilitation_facility_staff",
        "rehabilitation_facility_attributes",
        "rehabilitation_facility_contacts",
        "rehabilitation_facility_locations",
        "rehabilitation_facility_aliases",
        "rehabilitation_sources",
        "rehabilitation_facilities",
    ]:
        op.drop_table(table)
