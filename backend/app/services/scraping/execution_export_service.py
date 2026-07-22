"""Excel workbook export for persisted rehabilitation execution datasets."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, time
from decimal import Decimal
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import AuthContext
from app.core.exceptions import ConflictError, NotFoundError
from app.db.models import (
    RehabilitationFacility,
    RehabilitationFacilityAttribute,
    RehabilitationFacilityContact,
    RehabilitationPossibleDuplicate,
    RehabilitationSource,
    ScrapingCoverageCell,
    ScrapingExecution,
    ScrapingExecutionAgent,
    ScrapingExecutionStatus,
    ScrapingMission,
    ScrapingTask,
)
from app.services.scraping.execution_outcome import coverage_gap_count, execution_outcome_label
from app.services.scraping.mock_facility_generator import SOCIAL_CONTACT_TYPES, VERIFIED_STATUS

MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TERMINAL_STATUSES = {
    ScrapingExecutionStatus.COMPLETED,
    ScrapingExecutionStatus.FAILED,
    ScrapingExecutionStatus.CANCELLED,
}
SHEET_ORDER = [
    "Rehab Centers",
    "Locations",
    "Contacts",
    "Treatment Services",
    "Programs",
    "Populations Served",
    "Admissions and Eligibility",
    "Pricing and Payment",
    "Staff",
    "Licenses and Accreditations",
    "Amenities",
    "Operating Hours",
    "Social Media",
    "Sources",
    "Field Evidence",
    "Possible Duplicates",
    "Unresolved Records",
    "Coverage Report",
    "Execution Summary",
]
URL_HEADERS = {
    "Primary Website",
    "Public Profile URL",
    "URL",
    "Original URL",
    "Canonical URL",
    "Source URL Snapshot",
}
PERCENT_HEADERS = {"Confidence Score", "Coverage Percentage"}
WRAPPED_HEADERS = {
    "Description",
    "Primary Address",
    "Full Address",
    "Details",
    "Evidence Text",
    "Matching Reasons",
    "Reason",
    "Recommended Follow-up",
    "Discovery Query",
    "Blocked Reason",
    "Human Review Reason",
    "Notes",
}
ID_HEADERS = {
    "Facility ID",
    "Location ID",
    "Contact ID",
    "Attribute ID",
    "Staff ID",
    "Record ID",
    "Operating Hours ID",
    "Source ID",
    "Evidence ID",
    "Duplicate Relationship ID",
    "Unresolved Record ID",
    "Coverage Cell ID",
    "Execution ID",
    "Task ID",
    "Assigned Execution Agent ID",
    "Left Facility ID",
    "Right Facility ID",
    "Resolved Facility ID",
    "AI Team Plan ID",
    "Blueprint ID",
    "Mission ID",
}


class ExecutionExportService:
    async def build_workbook(
        self, db: AsyncSession, auth: AuthContext, execution_id: str
    ) -> tuple[bytes, str]:
        data = await self._load(db, auth, execution_id)
        execution = data["execution"]
        if execution.status not in TERMINAL_STATUSES:
            raise ConflictError("Excel report available after execution finishes.")
        if not data["facilities"]:
            raise ConflictError(
                "Excel export is unavailable because this execution only discovered candidate "
                "sources. Website retrieval and facility extraction are not enabled yet."
            )

        workbook = Workbook()
        workbook.properties.title = "Mock Rehabilitation Dataset"
        workbook.properties.creator = "MultiAI Verdict"
        workbook.remove(workbook.active)
        for sheet_name in SHEET_ORDER:
            workbook.create_sheet(sheet_name)

        self._write_rehab_centers(workbook["Rehab Centers"], data["facilities"])
        self._write_locations(workbook["Locations"], data["facilities"])
        self._write_contacts(workbook["Contacts"], data["facilities"], social=False)
        self._write_attributes(workbook["Treatment Services"], data["facilities"], "treatment_service")
        self._write_attributes(workbook["Programs"], data["facilities"], "program")
        self._write_attributes(workbook["Populations Served"], data["facilities"], "population_served")
        self._write_attributes(
            workbook["Admissions and Eligibility"], data["facilities"], "admission_eligibility"
        )
        self._write_attributes(workbook["Pricing and Payment"], data["facilities"], "pricing_payment")
        self._write_staff(workbook["Staff"], data["facilities"])
        self._write_licenses(workbook["Licenses and Accreditations"], data["facilities"])
        self._write_attributes(workbook["Amenities"], data["facilities"], "amenity")
        self._write_hours(workbook["Operating Hours"], data["facilities"])
        self._write_contacts(workbook["Social Media"], data["facilities"], social=True)
        self._write_sources(workbook["Sources"], data["sources"])
        self._write_evidence(workbook["Field Evidence"], data["facilities"])
        self._write_duplicates(workbook["Possible Duplicates"], data["duplicates"])
        self._write_unresolved(workbook["Unresolved Records"], data["facilities"])
        self._write_coverage(workbook["Coverage Report"], data["coverage"])
        await self._write_summary(workbook["Execution Summary"], db, data)

        for worksheet in workbook.worksheets:
            self._style_sheet(worksheet)
        buffer = BytesIO()
        workbook.save(buffer)
        filename = _filename(execution)
        return buffer.getvalue(), filename

    async def _load(self, db: AsyncSession, auth: AuthContext, execution_id: str) -> dict[str, Any]:
        result = await db.execute(
            select(ScrapingExecution)
            .where(ScrapingExecution.id == execution_id, ScrapingExecution.organization_id == auth.org_id)
            .options(
                selectinload(ScrapingExecution.mission).selectinload(ScrapingMission.project),
                selectinload(ScrapingExecution.blueprint),
                selectinload(ScrapingExecution.team_plan),
            )
        )
        execution = result.scalar_one_or_none()
        if execution is None:
            raise NotFoundError("ScrapingExecution", execution_id)
        facilities = (
            await db.execute(
                select(RehabilitationFacility)
                .where(RehabilitationFacility.execution_id == execution.id)
                .options(
                    selectinload(RehabilitationFacility.aliases),
                    selectinload(RehabilitationFacility.locations),
                    selectinload(RehabilitationFacility.contacts),
                    selectinload(RehabilitationFacility.attributes),
                    selectinload(RehabilitationFacility.staff),
                    selectinload(RehabilitationFacility.licenses),
                    selectinload(RehabilitationFacility.operating_hours),
                    selectinload(RehabilitationFacility.source_links),
                    selectinload(RehabilitationFacility.evidence),
                    selectinload(RehabilitationFacility.unresolved_fields),
                )
                .order_by(RehabilitationFacility.stable_key)
            )
        ).scalars().all()
        sources = (
            await db.execute(
                select(RehabilitationSource)
                .where(RehabilitationSource.execution_id == execution.id)
                .options(selectinload(RehabilitationSource.facility_links))
                .order_by(RehabilitationSource.canonical_url)
            )
        ).scalars().all()
        duplicates = (
            await db.execute(
                select(RehabilitationPossibleDuplicate)
                .where(RehabilitationPossibleDuplicate.execution_id == execution.id)
                .options(
                    selectinload(RehabilitationPossibleDuplicate.left_facility),
                    selectinload(RehabilitationPossibleDuplicate.right_facility),
                )
                .order_by(RehabilitationPossibleDuplicate.created_at)
            )
        ).scalars().all()
        coverage = (
            await db.execute(
                select(ScrapingCoverageCell)
                .where(ScrapingCoverageCell.execution_id == execution.id)
                .options(
                    selectinload(ScrapingCoverageCell.assigned_execution_agent).selectinload(
                        ScrapingExecutionAgent.team_agent
                    )
                )
                .order_by(
                    ScrapingCoverageCell.region_name,
                    ScrapingCoverageCell.language_name,
                    ScrapingCoverageCell.source_category,
                )
            )
        ).scalars().all()
        return {
            "execution": execution,
            "facilities": list(facilities),
            "sources": list(sources),
            "duplicates": list(duplicates),
            "coverage": list(coverage),
        }

    def _write_rehab_centers(self, ws: Any, facilities: list[RehabilitationFacility]) -> None:
        headers = [
            "Official Name", "Alternative Names", "Original-Language Name", "Facility Type",
            "Organization Type", "Operational Status", "Country", "Country Code",
            "Primary Region", "Primary City", "Primary Address", "Primary Phone",
            "Primary Email", "Primary Website", "Verification Status", "Confidence Score",
            "Human Review Status", "Duplicate Status", "Number of Locations",
            "Number of Contacts", "Number of Services", "Number of Sources",
            "Number of Evidence Records", "Description", "Facility ID", "Stable Key", "Mock",
            "Created At", "Updated At", "Last Verified At", "Latitude", "Longitude",
        ]
        rows = []
        for facility in facilities:
            rows.append([
                facility.canonical_name,
                "; ".join(alias.name for alias in facility.aliases if not alias.is_primary),
                facility.original_language_name,
                display_label(facility.facility_type),
                display_label(facility.organization_type),
                display_label(facility.operational_status),
                facility.country_name,
                facility.country_code,
                facility.primary_region,
                facility.primary_city,
                facility.primary_address,
                _contact_value(facility.contacts, "phone"),
                _contact_value(facility.contacts, "email"),
                facility.primary_website,
                display_label(facility.verification_status),
                facility.confidence_score,
                display_label(facility.human_review_status),
                display_label(facility.duplicate_status),
                len(facility.locations),
                len(facility.contacts),
                len([a for a in facility.attributes if a.attribute_group == "treatment_service"]),
                len(facility.source_links),
                len(facility.evidence),
                facility.description,
                facility.id,
                facility.stable_key,
                facility.is_mock,
                facility.created_at,
                facility.updated_at,
                facility.last_verified_at,
                facility.latitude or "",
                facility.longitude or "",
            ])
        _write_table(ws, headers, rows)

    def _write_locations(self, ws: Any, facilities: list[RehabilitationFacility]) -> None:
        headers = [
            "Location ID", "Facility ID", "Facility Name", "Location Type", "Location Name",
            "Country", "Country Code", "Region", "District", "City", "Area", "Full Address",
            "Postal Code", "Latitude", "Longitude", "Primary Location", "Verification Status",
            "Confidence Score", "Mock", "Created At", "Updated At",
        ]
        rows = [
            [
                loc.id, facility.id, facility.canonical_name, loc.location_type, loc.location_name,
                loc.country_name, loc.country_code, loc.region, loc.district, loc.city, loc.area,
                loc.full_address, loc.postal_code, loc.latitude or "", loc.longitude or "", loc.is_primary,
                display_label(loc.verification_status), loc.confidence_score, loc.is_mock,
                loc.created_at, loc.updated_at,
            ]
            for facility in facilities
            for loc in facility.locations
        ]
        _write_table(ws, headers, rows)

    def _write_contacts(self, ws: Any, facilities: list[RehabilitationFacility], *, social: bool) -> None:
        if social:
            headers = [
                "Contact ID", "Facility ID", "Facility Name", "Platform", "Label", "URL",
                "Verification Status", "Confidence Score", "Mock", "Created At",
            ]
        else:
            headers = [
                "Contact ID", "Facility ID", "Facility Name", "Contact Type", "Label", "Value",
                "Normalized Value", "Primary Contact", "Available 24/7", "Verification Status",
                "Confidence Score", "Mock", "Created At",
            ]
        rows = []
        for facility in facilities:
            for contact in facility.contacts:
                is_social = contact.contact_type in SOCIAL_CONTACT_TYPES
                if is_social != social:
                    continue
                if social:
                    rows.append([
                        contact.id, facility.id, facility.canonical_name,
                        display_label(contact.contact_type), contact.label, contact.value,
                        display_label(contact.verification_status),
                        contact.confidence_score, contact.is_mock, contact.created_at,
                    ])
                else:
                    rows.append([
                        contact.id, facility.id, facility.canonical_name,
                        display_label(contact.contact_type),
                        contact.label, contact.value, contact.normalized_value, contact.is_primary,
                        contact.available_24_7, display_label(contact.verification_status),
                        contact.confidence_score, contact.is_mock, contact.created_at,
                    ])
        _write_table(ws, headers, rows)

    def _write_attributes(self, ws: Any, facilities: list[RehabilitationFacility], group: str) -> None:
        headers_by_group = {
            "treatment_service": [
                "Attribute ID", "Facility ID", "Facility Name", "Service Key", "Service Name",
                "Available", "Details", "Verification Status", "Confidence Score", "Mock",
                "Created At", "Updated At",
            ],
            "program": [
                "Attribute ID", "Facility ID", "Facility Name", "Program Key", "Program Name",
                "Value", "Unit", "Period", "Details", "Verification Status", "Confidence Score",
                "Mock", "Created At", "Updated At",
            ],
            "population_served": [
                "Attribute ID", "Facility ID", "Facility Name", "Population Key", "Population Name",
                "Served", "Details", "Verification Status", "Confidence Score", "Mock",
                "Created At", "Updated At",
            ],
            "admission_eligibility": [
                "Attribute ID", "Facility ID", "Facility Name", "Rule Key", "Rule Name", "Text Value",
                "Boolean Value", "Number Value", "Unit", "Details", "Verification Status",
                "Confidence Score", "Mock", "Created At", "Updated At",
            ],
            "pricing_payment": [
                "Attribute ID", "Facility ID", "Facility Name", "Item Key", "Item Name", "Text Value",
                "Boolean Value", "Amount", "Currency", "Period", "Details", "Verification Status",
                "Confidence Score", "Mock", "Created At", "Updated At",
            ],
            "amenity": [
                "Attribute ID", "Facility ID", "Facility Name", "Amenity Key", "Amenity Name",
                "Available", "Details", "Verification Status", "Confidence Score", "Mock",
                "Created At", "Updated At",
            ],
        }
        rows = []
        for facility in facilities:
            for attr in facility.attributes:
                if attr.attribute_group != group:
                    continue
                rows.append(_attribute_row(facility, attr, group))
        _write_table(ws, headers_by_group[group], rows)

    def _write_staff(self, ws: Any, facilities: list[RehabilitationFacility]) -> None:
        headers = [
            "Staff ID", "Facility ID", "Facility Name", "Name", "Role", "Specialty",
            "Credentials", "Public Profile URL", "Verification Status", "Confidence Score",
            "Mock", "Created At",
        ]
        rows = [
            [
                staff.id, facility.id, facility.canonical_name, staff.name, staff.role,
                staff.specialty, staff.credentials, staff.public_profile_url,
                display_label(staff.verification_status), staff.confidence_score, staff.is_mock,
                staff.created_at,
            ]
            for facility in facilities
            for staff in facility.staff
        ]
        _write_table(ws, headers, rows)

    def _write_licenses(self, ws: Any, facilities: list[RehabilitationFacility]) -> None:
        headers = [
            "Record ID", "Facility ID", "Facility Name", "Record Type", "Name",
            "Issuing Authority", "Identifier", "Status", "Valid From", "Valid Until",
            "Verification Status", "Confidence Score", "Mock", "Created At",
        ]
        rows = [
            [
                license_row.id, facility.id, facility.canonical_name, license_row.record_type,
                license_row.name, license_row.issuing_authority, license_row.identifier,
                display_label(license_row.status), license_row.valid_from, license_row.valid_until,
                display_label(license_row.verification_status), license_row.confidence_score,
                license_row.is_mock, license_row.created_at,
            ]
            for facility in facilities
            for license_row in facility.licenses
        ]
        _write_table(ws, headers, rows)

    def _write_hours(self, ws: Any, facilities: list[RehabilitationFacility]) -> None:
        headers = [
            "Operating Hours ID", "Facility ID", "Facility Name", "Day of Week", "Opens At",
            "Closes At", "Closed", "Open 24 Hours", "Notes", "Mock", "Created At",
        ]
        rows = [
            [
                hours.id, facility.id, facility.canonical_name, hours.day_of_week,
                hours.opens_at, hours.closes_at, hours.is_closed, hours.is_24_hours,
                hours.notes, hours.is_mock, hours.created_at,
            ]
            for facility in facilities
            for hours in facility.operating_hours
        ]
        _write_table(ws, headers, rows)

    def _write_sources(self, ws: Any, sources: list[RehabilitationSource]) -> None:
        headers = [
            "Source ID", "Execution ID", "Coverage Cell ID", "Task ID", "Linked Facility Count",
            "Original URL", "Canonical URL", "Domain", "Source Category", "Discovery Query",
            "Page Title", "Language", "Region", "Fetch Status", "HTTP Status", "Content Type",
            "Content Hash", "Retrieved At", "Blocked Reason", "Mock", "Created At", "Updated At",
        ]
        rows = [
            [
                source.id, source.execution_id, source.coverage_cell_id, source.task_id,
                len(source.facility_links), source.original_url, source.canonical_url, source.domain,
                display_label(source.source_category), source.discovery_query, source.page_title,
                source.language_code, source.region, display_label(source.fetch_status),
                source.http_status, source.content_type,
                source.content_hash, source.retrieved_at, source.blocked_reason, source.is_mock,
                source.created_at, source.updated_at,
            ]
            for source in sources
        ]
        _write_table(ws, headers, rows)

    def _write_evidence(self, ws: Any, facilities: list[RehabilitationFacility]) -> None:
        headers = [
            "Evidence ID", "Facility ID", "Facility Name", "Source ID", "Field Path",
            "Extracted Value", "Evidence Text", "Page Title", "Source URL Snapshot", "Language",
            "Extraction Method", "Verification Status", "Confidence Score", "Mock", "Created At",
        ]
        rows = [
            [
                evidence.id, facility.id, facility.canonical_name, evidence.source_id,
                evidence.field_path, evidence.extracted_value, evidence.evidence_text,
                evidence.page_title, evidence.source_url_snapshot, evidence.language_code,
                display_label(evidence.extraction_method), display_label(evidence.verification_status),
                evidence.confidence_score, evidence.is_mock, evidence.created_at,
            ]
            for facility in facilities
            for evidence in facility.evidence
        ]
        _write_table(ws, headers, rows)

    def _write_duplicates(self, ws: Any, duplicates: list[RehabilitationPossibleDuplicate]) -> None:
        headers = [
            "Duplicate Relationship ID", "Left Facility ID", "Left Facility Name",
            "Right Facility ID", "Right Facility Name", "Match Score", "Matching Reasons",
            "Resolution Status", "Resolved Facility ID", "Reviewed At", "Mock", "Created At",
        ]
        rows = [
            [
                duplicate.id, duplicate.left_facility_id, duplicate.left_facility.canonical_name,
                duplicate.right_facility_id, duplicate.right_facility.canonical_name,
                duplicate.match_score, duplicate.matching_reasons,
                display_label(duplicate.resolution_status), duplicate.resolved_facility_id,
                duplicate.reviewed_at, duplicate.is_mock, duplicate.created_at,
            ]
            for duplicate in duplicates
        ]
        _write_table(ws, headers, rows)

    def _write_unresolved(self, ws: Any, facilities: list[RehabilitationFacility]) -> None:
        headers = [
            "Unresolved Record ID", "Facility ID", "Facility Name", "Field Path", "Status",
            "Reason", "Recommended Follow-up", "Source ID", "Mock", "Created At", "Resolved At",
        ]
        rows = [
            [
                unresolved.id, facility.id, facility.canonical_name, unresolved.field_path,
                display_label(unresolved.unresolved_status), unresolved.reason,
                unresolved.recommended_follow_up, unresolved.source_id, unresolved.is_mock,
                unresolved.created_at, unresolved.resolved_at,
            ]
            for facility in facilities
            for unresolved in facility.unresolved_fields
        ]
        _write_table(ws, headers, rows)

    def _write_coverage(self, ws: Any, coverage: list[ScrapingCoverageCell]) -> None:
        summary_headers = ["Coverage Status", "Count"]
        summary_rows = [
            ["Total Coverage Cells", len(coverage)],
            ["Covered", _coverage_count(coverage, "covered")],
            ["Covered — No Results", _coverage_count(coverage, "covered_no_results")],
            ["Partially Covered", _coverage_count(coverage, "partially_covered")],
            ["Blocked", _coverage_count(coverage, "blocked")],
            ["Human Review Required", _coverage_count(coverage, "human_review_required")],
            ["Failed", _coverage_count(coverage, "failed")],
            ["Cancelled", _coverage_count(coverage, "cancelled")],
            ["Not Started", _coverage_count(coverage, "not_started")],
            ["In Progress", _coverage_count(coverage, "in_progress")],
        ]
        ws.append(summary_headers)
        for row in summary_rows:
            ws.append([safe_cell(value) for value in row])
        ws.append([])
        ws.append(["Coverage Cell Details"])
        headers = [
            "Coverage Cell ID", "Region Code", "Region Name", "Language Code", "Language Name",
            "Source Category", "Status", "Result Count", "Assigned Execution Agent ID",
            "Assigned Agent Name", "Blocked Reason", "Human Review Reason", "Started At",
            "Completed At", "Created At", "Updated At",
        ]
        rows = []
        for cell in coverage:
            agent = cell.assigned_execution_agent
            rows.append([
                cell.id, cell.region_code, cell.region_name, cell.language_code, cell.language_name,
                display_label(cell.source_category), display_label(cell.status.value), cell.result_count,
                cell.assigned_execution_agent_id, agent.team_agent.name if agent else None,
                cell.reason if cell.status.value == "blocked" else None,
                cell.reason if cell.status.value == "human_review_required" else None,
                cell.started_at, cell.completed_at, cell.created_at, cell.updated_at,
            ])
        _write_table(ws, headers, rows)

    async def _write_summary(self, ws: Any, db: AsyncSession, data: dict[str, Any]) -> None:
        execution: ScrapingExecution = data["execution"]
        facilities: list[RehabilitationFacility] = data["facilities"]
        sources: list[RehabilitationSource] = data["sources"]
        coverage: list[ScrapingCoverageCell] = data["coverage"]
        duplicates: list[RehabilitationPossibleDuplicate] = data["duplicates"]
        task_counts = await _task_counts(db, execution.id)
        agent_count = await _scalar(
            db,
            select(func.count(ScrapingExecutionAgent.id)).where(
                ScrapingExecutionAgent.execution_id == execution.id
            ),
        )
        verified_count = len([f for f in facilities if f.verification_status == VERIFIED_STATUS])
        review_count = len([f for f in facilities if f.human_review_status == "required"])
        evidence_count = sum(len(f.evidence) for f in facilities)
        unresolved_count = sum(len(f.unresolved_fields) for f in facilities)
        coverage_percentage = _coverage_percentage(coverage)
        coverage_outcome = execution_outcome_label(execution.status, coverage_gap_count(coverage))
        dataset_type = _dataset_type(execution, facilities)
        country_completeness = (
            "Not measured in mock mode" if dataset_type == "Mock Sample Dataset" else "Not measured"
        )

        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=4)
        ws.cell(row=1, column=1, value=f"MOCK REHABILITATION DATASET — {execution.country_name}")
        ws.cell(
            row=2,
            column=1,
            value="All facility records in this workbook were generated for testing.",
        )
        ws.cell(
            row=3,
            column=1,
            value="No external websites or real rehabilitation centers were used.",
        )
        ws.cell(
            row=4,
            column=1,
            value=(
                "The facility rows in this workbook are fictional sample records used to test "
                "the dataset structure. They are not a count or estimate of real rehabilitation "
                "centers in the selected country."
            ),
        )
        ws.append(["KPI", "Value", "KPI", "Value"])
        kpis = [
            ("Total Facilities", len(facilities)),
            ("Sample Facility Count", len(facilities)),
            ("Verified Facilities", verified_count),
            ("Human Review Facilities", review_count),
            ("Total Sources", len(sources)),
            ("Total Evidence", evidence_count),
            ("Possible Duplicates", len(duplicates)),
            ("Unresolved Fields", unresolved_count),
            ("Coverage Percentage", coverage_percentage / 100),
            ("Coverage Outcome", coverage_outcome),
        ]
        for index in range(0, len(kpis), 2):
            left = kpis[index]
            right = kpis[index + 1] if index + 1 < len(kpis) else None
            ws.append([
                left[0],
                safe_cell(left[1]),
                right[0] if right else None,
                safe_cell(right[1]) if right else None,
            ])
        ws.append([])
        _append_section(
            ws,
            "Mission and Execution",
            [
                ("Mission Name", execution.mission.title if execution.mission else None),
                (
                    "Project Name",
                    execution.mission.project.name
                    if execution.mission and execution.mission.project
                    else None,
                ),
                ("Execution Type", display_label(execution.execution_type)),
                ("Mode", display_label(execution.mode)),
                ("Status", coverage_outcome),
                ("Country", execution.country_name),
                ("Country Code", execution.country_code),
            ],
        )
        _append_section(
            ws,
            "Dataset Results",
            [
                ("Dataset Type", dataset_type),
                ("Sample Facility Count", len(facilities)),
                ("Country Completeness", country_completeness),
                ("Coverage Outcome", coverage_outcome),
                ("Total Locations", sum(len(f.locations) for f in facilities)),
                ("Total Contacts", sum(len(f.contacts) for f in facilities)),
                (
                    "Total Services",
                    sum(
                        len(
                            [
                                a
                                for a in f.attributes
                                if a.attribute_group == "treatment_service"
                            ]
                        )
                        for f in facilities
                    ),
                ),
                (
                    "Total Programs",
                    sum(
                        len([a for a in f.attributes if a.attribute_group == "program"])
                        for f in facilities
                    ),
                ),
            ],
        )
        _append_section(
            ws,
            "Coverage",
            [
                ("Total Coverage Cells", len(coverage)),
                ("Covered Cells", _coverage_count(coverage, "covered")),
                ("Covered — No Results Cells", _coverage_count(coverage, "covered_no_results")),
                ("Partially Covered Cells", _coverage_count(coverage, "partially_covered")),
                ("Blocked Cells", _coverage_count(coverage, "blocked")),
                ("Human Review Cells", _coverage_count(coverage, "human_review_required")),
                ("Failed Coverage Cells", _coverage_count(coverage, "failed")),
                ("Coverage Percentage", coverage_percentage / 100),
            ],
        )
        _append_section(
            ws,
            "Tasks and Agents",
            [
                ("Total Execution Agents", agent_count),
                ("Total Tasks", sum(task_counts.values())),
                ("Completed Tasks", task_counts.get("completed", 0)),
                ("Failed Tasks", task_counts.get("failed", 0)),
                ("Cancelled Tasks", task_counts.get("cancelled", 0)),
            ],
        )
        _append_section(
            ws,
            "Timing and Status",
            [
                ("Created At", execution.created_at),
                ("Started At", execution.started_at),
                ("Completed At", execution.completed_at),
                ("Cancellation Requested At", execution.cancel_requested_at),
                ("Last Heartbeat", execution.heartbeat_at),
                ("Sanitized Error", execution.error_message),
            ],
        )
        _append_section(
            ws,
            "Technical Identifiers",
            [
                ("Execution ID", execution.id),
                ("Mission ID", execution.mission_id),
                ("Blueprint ID", execution.blueprint_id),
                ("Blueprint Version", execution.blueprint.version if execution.blueprint else None),
                ("AI Team Plan ID", execution.team_plan_id),
            ],
        )

    def _style_sheet(self, ws: Any) -> None:
        header_fill = PatternFill("solid", fgColor="244A6B")
        section_fill = PatternFill("solid", fgColor="E8EEF7")
        stripe_fill = PatternFill("solid", fgColor="F8FAFC")
        border = Border(bottom=Side(style="thin", color="D7DEE8"))
        detail_header_row = _detail_header_row(ws)
        ws.freeze_panes = f"A{detail_header_row + 1}"
        if ws.max_column and ws.max_row:
            ws.auto_filter.ref = f"A{detail_header_row}:{get_column_letter(ws.max_column)}{ws.max_row}"
        for cell in ws[detail_header_row]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.border = border
        if ws.title == "Execution Summary":
            ws.freeze_panes = "A6"
            ws.auto_filter.ref = None
            title = ws.cell(row=1, column=1)
            title.font = Font(bold=True, size=18, color="9A3412")
            for row in ws.iter_rows(min_row=1, max_row=4):
                row[0].font = Font(bold=True, color="9A3412")
            for row in range(5, ws.max_row + 1):
                if ws.cell(row=row, column=1).value and ws.cell(row=row, column=2).value is None:
                    for cell in ws[row]:
                        cell.font = Font(bold=True)
                        cell.fill = section_fill
                elif row == 5:
                    for cell in ws[row]:
                        cell.font = Font(bold=True, color="FFFFFF")
                        cell.fill = header_fill
        for row in ws.iter_rows():
            for cell in row:
                header = _column_header(ws, cell.column)
                cell.alignment = Alignment(
                    wrap_text=header in WRAPPED_HEADERS,
                    vertical="top",
                )
                cell.border = border
                if cell.row > detail_header_row and cell.row % 2 == 0 and ws.title != "Execution Summary":
                    cell.fill = stripe_fill
                if header in PERCENT_HEADERS and isinstance(cell.value, int | float):
                    cell.number_format = "0.0%"
                if header in URL_HEADERS and isinstance(cell.value, str) and _safe_http_url(cell.value):
                    cell.hyperlink = cell.value
                    cell.style = "Hyperlink"
                if ws.title == "Execution Summary" and cell.column in {2, 4}:
                    label = ws.cell(row=cell.row, column=cell.column - 1).value
                    if label in PERCENT_HEADERS and isinstance(cell.value, int | float):
                        cell.number_format = "0.0%"
        for column_index in range(1, ws.max_column + 1):
            letter = get_column_letter(column_index)
            ws.column_dimensions[letter].width = _column_width(ws, column_index)
        ws.sheet_view.showGridLines = False


def _write_table(ws: Any, headers: list[str], rows: Iterable[Iterable[Any]]) -> None:
    ws.append(headers)
    for row in rows:
        ws.append([safe_cell(value) for value in row])


def _append_section(ws: Any, title: str, rows: list[tuple[str, Any]]) -> None:
    ws.append([])
    ws.append([title, None, None, None])
    for label, value in rows:
        ws.append([label, safe_cell(value), None, None])


def safe_cell(value: Any) -> Any:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat(timespec="minutes")
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        return f"'{value}"
    return value


def display_label(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    explicit = {
        "active_mock": "Active — Mock",
        "private_mock": "Private — Mock",
        "public_mock": "Public — Mock",
        "nonprofit_mock": "Nonprofit — Mock",
        "unknown_mock": "Unknown — Mock",
        "mock_active": "Mock Active",
        "possible_duplicate": "Possible Duplicate",
        "human_review_required": "Human Review Required",
        "searched_not_found": "Searched — Not Found",
        "found_unverified": "Found — Unverified",
        "covered_no_results": "Covered — No Results",
        "in_progress": "In Progress",
        "not_started": "Not Started",
        "not_required": "Not Required",
        "official_mock": "Official — Mock",
        "directory_mock": "Directory — Mock",
        "shared_directory_mock": "Shared Directory — Mock",
        "deterministic_mock": "Deterministic Mock",
    }
    if value in explicit:
        return explicit[value]
    return value.replace("_", " ").title()


def _detail_header_row(ws: Any) -> int:
    if ws.title == "Coverage Report":
        return 14
    if ws.title == "Execution Summary":
        return 5
    return 1


def _column_header(ws: Any, column_index: int) -> str | None:
    header_row = _detail_header_row(ws)
    value = ws.cell(row=header_row, column=column_index).value
    return str(value) if value is not None else None


def _column_width(ws: Any, column_index: int) -> int:
    header = _column_header(ws, column_index) or str(ws.cell(row=1, column=column_index).value or "")
    if header in ID_HEADERS:
        return 38
    if "Name" in header:
        return 30
    if header in URL_HEADERS:
        return 42
    if "Address" in header:
        return 42
    if header in WRAPPED_HEADERS:
        return 48
    if "Created At" in header or "Updated At" in header or "Verified At" in header:
        return 24
    if "Status" in header or "Type" in header:
        return 22
    if "Country" in header or "Region" in header or "City" in header or "Language" in header:
        return 22
    if "Number of" in header or "Count" in header:
        return 14
    if header in {"Mock", "Closed", "Open 24 Hours", "Primary Contact", "Available 24/7"}:
        return 14
    return min(max(len(header) + 2, 14), 28)


def _safe_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _attribute_row(
    facility: RehabilitationFacility,
    attr: RehabilitationFacilityAttribute,
    group: str,
) -> list[Any]:
    base = [attr.id, facility.id, facility.canonical_name, attr.attribute_key, attr.display_name]
    if group in {"treatment_service", "population_served", "amenity"}:
        return base + [
            attr.value_boolean, attr.details, display_label(attr.verification_status), attr.confidence_score,
            attr.is_mock, attr.created_at, attr.updated_at,
        ]
    if group == "program":
        return base + [
            _typed_value(attr), attr.value_unit, attr.period, attr.details,
            display_label(attr.verification_status), attr.confidence_score, attr.is_mock,
            attr.created_at, attr.updated_at,
        ]
    if group == "admission_eligibility":
        return base + [
            attr.value_text, attr.value_boolean, attr.value_number, attr.value_unit, attr.details,
            display_label(attr.verification_status), attr.confidence_score, attr.is_mock,
            attr.created_at, attr.updated_at,
        ]
    return base + [
        attr.value_text, attr.value_boolean, attr.value_number, attr.currency_code, attr.period,
        attr.details, display_label(attr.verification_status), attr.confidence_score,
        attr.is_mock, attr.created_at, attr.updated_at,
    ]


def _typed_value(attr: RehabilitationFacilityAttribute) -> Any:
    return attr.value_text if attr.value_text is not None else attr.value_number


def _contact_value(contacts: list[RehabilitationFacilityContact], contact_type: str) -> str | None:
    for contact in contacts:
        if contact.contact_type == contact_type and contact.is_primary:
            return contact.value
    for contact in contacts:
        if contact.contact_type == contact_type:
            return contact.value
    return None


async def _task_counts(db: AsyncSession, execution_id: str) -> dict[str, int]:
    result = await db.execute(
        select(ScrapingTask.status, func.count(ScrapingTask.id))
        .where(ScrapingTask.execution_id == execution_id)
        .group_by(ScrapingTask.status)
    )
    return {status.value: count for status, count in result.all()}


async def _scalar(db: AsyncSession, query: Any) -> int:
    return int((await db.execute(query)).scalar_one() or 0)


def _coverage_count(coverage: list[ScrapingCoverageCell], status: str) -> int:
    return len([cell for cell in coverage if cell.status.value == status])


def _coverage_percentage(coverage: list[ScrapingCoverageCell]) -> float:
    if not coverage:
        return 0
    covered = len([cell for cell in coverage if cell.status.value in {"covered", "covered_no_results", "partially_covered"}])
    return round((covered / len(coverage)) * 100, 2)


def _dataset_type(execution: ScrapingExecution, facilities: list[RehabilitationFacility]) -> str:
    if execution.mode == "mock" or (facilities and all(facility.is_mock for facility in facilities)):
        return "Mock Sample Dataset"
    return "Rehabilitation Dataset"


def _filename(execution: ScrapingExecution) -> str:
    country = re.sub(r"[^A-Za-z0-9]+", "-", execution.country_name).strip("-").lower() or "country"
    suffix = execution.id[:8]
    return f"mock-rehabilitation-dataset-{country}-{suffix}.xlsx"


execution_export_service = ExecutionExportService()
