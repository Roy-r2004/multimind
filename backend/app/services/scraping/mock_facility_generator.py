"""Deterministic fictional rehabilitation facility dataset generation."""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    RehabilitationFacility,
    RehabilitationFacilityAlias,
    RehabilitationFacilityAttribute,
    RehabilitationFacilityContact,
    RehabilitationFacilityLicense,
    RehabilitationFacilityLocation,
    RehabilitationFacilityOperatingHours,
    RehabilitationFacilitySourceLink,
    RehabilitationFacilityStaff,
    RehabilitationFieldEvidence,
    RehabilitationPossibleDuplicate,
    RehabilitationSource,
    RehabilitationUnresolvedField,
    ScrapingCoverageCell,
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingTask,
)

VERIFIED_STATUS = "verified"
SOCIAL_CONTACT_TYPES = {"facebook", "instagram", "linkedin", "youtube", "other_social"}
DOCUMENT_CONTENT_TYPES = {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


class MockFacilityGenerator:
    async def generate(self, db: AsyncSession, execution: ScrapingExecution) -> None:
        await db.refresh(execution)
        if execution.status == ScrapingExecutionStatus.CANCEL_REQUESTED:
            return
        await self._clear_existing(db, execution.id)
        cells = await self._coverage_cells(db, execution.id)
        tasks_by_cell = await self._tasks_by_cell(db, execution.id)
        count = min(8, max(4, len({cell.region_name for cell in cells}) + 3))
        coverage_contexts = _facility_coverage_contexts(
            cells or [
            _CellFallback(
                id=None,
                region_name=execution.country_name,
                region_code=execution.country_code.lower(),
                language_code="en",
                language_name="English",
                source_category="mock directory",
            )
            ],
            count,
        )

        facilities: list[RehabilitationFacility] = []
        now = datetime.now(UTC)
        for index in range(1, count + 1):
            cell = coverage_contexts[index - 1]
            name = _facility_name(index)
            verified = index % 3 != 0
            city = _city_name(cell.region_name, index)
            facility = RehabilitationFacility(
                execution_id=execution.id,
                organization_id=execution.organization_id,
                stable_key=_stable_key(execution.id, execution.country_code, cell.region_name, index),
                canonical_name=name,
                original_language_name=f"{name} Mock Local Name",
                description=(
                    f"{name} is a fictional rehabilitation-center record generated for testing. "
                    "It does not describe a real facility."
                ),
                facility_type=_pick(index, ["residential", "outpatient", "clinic", "day_program"]),
                organization_type=_pick(index, ["nonprofit_mock", "private_mock", "public_mock"]),
                operational_status=_pick(index, ["active_mock", "unknown_mock"]),
                country_code=execution.country_code,
                country_name=execution.country_name,
                primary_region=cell.region_name,
                primary_city=city,
                primary_address=f"Mock Address {index}, {city}, {execution.country_name}",
                latitude=None,
                longitude=None,
                primary_website=f"https://facility-{index:03d}.example.invalid",
                verification_status=VERIFIED_STATUS if verified else "unverified",
                confidence_score=Decimal("0.9100") if verified else Decimal("0.6200"),
                duplicate_status="possible_duplicate" if index == 2 else "unique",
                human_review_status="required" if index in {2, 3} else "not_required",
                is_mock=True,
                last_verified_at=now if verified else None,
            )
            db.add(facility)
            facilities.append(facility)
        await db.flush()

        sources: list[RehabilitationSource] = []
        for index, facility in enumerate(facilities, start=1):
            cell = coverage_contexts[index - 1]
            task_id = tasks_by_cell.get(cell.id) if cell.id else None
            for source_number, category in enumerate(["official_mock", "directory_mock"], start=1):
                blocked = index == len(facilities) and source_number == 2
                url = f"https://source-{index:03d}-{source_number}.example.invalid/mock-page"
                source = RehabilitationSource(
                    execution_id=execution.id,
                    coverage_cell_id=cell.id,
                    task_id=task_id,
                    original_url=url,
                    canonical_url=url,
                    domain="example.invalid",
                    source_category=category,
                    discovery_query=(
                        f"Mock rehab center {execution.country_name} {cell.region_name} "
                        f"{cell.language_name} {cell.source_category}"
                    ),
                    page_title=f"Mock Source {index}-{source_number}",
                    language_code=cell.language_code,
                    region=cell.region_name,
                    fetch_status="blocked" if blocked else "fetched",
                    http_status=403 if blocked else 200,
                    content_type="application/pdf" if source_number == 2 and index == 1 else "text/html",
                    content_hash=_hash(url),
                    retrieved_at=now,
                    blocked_reason="Mock blocked source for export testing." if blocked else None,
                    is_mock=True,
                )
                db.add(source)
                sources.append(source)
        shared_sources: dict[str, RehabilitationSource] = {}
        for cell in coverage_contexts:
            context_key = _coverage_context_key(cell)
            if context_key in shared_sources:
                continue
            url = (
                f"https://shared-{_slug(cell.region_name)}-"
                f"{_hash(context_key)[:8]}.example.invalid/mock-index"
            )
            shared_source = RehabilitationSource(
                execution_id=execution.id,
                coverage_cell_id=cell.id,
                task_id=tasks_by_cell.get(cell.id),
                original_url=url,
                canonical_url=url,
                domain="example.invalid",
                source_category="shared_directory_mock",
                discovery_query=(
                    f"Mock shared directory {execution.country_name} {cell.region_name} "
                    f"{cell.language_name}"
                ),
                page_title=f"Mock Shared Directory {cell.region_name}",
                language_code=cell.language_code,
                region=cell.region_name,
                fetch_status="fetched",
                http_status=200,
                content_type="text/html",
                content_hash=_hash(f"shared:{execution.id}:{cell.region_name}:{cell.language_code}"),
                retrieved_at=now,
                is_mock=True,
            )
            db.add(shared_source)
            shared_sources[context_key] = shared_source
        await db.flush()

        for index, facility in enumerate(facilities, start=1):
            cell = coverage_contexts[index - 1]
            facility_sources = [
                sources[(index - 1) * 2],
                sources[(index - 1) * 2 + 1],
                shared_sources[_coverage_context_key(cell)],
            ]
            self._add_children(db, facility, facility_sources, index)

        if len(facilities) >= 2:
            left, right = sorted([facilities[0].id, facilities[1].id])
            db.add(
                RehabilitationPossibleDuplicate(
                    execution_id=execution.id,
                    left_facility_id=left,
                    right_facility_id=right,
                    match_score=Decimal("0.7800"),
                    matching_reasons="Mock similar names and region; human review required.",
                    resolution_status="possible",
                    is_mock=True,
                )
            )
        await db.flush()

    async def refresh_execution_metrics(self, db: AsyncSession, execution: ScrapingExecution) -> None:
        execution.sources_discovered = await _count(
            db, select(func.count(RehabilitationSource.id)).where(RehabilitationSource.execution_id == execution.id)
        )
        execution.records_extracted = await _count(
            db,
            select(func.count(RehabilitationFacility.id)).where(
                RehabilitationFacility.execution_id == execution.id
            ),
        )
        execution.records_verified = await _count(
            db,
            select(func.count(RehabilitationFacility.id)).where(
                RehabilitationFacility.execution_id == execution.id,
                RehabilitationFacility.verification_status == VERIFIED_STATUS,
            ),
        )
        execution.duplicates_detected = await _count(
            db,
            select(func.count(RehabilitationPossibleDuplicate.id)).where(
                RehabilitationPossibleDuplicate.execution_id == execution.id
            ),
        )
        execution.blocked_sources = await _count(
            db,
            select(func.count(RehabilitationSource.id)).where(
                RehabilitationSource.execution_id == execution.id,
                RehabilitationSource.fetch_status == "blocked",
            ),
        )
        execution.documents_found = await _count(
            db,
            select(func.count(RehabilitationSource.id)).where(
                RehabilitationSource.execution_id == execution.id,
                RehabilitationSource.content_type.in_(DOCUMENT_CONTENT_TYPES),
            ),
        )

    async def _clear_existing(self, db: AsyncSession, execution_id: str) -> None:
        facility_ids = (
            await db.execute(
                select(RehabilitationFacility.id).where(RehabilitationFacility.execution_id == execution_id)
            )
        ).scalars().all()
        source_ids = (
            await db.execute(select(RehabilitationSource.id).where(RehabilitationSource.execution_id == execution_id))
        ).scalars().all()
        if facility_ids:
            for model in [
                RehabilitationUnresolvedField,
                RehabilitationPossibleDuplicate,
                RehabilitationFieldEvidence,
                RehabilitationFacilitySourceLink,
                RehabilitationFacilityOperatingHours,
                RehabilitationFacilityLicense,
                RehabilitationFacilityStaff,
                RehabilitationFacilityAttribute,
                RehabilitationFacilityContact,
                RehabilitationFacilityLocation,
                RehabilitationFacilityAlias,
            ]:
                column = getattr(model, "facility_id", None)
                if column is not None:
                    await db.execute(delete(model).where(column.in_(facility_ids)))
            await db.execute(
                delete(RehabilitationPossibleDuplicate).where(
                    RehabilitationPossibleDuplicate.left_facility_id.in_(facility_ids)
                    | RehabilitationPossibleDuplicate.right_facility_id.in_(facility_ids)
                )
            )
            await db.execute(delete(RehabilitationFacility).where(RehabilitationFacility.id.in_(facility_ids)))
        if source_ids:
            await db.execute(delete(RehabilitationSource).where(RehabilitationSource.id.in_(source_ids)))

    async def _coverage_cells(self, db: AsyncSession, execution_id: str) -> list[ScrapingCoverageCell]:
        result = await db.execute(
            select(ScrapingCoverageCell)
            .where(ScrapingCoverageCell.execution_id == execution_id)
            .order_by(
                ScrapingCoverageCell.region_name,
                ScrapingCoverageCell.language_code,
                ScrapingCoverageCell.language_name,
                ScrapingCoverageCell.source_category,
                ScrapingCoverageCell.id,
            )
        )
        return list(result.scalars().all())

    async def _tasks_by_cell(self, db: AsyncSession, execution_id: str) -> dict[str | None, str]:
        result = await db.execute(
            select(ScrapingTask.coverage_cell_id, ScrapingTask.id)
            .where(ScrapingTask.execution_id == execution_id)
            .order_by(ScrapingTask.created_at)
        )
        mapping: dict[str | None, str] = {}
        for coverage_cell_id, task_id in result.all():
            mapping.setdefault(coverage_cell_id, task_id)
        return mapping

    def _add_children(
        self,
        db: AsyncSession,
        facility: RehabilitationFacility,
        sources: list[RehabilitationSource],
        index: int,
    ) -> None:
        db.add_all(
            [
                RehabilitationFacilityAlias(
                    facility_id=facility.id,
                    name=facility.canonical_name,
                    language_code="en",
                    alias_type="official",
                    is_primary=True,
                    is_mock=True,
                ),
                RehabilitationFacilityAlias(
                    facility_id=facility.id,
                    name=f"Mock Alternate Name {index}",
                    language_code="en",
                    alias_type="alternate",
                    is_mock=True,
                ),
                RehabilitationFacilityLocation(
                    facility_id=facility.id,
                    location_type="main",
                    location_name=f"{facility.canonical_name} Main Mock Location",
                    country_code=facility.country_code,
                    country_name=facility.country_name,
                    region=facility.primary_region,
                    city=facility.primary_city,
                    area=f"Mock Area {index}",
                    full_address=facility.primary_address,
                    postal_code=f"MOCK-{index:03d}",
                    latitude=facility.latitude,
                    longitude=facility.longitude,
                    is_primary=True,
                    verification_status=facility.verification_status,
                    confidence_score=facility.confidence_score,
                    is_mock=True,
                ),
                RehabilitationFacilityContact(
                    facility_id=facility.id,
                    contact_type="phone",
                    label="Mock main phone",
                    value=f"MOCK-PHONE-{index:03d}",
                    normalized_value=f"MOCK-PHONE-{index:03d}",
                    is_primary=True,
                    available_24_7=index % 2 == 0,
                    verification_status=facility.verification_status,
                    confidence_score=facility.confidence_score,
                    is_mock=True,
                ),
                RehabilitationFacilityContact(
                    facility_id=facility.id,
                    contact_type="email",
                    label="Mock intake email",
                    value=f"contact-{index:03d}@example.invalid",
                    normalized_value=f"contact-{index:03d}@example.invalid",
                    is_primary=True,
                    verification_status=facility.verification_status,
                    confidence_score=facility.confidence_score,
                    is_mock=True,
                ),
                RehabilitationFacilityContact(
                    facility_id=facility.id,
                    contact_type="website",
                    label="Mock website",
                    value=facility.primary_website or "",
                    normalized_value=facility.primary_website,
                    is_primary=True,
                    verification_status=facility.verification_status,
                    confidence_score=facility.confidence_score,
                    is_mock=True,
                ),
                RehabilitationFacilityContact(
                    facility_id=facility.id,
                    contact_type="facebook",
                    label="Mock Facebook",
                    value=f"https://facebook.example.invalid/mock-facility-{index:03d}",
                    verification_status="unverified",
                    confidence_score=Decimal("0.5000"),
                    is_mock=True,
                ),
                RehabilitationFacilityStaff(
                    facility_id=facility.id,
                    name=f"Mock Clinician {index}",
                    role="Mock Lead Clinician",
                    specialty="Mock addiction care",
                    credentials=f"MOCK-CREDENTIAL-{index:03d}",
                    public_profile_url=f"https://staff-{index:03d}.example.invalid/profile",
                    verification_status=facility.verification_status,
                    confidence_score=facility.confidence_score,
                    is_mock=True,
                ),
                RehabilitationFacilityLicense(
                    facility_id=facility.id,
                    record_type="license",
                    name="Mock Rehabilitation License",
                    issuing_authority="Mock Public Authority",
                    identifier=f"MOCK-LICENSE-{index:03d}",
                    status="mock_active",
                    valid_from=date(2026, 1, 1),
                    valid_until=date(2026, 12, 31),
                    verification_status=facility.verification_status,
                    confidence_score=facility.confidence_score,
                    is_mock=True,
                ),
            ]
        )
        if index == 1:
            db.add(
                RehabilitationFacilityLocation(
                    facility_id=facility.id,
                    location_type="branch",
                    location_name=f"{facility.canonical_name} Mock Branch",
                    country_code=facility.country_code,
                    country_name=facility.country_name,
                    region=facility.primary_region,
                    city=f"{facility.primary_city} Mock Branch",
                    full_address=f"Mock Branch Address {index}, {facility.country_name}",
                    verification_status="unverified",
                    confidence_score=Decimal("0.5500"),
                    is_mock=True,
                )
            )
        for group, key, display, value_type, value in _attributes(index):
            typed_values = _typed_attribute_values(value)
            db.add(
                RehabilitationFacilityAttribute(
                    facility_id=facility.id,
                    attribute_group=group,
                    attribute_key=key,
                    display_name=display,
                    value_type=value_type,
                    value_boolean=typed_values["value_boolean"],
                    value_number=typed_values["value_number"],
                    value_text=typed_values["value_text"],
                    value_unit="years" if key == "minimum_age" else None,
                    currency_code="USD" if key == "estimated_monthly_price" else None,
                    period="month" if key == "estimated_monthly_price" else None,
                    details=f"Mock {display.lower()} detail.",
                    verification_status=facility.verification_status,
                    confidence_score=facility.confidence_score,
                    is_mock=True,
                )
            )
        for day in range(7):
            db.add(
                RehabilitationFacilityOperatingHours(
                    facility_id=facility.id,
                    day_of_week=day,
                    opens_at=None if index % 2 == 0 else time(9, 0),
                    closes_at=None if index % 2 == 0 else time(17, 0),
                    is_closed=False,
                    is_24_hours=index % 2 == 0,
                    notes="Mock 24/7 operation." if index % 2 == 0 else "Mock business hours.",
                    is_mock=True,
                )
            )
        for source_index, source in enumerate(sources):
            db.add(
                RehabilitationFacilitySourceLink(
                    facility_id=facility.id,
                    source_id=source.id,
                    relationship_type="official" if source_index == 0 else "supporting",
                    is_primary=source_index == 0,
                )
            )
            db.add(
                RehabilitationFieldEvidence(
                    facility_id=facility.id,
                    source_id=source.id,
                    field_path="facility.canonical_name" if source_index == 0 else "facility.primary_website",
                    extracted_value=facility.canonical_name if source_index == 0 else facility.primary_website,
                    evidence_text=(
                        f"Mock evidence snippet for {facility.canonical_name}; "
                        "generated locally for testing only."
                    ),
                    page_title=source.page_title,
                    source_url_snapshot=source.canonical_url,
                    language_code=source.language_code,
                    extraction_method="deterministic_mock",
                    verification_status=facility.verification_status,
                    confidence_score=facility.confidence_score,
                    is_mock=True,
                )
            )
        if index == 1:
            db.add(
                RehabilitationUnresolvedField(
                    facility_id=facility.id,
                    field_path="pricing_payment.private_room_rate",
                    unresolved_status="searched_not_found",
                    reason="Mock searched source did not contain this field.",
                    recommended_follow_up="Review another mock source in future tests.",
                    source_id=sources[0].id,
                    is_mock=True,
                )
            )
        if index == 2:
            db.add(
                RehabilitationUnresolvedField(
                    facility_id=facility.id,
                    field_path="facility.operational_status",
                    unresolved_status="conflicting",
                    reason="Mock source conflict created for human review testing.",
                    recommended_follow_up="Resolve mock conflict manually.",
                    source_id=sources[1].id,
                    is_mock=True,
                )
            )


class _CellFallback:
    def __init__(
        self,
        *,
        id: str | None,
        region_name: str,
        region_code: str | None,
        language_code: str | None,
        language_name: str,
        source_category: str,
    ) -> None:
        self.id = id
        self.region_name = region_name
        self.region_code = region_code
        self.language_code = language_code
        self.language_name = language_name
        self.source_category = source_category


async def _count(db: AsyncSession, query: Any) -> int:
    return int((await db.execute(query)).scalar_one() or 0)


def _facility_name(index: int) -> str:
    names = [
        "Mock Cedar Recovery Center",
        "Mock Coastal Rehabilitation Clinic",
        "Mock Northern Wellness Institute",
        "Mock Harbor Recovery Program",
        "Mock Summit Rehabilitation Center",
        "Mock Valley Wellness Clinic",
        "Mock Horizon Recovery Institute",
        "Mock Lakeside Rehabilitation Program",
    ]
    return names[index - 1]


def _pick(index: int, values: list[str]) -> str:
    return values[(index - 1) % len(values)]


def _city_name(region: str | None, index: int) -> str:
    base = (region or "Mock Region").strip() or "Mock Region"
    return f"{base} Mock City {index}"


def _stable_key(execution_id: str, country_code: str, region: str | None, index: int) -> str:
    digest = _hash(f"{execution_id}:{country_code}:{region}:{index}")[:12]
    return f"mock-{country_code.lower()}-{index:03d}-{digest}"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _facility_coverage_contexts(
    cells: list[ScrapingCoverageCell | _CellFallback],
    count: int,
) -> list[ScrapingCoverageCell | _CellFallback]:
    ordered_cells = sorted(
        cells,
        key=lambda cell: (
            cell.region_name.casefold(),
            (cell.language_code or "").casefold(),
            cell.source_category.casefold(),
            cell.id or "",
        ),
    )

    cells_by_region: dict[str, list[ScrapingCoverageCell | _CellFallback]] = {}
    cells_by_language: dict[str, list[ScrapingCoverageCell | _CellFallback]] = {}
    cells_by_region_language: dict[
        tuple[str, str], list[ScrapingCoverageCell | _CellFallback]
    ] = {}
    region_order: list[str] = []
    language_order: list[str] = []
    for cell in ordered_cells:
        region_key = cell.region_name.casefold()
        if region_key not in cells_by_region:
            cells_by_region[region_key] = []
            region_order.append(region_key)
        cells_by_region[region_key].append(cell)

        language_key = (cell.language_code or "").casefold()
        if language_key:
            if language_key not in cells_by_language:
                cells_by_language[language_key] = []
                language_order.append(language_key)
            cells_by_language[language_key].append(cell)
            cells_by_region_language.setdefault((region_key, language_key), []).append(cell)

    contexts: list[ScrapingCoverageCell | _CellFallback] = []
    selection_counts: dict[tuple[str, str], int] = {}
    for index in range(count):
        region_key = region_order[index % len(region_order)]
        language_key = language_order[index % len(language_order)] if language_order else ""
        selection_key = (region_key, language_key)
        candidates = cells_by_region_language.get(selection_key)
        if not candidates:
            candidates = cells_by_region.get(region_key)
        if not candidates and language_key:
            candidates = cells_by_language.get(language_key)
        if not candidates:
            candidates = ordered_cells
        cycle = selection_counts.get(selection_key, 0)
        contexts.append(candidates[cycle % len(candidates)])
        selection_counts[selection_key] = cycle + 1
    return contexts


def _slug(value: str) -> str:
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value)
    parts = [part for part in cleaned.split("-") if part]
    return "-".join(parts) or "region"


def _coverage_context_key(cell: ScrapingCoverageCell | _CellFallback) -> str:
    return "|".join(
        [
            cell.id or "",
            cell.region_name.casefold(),
            (cell.language_code or "").casefold(),
            cell.source_category.casefold(),
        ]
    )


def _typed_attribute_values(value: bool | Decimal | int | float | str | None) -> dict[str, Any]:
    typed_values: dict[str, Any] = {
        "value_boolean": None,
        "value_number": None,
        "value_text": None,
    }
    if isinstance(value, bool):
        typed_values["value_boolean"] = value
        return typed_values
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Mock facility attribute Decimal value must be finite.")
        typed_values["value_number"] = value
        return typed_values
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Mock facility attribute numeric value must be finite.")
        typed_values["value_number"] = Decimal(str(value))
        return typed_values
    if isinstance(value, str):
        typed_values["value_text"] = value
        return typed_values
    if value is None:
        return typed_values
    raise TypeError(f"Unsupported mock facility attribute value type: {type(value).__name__}")


def _attributes(
    index: int,
) -> list[tuple[str, str, str, str, bool | Decimal | int | float | str | None]]:
    return [
        ("treatment_service", "detoxification", "Detoxification", "boolean", index % 2 == 0),
        ("treatment_service", "counseling", "Counseling", "boolean", True),
        ("program", "residential_30_day", "Residential 30 Day", "text", "Mock available"),
        ("population_served", "adults", "Adults", "boolean", True),
        ("admission_eligibility", "minimum_age", "Minimum Age", "number", 18),
        ("pricing_payment", "insurance_accepted", "Insurance Accepted", "boolean", index % 2 == 1),
        ("pricing_payment", "estimated_monthly_price", "Estimated Monthly Price", "number", 1200 + index),
        ("amenity", "mock_transportation", "Mock Transportation", "boolean", index % 2 == 0),
    ]


mock_facility_generator = MockFacilityGenerator()
