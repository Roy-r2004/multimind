"""Tests for the Maps Census Excel workbook export."""

from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import load_workbook

from app.core.dependencies import AuthContext
from app.db.models import MapsCensusRun, MapsCensusStatus, MapsPlace
from app.services.scraping.maps_census_service import CSV_EXPORT_HEADERS
from app.services.scraping.maps_export_service import MIME_XLSX, maps_export_service


async def _create_run(db, auth: AuthContext) -> MapsCensusRun:
    run = MapsCensusRun(
        organization_id=auth.org_id,
        created_by=auth.user.id,
        country_code="DZ",
        country_name="Algeria",
        status=MapsCensusStatus.COMPLETED,
    )
    db.add(run)
    await db.flush()
    await db.commit()
    return run


def _facilities_sheet(content: bytes):
    workbook = load_workbook(BytesIO(content))
    return workbook, workbook["Facilities"]


@pytest.mark.asyncio
async def test_export_xlsx_includes_every_relevant_row_even_incomplete(db, auth):
    run = await _create_run(db, auth)
    complete = MapsPlace(
        run_id=run.id,
        google_place_id="complete",
        raw_name="Complete Rehab",
        canonical_name="Complete Rehab",
        is_relevant=True,
        confidence_score=0.95,
        formatted_address="1 Rehab Lane, Algiers",
        official_website="https://complete.example/",
        international_phone_number="+213 21 00 00 00",
        addictions_treated=["Alcohol"],
    )
    incomplete = MapsPlace(
        run_id=run.id,
        google_place_id="incomplete",
        raw_name="Incomplete Center",
        canonical_name="Incomplete Center",
        is_relevant=True,
        confidence_score=0.40,
    )
    excluded = MapsPlace(
        run_id=run.id,
        google_place_id="excluded",
        raw_name="Excluded Clinic",
        canonical_name="Excluded Clinic",
        is_relevant=False,
        confidence_score=0.95,
        formatted_address="9 Skip Rd, Oran",
    )
    db.add_all([complete, incomplete, excluded])
    await db.commit()

    content, filename = await maps_export_service.build_workbook(db, auth, run.id)

    assert filename == "dz-maps-census-export.xlsx"
    workbook, sheet = _facilities_sheet(content)
    assert workbook.sheetnames == ["Facilities", "Technical Data"]
    names = [row[0].value for row in sheet.iter_rows(min_row=2)]
    assert "Complete Rehab" in names
    assert "Incomplete Center" in names  # kept despite failing export-eligibility
    assert "Excluded Clinic" not in names  # irrelevant rows never exported


@pytest.mark.asyncio
async def test_export_xlsx_facilities_headers_match_table(db, auth):
    run = await _create_run(db, auth)
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="p1",
            raw_name="Rehab One",
            canonical_name="Rehab One",
            is_relevant=True,
            confidence_score=0.9,
            formatted_address="1 Street, Algiers",
        )
    )
    await db.commit()

    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    _, sheet = _facilities_sheet(content)
    header = [cell.value for cell in sheet[1]]
    assert header == list(CSV_EXPORT_HEADERS)


@pytest.mark.asyncio
async def test_export_xlsx_uses_placeholders_for_missing_values(db, auth):
    run = await _create_run(db, auth)
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="bare",
            raw_name="Bare Rehab",
            canonical_name="Bare Rehab",
            is_relevant=True,
            confidence_score=0.9,
            formatted_address="1 Street, Algiers",
        )
    )
    await db.commit()

    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    _, sheet = _facilities_sheet(content)
    row = {cell.column_letter: cell.value for cell in sheet[2]}
    values = list(row.values())
    assert "Not Specified" in values
    assert "Contact for pricing" in values


@pytest.mark.asyncio
async def test_export_xlsx_preserves_arabic_names(db, auth):
    run = await _create_run(db, auth)
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="ar",
            raw_name="مركز بوشاوي للادمان",
            canonical_name="مركز بوشاوي للادمان",
            is_relevant=True,
            confidence_score=0.9,
            formatted_address="Chéraga, Algiers",
        )
    )
    await db.commit()

    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    _, sheet = _facilities_sheet(content)
    names = [row[0].value for row in sheet.iter_rows(min_row=2)]
    assert "مركز بوشاوي للادمان" in names


@pytest.mark.asyncio
async def test_export_xlsx_website_is_hyperlinked_and_phone_is_text(db, auth):
    run = await _create_run(db, auth)
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="linky",
            raw_name="Linked Rehab",
            canonical_name="Linked Rehab",
            is_relevant=True,
            confidence_score=0.95,
            formatted_address="1 Rehab Lane, Algiers",
            official_website="https://linked.example/",
            international_phone_number="+213 555 00 11 22",
        )
    )
    await db.commit()

    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    _, sheet = _facilities_sheet(content)
    header = [cell.value for cell in sheet[1]]
    website_col = header.index("Website") + 1
    phone_col = header.index("Phone Number") + 1
    website_cell = sheet.cell(row=2, column=website_col)
    phone_cell = sheet.cell(row=2, column=phone_col)
    assert website_cell.hyperlink is not None
    assert website_cell.hyperlink.target == "https://linked.example/"
    assert isinstance(phone_cell.value, str)
    assert phone_cell.value.startswith("+213")
    assert phone_cell.number_format == "@"


@pytest.mark.asyncio
async def test_export_xlsx_has_frozen_header_and_autofilter(db, auth):
    run = await _create_run(db, auth)
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="f1",
            raw_name="Rehab One",
            canonical_name="Rehab One",
            is_relevant=True,
            confidence_score=0.9,
            formatted_address="1 Street, Algiers",
        )
    )
    await db.commit()

    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    _, sheet = _facilities_sheet(content)
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref is not None


@pytest.mark.asyncio
async def test_export_xlsx_alignment_is_readable(db, auth):
    run = await _create_run(db, auth)
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="align-1",
            raw_name="Alignment Rehab",
            canonical_name="Alignment Rehab",
            is_relevant=True,
            confidence_score=0.9,
            formatted_address="1 Long Street Name, Algiers",
            official_website="https://align.example/",
            international_phone_number="+213 555 00 00",
        )
    )
    await db.commit()

    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    _, sheet = _facilities_sheet(content)
    header = [cell.value for cell in sheet[1]]

    # Header row: centered both ways, wrapped, and taller than default.
    header_cell = sheet.cell(row=1, column=1)
    assert header_cell.alignment.horizontal == "center"
    assert header_cell.alignment.vertical == "center"
    assert header_cell.alignment.wrap_text is True
    assert (sheet.row_dimensions[1].height or 0) >= 24

    # Long free-text columns: left-aligned, vertically centered, wrapped.
    name_col = header.index("Facility Name") + 1
    name_cell = sheet.cell(row=2, column=name_col)
    assert name_cell.alignment.horizontal == "left"
    assert name_cell.alignment.vertical == "center"
    assert name_cell.alignment.wrap_text is True

    # Short scannable columns: centered both ways.
    phone_col = header.index("Phone Number") + 1
    phone_cell = sheet.cell(row=2, column=phone_col)
    assert phone_cell.alignment.horizontal == "center"
    assert phone_cell.alignment.vertical == "center"


@pytest.mark.asyncio
async def test_export_xlsx_technical_short_columns_are_centered(db, auth):
    run = await _create_run(db, auth)
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="align-tech",
            raw_name="Tech Align",
            canonical_name="Tech Align",
            is_relevant=True,
            confidence_score=0.88,
            formatted_address="2 Street, Algiers",
            latitude=36.75,
            longitude=3.06,
        )
    )
    await db.commit()

    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    workbook = load_workbook(BytesIO(content))
    sheet = workbook["Technical Data"]
    header = [cell.value for cell in sheet[1]]
    for short_header in ("Relevance Confidence", "Latitude", "Longitude", "Export Ready"):
        column = header.index(short_header) + 1
        cell = sheet.cell(row=2, column=column)
        assert cell.alignment.horizontal == "center", short_header
        assert cell.alignment.vertical == "center", short_header


@pytest.mark.asyncio
async def test_export_xlsx_technical_sheet_carries_diagnostics(db, auth):
    run = await _create_run(db, auth)
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="tech-1",
            raw_name="Tech Rehab",
            canonical_name="Tech Rehab",
            is_relevant=True,
            confidence_score=0.83,
            formatted_address="5 Data St, Algiers",
            official_website="https://tech.example/",
            website_source="llm_social",
            relevance_reason="explicit addiction center",
            discovered_via_query="rehab Algiers",
        )
    )
    await db.commit()

    content, _ = await maps_export_service.build_workbook(db, auth, run.id)
    workbook = load_workbook(BytesIO(content))
    sheet = workbook["Technical Data"]
    header = [cell.value for cell in sheet[1]]
    for expected in (
        "Google Place ID",
        "Website Source",
        "Relevance Confidence",
        "Relevance Reason",
        "Verification Tier",
        "Discovery Query",
    ):
        assert expected in header
    reason_col = header.index("Relevance Reason") + 1
    assert sheet.cell(row=2, column=reason_col).value == "explicit addiction center"


@pytest.mark.asyncio
async def test_export_xlsx_returns_mime_and_rejects_other_orgs(db, auth):
    run = await _create_run(db, auth)
    db.add(
        MapsPlace(
            run_id=run.id,
            google_place_id="p1",
            raw_name="Rehab One",
            canonical_name="Rehab One",
            is_relevant=True,
            confidence_score=0.9,
            formatted_address="1 Street, Algiers",
        )
    )
    await db.commit()

    assert MIME_XLSX.endswith("spreadsheetml.sheet")

    from app.core.exceptions import NotFoundError

    other = AuthContext(user=auth.user, org_id="different-org", role=auth.role)
    with pytest.raises(NotFoundError):
        await maps_export_service.build_workbook(db, other, run.id)
