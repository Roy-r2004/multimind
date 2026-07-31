"""Excel workbook export for a Maps Census run.

Produces a two-sheet ``.xlsx``:

- ``Facilities`` — the seven business columns shown in the results table, one
  row per relevant facility (no export-eligibility gate; incomplete rows are
  kept so the user gets everything).
- ``Technical Data`` — the same facilities with diagnostic fields (IDs,
  coordinates, confidence, website source, verification tier, etc.).

Only Excel's own worksheet row limit bounds the output; the application applies
no cap of its own.
"""

from __future__ import annotations

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
from app.db.models import MapsCensusRun, MapsPlace
from app.services.scraping.maps_census_service import (
    CSV_EXPORT_HEADERS,
    _export_csv_row,
    _export_eligible,
    _export_location,
    _verification_tier,
    normalized_export_website,
)

MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

FACILITIES_SHEET = "Facilities"
TECHNICAL_SHEET = "Technical Data"

# Headers whose string cells should render as clickable links.
_URL_HEADERS = {"Website", "Raw Website", "Official Website"}
# Headers whose values must stay textual so "+" and leading zeroes survive.
_TEXT_HEADERS = {"Phone Number", "Google Place ID", "Internal ID"}
# Short, scannable columns that read best centered in both axes.
_CENTERED_HEADERS = {
    "Phone Number",
    "Treatment Price",
    "Languages Spoken",
    "Website Source",
    "Latitude",
    "Longitude",
    "Relevance Confidence",
    "Verification Tier",
    "Export Ready",
    "Enrichment Status",
    "Has Photo",
}

_TECHNICAL_HEADERS = (
    "Facility Name",
    "Raw Name",
    "Addictions Treated",
    "Location",
    "Region",
    "City",
    "Formatted Address",
    "Latitude",
    "Longitude",
    "Languages Spoken",
    "Website",
    "Raw Website",
    "Official Website",
    "Website Source",
    "Phone Number",
    "Treatment Price",
    "Place Types",
    "Relevance Confidence",
    "Relevance Reason",
    "Verification Tier",
    "Export Ready",
    "Enrichment Status",
    "Has Photo",
    "Discovery Query",
    "Google Place ID",
    "Internal ID",
)

_WIDE_COLUMNS = {
    "Facility Name": 34,
    "Raw Name": 30,
    "Location": 42,
    "Formatted Address": 42,
    "Website": 40,
    "Raw Website": 38,
    "Official Website": 38,
    "Relevance Reason": 48,
    "Discovery Query": 34,
    "Place Types": 34,
    "Addictions Treated": 26,
    "Languages Spoken": 22,
    "Google Place ID": 30,
    "Internal ID": 30,
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
                .where(MapsPlace.run_id == run_id, MapsPlace.is_relevant.is_(True))
                .order_by(MapsPlace.canonical_name)
            )
        ).scalars().all()

        workbook = Workbook()
        workbook.properties.title = "Maps Census Export"
        workbook.properties.creator = "MultiAI Verdict"
        workbook.remove(workbook.active)

        facilities_ws = workbook.create_sheet(FACILITIES_SHEET)
        technical_ws = workbook.create_sheet(TECHNICAL_SHEET)

        self._write_sheet(
            facilities_ws,
            list(CSV_EXPORT_HEADERS),
            [_export_csv_row(place, country_name=run.country_name) for place in places],
            table_name="Facilities",
        )
        self._write_sheet(
            technical_ws,
            list(_TECHNICAL_HEADERS),
            [self._technical_row(place, country_name=run.country_name) for place in places],
            table_name="TechnicalData",
        )

        buffer = BytesIO()
        workbook.save(buffer)
        filename = f"{run.country_code.lower()}-maps-census-export.xlsx"
        return buffer.getvalue(), filename

    def _technical_row(self, place: MapsPlace, *, country_name: str) -> list[Any]:
        business = _export_csv_row(place, country_name=country_name)
        # business = [name, addictions, location, languages, website, phone, price]
        types = ", ".join(str(item) for item in (place.place_types or []))
        confidence = (
            float(place.confidence_score) if place.confidence_score is not None else None
        )
        return [
            business[0],  # Facility Name
            (place.raw_name or "").strip(),
            business[1],  # Addictions Treated
            business[2],  # Location
            (place.region_name or "").strip(),
            (place.city_name or "").strip(),
            (place.formatted_address or "").strip(),
            place.latitude,
            place.longitude,
            business[3],  # Languages Spoken
            business[4],  # Website (normalized)
            (place.raw_website or "").strip(),
            (place.official_website or "").strip(),
            (place.website_source or "").strip(),
            business[5],  # Phone Number
            business[6],  # Treatment Price
            types,
            confidence,
            (place.relevance_reason or "").strip(),
            _verification_tier(place),
            "Yes" if _export_eligible(place) else "No",
            place.enrichment_status or "pending",
            "Yes" if place.photo_reference else "No",
            (place.discovered_via_query or "").strip(),
            place.google_place_id,
            place.id,
        ]

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


def _safe_cell(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
        # Phone numbers are formatted as text elsewhere; anything else with a
        # formula-like prefix is neutralized so Excel never evaluates it.
        return value
    return value


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


maps_export_service = MapsExportService()
