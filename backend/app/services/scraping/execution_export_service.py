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
    RehabilitationSource,
    ScrapingCoverageCell,
    ScrapingExecution,
    ScrapingExecutionAgent,
    ScrapingExecutionStatus,
    ScrapingMission,
    ScrapingTask,
)
from app.services.scraping.execution_outcome import coverage_gap_count, execution_outcome_label
from app.services.scraping.result_metrics import (
    execution_completeness_percent,
    normalized_publication_class,
    primary_phone_for_facility,
    primary_phone_for_location,
    result_counts,
)

MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TERMINAL_STATUSES = {
    ScrapingExecutionStatus.COMPLETED,
    ScrapingExecutionStatus.FAILED,
    ScrapingExecutionStatus.CANCELLED,
}
SHEET_ORDER = [
    "Verified",
    "Review",
    "Excluded",
    "Locations",
    "Contacts",
    "Hard Gates",
    "Contradictions",
    "Coverage",
    "Execution Summary",
]
URL_HEADERS = {"Primary Website", "Website", "Value"}
PERCENT_HEADERS = {"Completeness %", "Coverage Percentage"}
WRAPPED_HEADERS = {"Reason", "Primary Address", "Full Address", "Policy Summary", "Notes"}


class ExecutionExportService:
    async def build_workbook(
        self, db: AsyncSession, auth: AuthContext, execution_id: str
    ) -> tuple[bytes, str]:
        data = await self._load(db, auth, execution_id)
        execution = data["execution"]
        if execution.status not in TERMINAL_STATUSES:
            raise ConflictError("Excel report available after execution finishes.")

        workbook = Workbook()
        workbook.properties.title = "Scraping Execution Export"
        workbook.properties.creator = "MultiAI Verdict"
        workbook.remove(workbook.active)
        for sheet_name in SHEET_ORDER:
            workbook.create_sheet(sheet_name)

        facilities = data["facilities"]
        self._write_classification_sheet(workbook["Verified"], facilities, "verified")
        self._write_classification_sheet(workbook["Review"], facilities, "review_required")
        self._write_classification_sheet(workbook["Excluded"], facilities, "excluded")
        self._write_locations(workbook["Locations"], facilities)
        self._write_contacts(workbook["Contacts"], facilities)
        self._write_hard_gates(workbook["Hard Gates"], facilities)
        self._write_contradictions(workbook["Contradictions"], facilities)
        self._write_coverage(workbook["Coverage"], data["coverage"])
        await self._write_summary(workbook["Execution Summary"], db, data)

        for worksheet in workbook.worksheets:
            self._style_sheet(worksheet)
        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue(), _filename(execution)

    async def _load(self, db: AsyncSession, auth: AuthContext, execution_id: str) -> dict[str, Any]:
        result = await db.execute(
            select(ScrapingExecution)
            .where(
                ScrapingExecution.id == execution_id,
                ScrapingExecution.organization_id == auth.org_id,
            )
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
                    selectinload(RehabilitationFacility.locations),
                    selectinload(RehabilitationFacility.contacts),
                    selectinload(RehabilitationFacility.unresolved_fields),
                )
                .order_by(RehabilitationFacility.stable_key)
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
        sources = (
            await db.execute(
                select(RehabilitationSource).where(RehabilitationSource.execution_id == execution.id)
            )
        ).scalars().all()
        return {
            "execution": execution,
            "facilities": list(facilities),
            "coverage": list(coverage),
            "sources": list(sources),
        }

    def _write_classification_sheet(
        self,
        ws: Any,
        facilities: list[RehabilitationFacility],
        publication_class: str,
    ) -> None:
        headers = [
            "Facility Name",
            "Publication Class",
            "Verification Status",
            "Review Status",
            "Completeness %",
            "Country",
            "Region",
            "City",
            "Primary Address",
            "Primary Phone",
            "Country Containment Status",
            "Reason",
            "Primary Website",
        ]
        rows = []
        for facility in facilities:
            if normalized_publication_class(getattr(facility, "publication_class", None)) != publication_class:
                continue
            rows.append(
                [
                    facility.canonical_name,
                    display_label(getattr(facility, "publication_class", "review_required")),
                    display_label(facility.verification_status),
                    display_label(facility.human_review_status),
                    _facility_completeness_percent(facility) / 100,
                    facility.country_name,
                    facility.primary_region,
                    facility.primary_city,
                    facility.primary_address,
                    primary_phone_for_facility(facility),
                    display_label(getattr(facility, "country_containment_status", None)),
                    getattr(facility, "country_containment_reason", None),
                    facility.primary_website,
                ]
            )
        if not rows:
            rows.append([f"No {display_label(publication_class)} facilities recorded."] + [None] * (len(headers) - 1))
        _write_table(ws, headers, rows)

    def _write_locations(self, ws: Any, facilities: list[RehabilitationFacility]) -> None:
        headers = [
            "Facility Name",
            "Location Name",
            "Location Type",
            "Country",
            "Region",
            "City",
            "Full Address",
            "Primary Phone",
            "Verification Status",
            "Country Containment Status",
            "Location Completeness Status",
            "Location Gap Reason",
        ]
        rows = []
        for facility in facilities:
            for location in facility.locations:
                rows.append(
                    [
                        facility.canonical_name,
                        location.location_name,
                        location.location_type,
                        location.country_name,
                        location.region,
                        location.city,
                        location.full_address,
                        primary_phone_for_location(location, facility.contacts),
                        display_label(location.verification_status),
                        display_label(getattr(location, "country_containment_status", None)),
                        display_label(getattr(location, "location_completeness_status", None)),
                        getattr(location, "location_gap_reason", None),
                    ]
                )
        if not rows:
            rows.append(["No locations recorded."] + [None] * (len(headers) - 1))
        _write_table(ws, headers, rows)

    def _write_contacts(self, ws: Any, facilities: list[RehabilitationFacility]) -> None:
        headers = [
            "Facility Name",
            "Location Name",
            "Contact Type",
            "Label",
            "Verification Status",
            "Value",
            "Normalized Value",
            "Contact Discovery Status",
            "Primary",
        ]
        rows = []
        location_name_by_id = {
            location.id: location.location_name
            for facility in facilities
            for location in facility.locations
        }
        for facility in facilities:
            for contact in facility.contacts:
                rows.append(
                    [
                        facility.canonical_name,
                        location_name_by_id.get(getattr(contact, "location_id", None)),
                        display_label(contact.contact_type),
                        contact.label,
                        display_label(contact.verification_status),
                        contact.value,
                        contact.normalized_value,
                        display_label(getattr(contact, "contact_discovery_status", None)),
                        contact.is_primary,
                    ]
                )
        if not rows:
            rows.append(["No contacts recorded."] + [None] * (len(headers) - 1))
        _write_table(ws, headers, rows)

    def _write_hard_gates(self, ws: Any, facilities: list[RehabilitationFacility]) -> None:
        headers = [
            "Facility Name",
            "Publication Class",
            "Country Containment Status",
            "Review Status",
            "Reason",
        ]
        rows = []
        for facility in facilities:
            if normalized_publication_class(getattr(facility, "publication_class", None)) == "verified":
                continue
            rows.append(
                [
                    facility.canonical_name,
                    display_label(getattr(facility, "publication_class", None)),
                    display_label(getattr(facility, "country_containment_status", None)),
                    display_label(facility.human_review_status),
                    getattr(facility, "country_containment_reason", None),
                ]
            )
        if not rows:
            rows.append(["No hard-gate exceptions recorded."] + [None] * (len(headers) - 1))
        _write_table(ws, headers, rows)

    def _write_contradictions(self, ws: Any, facilities: list[RehabilitationFacility]) -> None:
        headers = [
            "Facility Name",
            "Field Path",
            "Status",
            "Reason",
            "Recommended Follow-up",
        ]
        rows = []
        for facility in facilities:
            for unresolved in facility.unresolved_fields:
                if str(unresolved.unresolved_status).strip().lower() != "conflicting":
                    continue
                rows.append(
                    [
                        facility.canonical_name,
                        unresolved.field_path,
                        display_label(unresolved.unresolved_status),
                        unresolved.reason,
                        unresolved.recommended_follow_up,
                    ]
                )
        if not rows:
            rows.append(["No contradictions recorded."] + [None] * (len(headers) - 1))
        _write_table(ws, headers, rows)

    def _write_coverage(self, ws: Any, coverage: list[ScrapingCoverageCell]) -> None:
        headers = [
            "Coverage Cell ID",
            "Region",
            "Language",
            "Source Category",
            "Status",
            "Result Count",
            "Assigned Agent",
            "Reason",
        ]
        rows = []
        for cell in coverage:
            agent = cell.assigned_execution_agent
            rows.append(
                [
                    cell.id,
                    cell.region_name,
                    cell.language_name,
                    cell.source_category,
                    display_label(cell.status.value),
                    cell.result_count,
                    agent.team_agent.name if agent else None,
                    cell.reason,
                ]
            )
        if not rows:
            rows.append(["No coverage cells recorded."] + [None] * (len(headers) - 1))
        _write_table(ws, headers, rows)

    async def _write_summary(self, ws: Any, db: AsyncSession, data: dict[str, Any]) -> None:
        execution: ScrapingExecution = data["execution"]
        facilities: list[RehabilitationFacility] = data["facilities"]
        coverage: list[ScrapingCoverageCell] = data["coverage"]
        sources: list[RehabilitationSource] = data["sources"]
        counts = result_counts(facilities)
        task_counts = await _task_counts(db, execution.id)
        agent_count = await _scalar(
            db,
            select(func.count(ScrapingExecutionAgent.id)).where(
                ScrapingExecutionAgent.execution_id == execution.id
            ),
        )
        coverage_percentage = _coverage_percentage(coverage)
        coverage_outcome = execution_outcome_label(execution.status, coverage_gap_count(coverage))
        dataset_type = _dataset_type(execution, facilities)
        completeness_percent = execution_completeness_percent(facilities)

        title = (
            f"MOCK EXECUTION SUMMARY — {execution.country_name}"
            if dataset_type == "Mock Sample Dataset"
            else f"SCRAPING EXECUTION SUMMARY — {execution.country_name}"
        )
        subtitle = (
            "This workbook contains generated mock records for pipeline testing."
            if dataset_type == "Mock Sample Dataset"
            else "This workbook contains exported execution results grouped by publication decision."
        )
        ws.cell(row=1, column=1, value=title)
        ws.cell(row=2, column=1, value=subtitle)
        ws.append(["KPI", "Value", "KPI", "Value"])
        kpis = [
            ("Total Facilities", len(facilities)),
            ("Verified Facilities", counts["verified"]),
            ("Review Facilities", counts["review"]),
            ("Excluded Facilities", counts["excluded"]),
            ("Completeness %", completeness_percent / 100),
            ("Coverage Outcome", coverage_outcome),
            ("Coverage Percentage", coverage_percentage / 100),
            ("Total Sources", len(sources)),
        ]
        for index in range(0, len(kpis), 2):
            left = kpis[index]
            right = kpis[index + 1] if index + 1 < len(kpis) else None
            ws.append(
                [
                    left[0],
                    safe_cell(left[1]),
                    right[0] if right else None,
                    safe_cell(right[1]) if right else None,
                ]
            )
        _append_section(
            ws,
            "Mission and Execution",
            [
                ("Mission Name", execution.mission.title if execution.mission else None),
                ("Execution Type", display_label(execution.execution_type)),
                ("Mode", display_label(execution.mode)),
                ("Country", execution.country_name),
                ("Country Code", execution.country_code),
            ],
        )
        _append_section(
            ws,
            "Operations",
            [
                ("Execution Agents", agent_count),
                ("Total Tasks", sum(task_counts.values())),
                ("Completed Tasks", task_counts.get("completed", 0)),
                ("Failed Tasks", task_counts.get("failed", 0)),
                ("Coverage Cells", len(coverage)),
            ],
        )
        _append_section(
            ws,
            "Timing and Status",
            [
                ("Created At", execution.created_at),
                ("Started At", execution.started_at),
                ("Completed At", execution.completed_at),
                ("Error", execution.error_message),
            ],
        )

    def _style_sheet(self, ws: Any) -> None:
        header_fill = PatternFill("solid", fgColor="244A6B")
        stripe_fill = PatternFill("solid", fgColor="F8FAFC")
        border = Border(bottom=Side(style="thin", color="D7DEE8"))
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.border = border
        if ws.title == "Execution Summary":
            ws["A1"].font = Font(bold=True, size=16, color="9A3412")
            ws["A2"].font = Font(color="9A3412")
            for cell in ws[3]:
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
                if ws.title != "Execution Summary" and cell.row > 1 and cell.row % 2 == 0:
                    cell.fill = stripe_fill
                if header in PERCENT_HEADERS and isinstance(cell.value, int | float):
                    cell.number_format = "0.0%"
                if header in URL_HEADERS and isinstance(cell.value, str) and _safe_http_url(cell.value):
                    cell.hyperlink = cell.value
                    cell.style = "Hyperlink"
        for column_index in range(1, ws.max_column + 1):
            letter = get_column_letter(column_index)
            ws.column_dimensions[letter].width = _column_width(ws, column_index)
        ws.sheet_view.showGridLines = False
        ws.freeze_panes = "A2"


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
        return None
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
    if value == "review_required":
        return "Review Required"
    return value.replace("_", " ").title()


async def _task_counts(db: AsyncSession, execution_id: str) -> dict[str, int]:
    result = await db.execute(
        select(ScrapingTask.status, func.count(ScrapingTask.id))
        .where(ScrapingTask.execution_id == execution_id)
        .group_by(ScrapingTask.status)
    )
    return {status.value: count for status, count in result.all()}


async def _scalar(db: AsyncSession, query: Any) -> int:
    return int((await db.execute(query)).scalar_one() or 0)


def _coverage_percentage(coverage: list[ScrapingCoverageCell]) -> float:
    if not coverage:
        return 0.0
    covered = len(
        [
            cell
            for cell in coverage
            if cell.status.value in {"covered", "covered_no_results", "partially_covered"}
        ]
    )
    return round((covered / len(coverage)) * 100.0, 2)


def _dataset_type(execution: ScrapingExecution, facilities: list[RehabilitationFacility]) -> str:
    if execution.mode == "mock" or (facilities and all(facility.is_mock for facility in facilities)):
        return "Mock Sample Dataset"
    return "Real Execution Export"


def _facility_completeness_percent(facility: RehabilitationFacility) -> float:
    return execution_completeness_percent([facility])


def _column_header(ws: Any, column_index: int) -> str | None:
    header_row = 3 if ws.title == "Execution Summary" else 1
    value = ws.cell(row=header_row, column=column_index).value
    return str(value) if value is not None else None


def _column_width(ws: Any, column_index: int) -> int:
    header = _column_header(ws, column_index) or ""
    if "Address" in header or header in WRAPPED_HEADERS:
        return 42
    if "Name" in header or "Reason" in header:
        return 28
    if header in URL_HEADERS:
        return 38
    return min(max(len(header) + 2, 14), 28)


def _safe_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _filename(execution: ScrapingExecution) -> str:
    country = re.sub(r"[^A-Za-z0-9]+", "-", execution.country_name).strip("-").lower() or "country"
    suffix = execution.id[:8]
    return f"scraping-execution-{country}-{suffix}.xlsx"


execution_export_service = ExecutionExportService()
