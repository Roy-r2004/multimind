"""Excel workbook export for a Maps Census run.

Produces a single ``.xlsx`` workbook split by client-facing facility categories:

- ``Eligible Centers``
- ``Needs Review``
- ``Public/Government``
- ``Individual Practitioners``
- ``Excluded/Unrelated``
- ``Discovery Audit``
"""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.table import Table, TableStyleInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError
from app.db.models import (
    MapsCensusCell,
    MapsCensusRun,
    MapsClientEligibility,
    MapsFacilityType,
    MapsLifecycleStatus,
    MapsOperatorType,
    MapsOrganizationScope,
    MapsOwnershipStatus,
    MapsPlace,
)
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

ELIGIBLE_CENTERS_SHEET = "Eligible Centers"
NEEDS_REVIEW_SHEET = "Needs Review"
PUBLIC_GOVERNMENT_SHEET = "Public Government"
INDIVIDUAL_PRACTITIONERS_SHEET = "Individual Practitioners"
EXCLUDED_UNRELATED_SHEET = "Excluded Unrelated"
DISCOVERY_AUDIT_SHEET = "Discovery Audit"

EXPORT_HEADERS = [
    "Facility name",
    "Operator name",
    "Operator type",
    "Ownership status",
    "Funding type",
    "Facility type",
    "Care setting",
    "Residential accommodation",
    "Medical detox",
    "Addictions treated",
    "Treatment languages",
    "Address",
    "Website",
    "Phone",
    "Verification confidence",
    "Evidence URL",
    "Discovery sources",
]

AUDIT_HEADERS = ["Section", "Metric", "Value", "Details"]

# Headers whose string cells should render as clickable links.
_URL_HEADERS = {"Website", "Evidence URL"}
# Headers whose values must stay textual so "+" and leading zeroes survive.
_TEXT_HEADERS = {"Phone"}
# Short, scannable columns that read best centered in both axes.
_CENTERED_HEADERS = {
    "Phone",
    "Residential accommodation",
    "Medical detox",
    "Verification confidence",
}

_WIDE_COLUMNS = {
    "Facility name": 34,
    "Operator name": 28,
    "Operator type": 20,
    "Ownership status": 24,
    "Funding type": 20,
    "Facility type": 28,
    "Care setting": 18,
    "Addictions treated": 26,
    "Treatment languages": 22,
    "Address": 42,
    "Website": 40,
    "Evidence URL": 40,
    "Discovery sources": 24,
    "Details": 44,
}


class MapsExportService:
    async def build_workbook(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> tuple[bytes, str]:
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)

        places = (
            await db.execute(
                select(MapsPlace)
                .where(MapsPlace.run_id == run_id)
                .order_by(MapsPlace.canonical_name)
            )
        ).scalars().all()
        cells = (
            await db.execute(
                select(MapsCensusCell)
                .where(MapsCensusCell.run_id == run_id)
                .order_by(
                    MapsCensusCell.region_name,
                    MapsCensusCell.city_name,
                    MapsCensusCell.query_text,
                )
            )
        ).scalars().all()

        workbook = Workbook()
        workbook.properties.title = "Maps Census Export"
        workbook.properties.creator = "MultiAI Verdict"
        workbook.remove(workbook.active)

        eligible_places: list[MapsPlace] = []
        review_places: list[MapsPlace] = []
        public_places: list[MapsPlace] = []
        individual_places: list[MapsPlace] = []
        excluded_places: list[MapsPlace] = []

        for place in places:
            if _is_eligible_center(place):
                eligible_places.append(place)
            elif _is_review_place(place):
                review_places.append(place)
            elif _is_public_place(place):
                public_places.append(place)
            elif _is_individual_practitioner(place):
                individual_places.append(place)
            else:
                excluded_places.append(place)

        self._write_sheet(
            workbook.create_sheet(ELIGIBLE_CENTERS_SHEET),
            EXPORT_HEADERS,
            [self._export_place_row(place) for place in eligible_places],
            table_name="EligibleCenters",
        )
        self._write_sheet(
            workbook.create_sheet(NEEDS_REVIEW_SHEET),
            EXPORT_HEADERS,
            [self._export_place_row(place) for place in review_places],
            table_name="NeedsReview",
        )
        self._write_sheet(
            workbook.create_sheet(PUBLIC_GOVERNMENT_SHEET),
            EXPORT_HEADERS,
            [self._export_place_row(place) for place in public_places],
            table_name="PublicGovernment",
        )
        self._write_sheet(
            workbook.create_sheet(INDIVIDUAL_PRACTITIONERS_SHEET),
            EXPORT_HEADERS,
            [self._export_place_row(place) for place in individual_places],
            table_name="IndividualPractitioners",
        )
        self._write_sheet(
            workbook.create_sheet(EXCLUDED_UNRELATED_SHEET),
            EXPORT_HEADERS,
            [self._export_place_row(place) for place in excluded_places],
            table_name="ExcludedUnrelated",
        )
        self._write_sheet(
            workbook.create_sheet(DISCOVERY_AUDIT_SHEET),
            AUDIT_HEADERS,
            self._audit_rows(run, cells, places),
            table_name="DiscoveryAudit",
        )

        buffer = BytesIO()
        workbook.save(buffer)
        filename = f"{run.country_code.lower()}-maps-census-export.xlsx"
        return buffer.getvalue(), filename

    async def get_export_summary(
        self, db: AsyncSession, auth: AuthContext, run_id: str
    ) -> dict[str, int]:
        run = await db.get(MapsCensusRun, run_id)
        if run is None or run.organization_id != auth.org_id:
            raise NotFoundError("Maps census run", run_id)

        places = (
            await db.execute(select(MapsPlace).where(MapsPlace.run_id == run_id))
        ).scalars().all()

        counts = {
            ELIGIBLE_CENTERS_SHEET: 0,
            NEEDS_REVIEW_SHEET: 0,
            PUBLIC_GOVERNMENT_SHEET: 0,
            INDIVIDUAL_PRACTITIONERS_SHEET: 0,
            EXCLUDED_UNRELATED_SHEET: 0,
        }
        for place in places:
            if _is_eligible_center(place):
                counts[ELIGIBLE_CENTERS_SHEET] += 1
            elif _is_review_place(place):
                counts[NEEDS_REVIEW_SHEET] += 1
            elif _is_public_place(place):
                counts[PUBLIC_GOVERNMENT_SHEET] += 1
            elif _is_individual_practitioner(place):
                counts[INDIVIDUAL_PRACTITIONERS_SHEET] += 1
            else:
                counts[EXCLUDED_UNRELATED_SHEET] += 1
        return counts

    def _export_place_row(self, place: MapsPlace) -> list[Any]:
        return [
            _display_text(place.canonical_name or place.raw_name),
            _display_text(place.operator_name),
            _display_choice(place.operator_type),
            _display_choice(place.ownership_status),
            _display_choice(place.funding_type),
            _display_choice(place.facility_type),
            _display_choice(place.care_setting),
            _display_bool(place.residential_accommodation),
            _display_bool(place.medical_detox),
            _display_list(place.addictions_treated),
            _display_list(place.languages_spoken),
            _display_text(place.formatted_address),
            _display_text(place.official_website or place.raw_website),
            _display_text(place.international_phone_number),
            _display_confidence(place.classification_confidence, place.confidence_score),
            _display_text(place.verification_source_url),
            _display_list(place.discovery_sources),
        ]

    def _audit_rows(
        self, run: MapsCensusRun, cells: list[MapsCensusCell], places: list[MapsPlace]
    ) -> list[list[Any]]:
        rows: list[list[Any]] = [
            ["Run", "Country", run.country_name, run.country_code],
            ["Run", "Cells planned", run.cells_total, ""],
            ["Run", "Cells completed", run.cells_completed, ""],
            ["Run", "Places found", run.places_found, ""],
            ["Run", "Relevant places", run.places_classified_relevant, ""],
            ["Run", "Places with website", run.places_with_website, ""],
            ["Run", "Places enriched", run.places_enriched, ""],
            ["Run", "Website refresh attempts", run.website_refresh_attempts, ""],
            ["Run", "Enrichment refresh attempts", run.enrichment_refresh_attempts, ""],
            ["Run", "Workbook places", len(places), ""],
        ]
        rows.extend(_flatten_audit_mapping("Funnel", run.funnel_metrics))
        rows.extend(_flatten_audit_mapping("Cell summary", run.saturation_summary))

        if cells:
            for cell in cells:
                rows.append(
                    [
                        "Cell",
                        cell.query_text,
                        cell.status,
                        "; ".join(
                            [
                                f"region={cell.region_name}",
                                f"city={cell.city_name or 'n/a'}",
                                f"places_found={cell.places_found}",
                                f"new_unique_places={cell.new_unique_places}",
                                f"new_plausible_places={cell.new_plausible_places}",
                            ]
                        ),
                    ]
                )
        else:
            rows.append(["Cell", "Summary", "No cells recorded", ""])
        return rows

    def _write_sheet(
        self, ws: Any, headers: list[str], rows: list[list[Any]], *, table_name: str
    ) -> None:
        ws.append(headers)
        for row in rows:
            ws.append([_safe_cell(value) for value in row])
        self._style_sheet(ws, headers, row_count=len(rows), table_name=table_name)

    def _style_sheet(
        self, ws: Any, headers: list[str], *, row_count: int, table_name: str
    ) -> None:
        header_fill = PatternFill("solid", fgColor="1F3B5B")
        thin = Side(style="thin", color="C9D4E3")
        cell_border = Border(left=thin, right=thin, top=thin, bottom=thin)
        link_font = Font(color="0563C1", underline="single")

        # Header row: bold, centered both ways, wrapped, taller for breathing room.
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF", size=11)
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = cell_border
        ws.row_dimensions[1].height = 28

        text_columns = {
            index for index, header in enumerate(headers, start=1) if header in _TEXT_HEADERS
        }
        url_columns = {
            index for index, header in enumerate(headers, start=1) if header in _URL_HEADERS
        }
        centered_columns = {
            index for index, header in enumerate(headers, start=1) if header in _CENTERED_HEADERS
        }
        widths = {
            index: self._column_width(header) for index, header in enumerate(headers, start=1)
        }

        for row in ws.iter_rows(min_row=2):
            for cell in row:
                if cell.column in centered_columns:
                    cell.alignment = Alignment(
                        horizontal="center", vertical="center", wrap_text=True
                    )
                else:
                    cell.alignment = Alignment(
                        horizontal="left", vertical="center", wrap_text=True
                    )
                cell.border = cell_border
                if cell.column in text_columns and cell.value is not None:
                    cell.number_format = "@"
                if (
                    cell.column in url_columns
                    and isinstance(cell.value, str)
                    and _is_http_url(cell.value)
                ):
                    cell.hyperlink = cell.value
                    cell.font = link_font
            ws.row_dimensions[row[0].row].height = _estimate_row_height(row, widths)

        for index, header in enumerate(headers, start=1):
            ws.column_dimensions[get_column_letter(index)].width = widths[index]

        last_col = get_column_letter(len(headers))
        table_ref = f"A1:{last_col}{row_count + 1}"
        ws.auto_filter.ref = table_ref
        if row_count > 0:
            table = Table(displayName=table_name, ref=table_ref)
            table.tableStyleInfo = TableStyleInfo(
                name="TableStyleMedium2",
                showRowStripes=True,
                showColumnStripes=False,
            )
            ws.add_table(table)
        ws.freeze_panes = "A2"
        ws.sheet_view.showGridLines = False
        ws.sheet_view.zoomScale = 110
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        ws.print_title_rows = "1:1"

    def _column_width(self, header: str) -> int:
        return _WIDE_COLUMNS.get(header, min(max(len(header) + 2, 14), 30))


def _estimate_row_height(row: Any, widths: dict[int, int]) -> float:
    """Enough height to show wrapped content without dead space (≈ 2-4 lines)."""
    max_lines = 1
    for cell in row:
        value = cell.value
        if value is None:
            continue
        text = str(value)
        width = max(widths.get(cell.column, 14), 6)
        longest_word = max((len(word) for word in text.split()), default=0)
        # Wrapped lines ≈ characters / column width, but never fewer than the
        # number of hard newlines, and account for a single very long token.
        wrapped = max(
            text.count("\n") + 1,
            -(-len(text) // width),  # ceil division
            -(-longest_word // width),
        )
        max_lines = max(max_lines, wrapped)
    return min(15 * min(max_lines, 6) + 4, 96)


_ILLEGAL_XLSX_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _safe_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        # openpyxl rejects ASCII control chars that are illegal in OOXML.
        cleaned = _ILLEGAL_XLSX_CHARS.sub("", value)
        if cleaned[:1] in {"=", "+", "-", "@"}:
            # Phone numbers are formatted as text elsewhere; anything else with a
            # formula-like prefix is neutralized so Excel never evaluates it.
            return cleaned
        return cleaned
    return value


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _display_text(value: Any) -> Any:
    if value is None:
        return "Not Specified"
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else "Not Specified"
    return value


def _display_choice(value: str | None) -> str:
    text = _display_text(value)
    if text == "Not Specified":
        return text
    return str(text).replace("_", " ").title()


def _display_bool(value: bool | None) -> str:
    if value is None:
        return "Not Specified"
    return "Yes" if value else "No"


def _display_list(values: list[str] | None) -> str:
    if not values:
        return "Not Specified"
    cleaned = [str(item).strip() for item in values if str(item).strip()]
    return ", ".join(cleaned) if cleaned else "Not Specified"


def _display_confidence(primary: Any, fallback: Any) -> str:
    value = primary if primary is not None else fallback
    if value is None:
        return "Not Specified"
    return f"{float(value):.2f}"


def _flatten_audit_mapping(section: str, payload: dict[str, Any] | None) -> list[list[Any]]:
    if not payload:
        return []
    rows: list[list[Any]] = []
    for key in sorted(payload):
        value = payload[key]
        if isinstance(value, dict):
            for nested_key in sorted(value):
                rows.append(
                    [
                        section,
                        f"{key}.{nested_key}",
                        _display_text(value[nested_key]),
                        "",
                    ]
                )
        else:
            rows.append([section, key, _display_text(value), ""])
    return rows


def _is_eligible_center(place: MapsPlace) -> bool:
    return place.client_eligibility == MapsClientEligibility.ELIGIBLE.value


def _is_review_place(place: MapsPlace) -> bool:
    return place.client_eligibility == MapsClientEligibility.REVIEW.value or (
        place.lifecycle_status == MapsLifecycleStatus.NEEDS_REVIEW.value
    )


def _is_public_place(place: MapsPlace) -> bool:
    return (
        place.lifecycle_status == MapsLifecycleStatus.CONFIRMED_PUBLIC.value
        or place.ownership_status == MapsOwnershipStatus.CONFIRMED_GOVERNMENT.value
        or place.operator_type
        in {
            MapsOperatorType.PUBLIC_HOSPITAL.value,
            MapsOperatorType.GOVERNMENT_AGENCY.value,
        }
    )


def _is_individual_practitioner(place: MapsPlace) -> bool:
    return (
        place.lifecycle_status
        == MapsLifecycleStatus.CONFIRMED_INDIVIDUAL_PRACTITIONER.value
        or place.organization_scope == MapsOrganizationScope.INDIVIDUAL_PRACTICE.value
        or place.operator_type == MapsOperatorType.INDIVIDUAL_PRACTICE.value
        or place.facility_type
        in {
            MapsFacilityType.INDIVIDUAL_ADDICTOLOGIST.value,
            MapsFacilityType.THERAPIST_OR_COUNSELOR.value,
        }
    )


maps_export_service = MapsExportService()
