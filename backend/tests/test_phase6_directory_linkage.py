"""Deterministic directory-observation linkage without external calls."""

from types import SimpleNamespace

import pytest

from app.services.scraping.facility_extraction_provider import (
    ExtractedEvidenceValue, ExtractedFacility,
)
from app.services.scraping.facility_extraction_service import FacilityExtractionService


class _Scalars:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self.rows)


class _ObservationSession:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, statement):
        return _Scalars(self.rows)


def ev(value: str) -> ExtractedEvidenceValue:
    return ExtractedEvidenceValue(value=value, evidence_quote=value)


@pytest.mark.asyncio
async def test_matching_observation_requires_name_and_strong_identity_evidence():
    rows = [
        SimpleNamespace(
            id="similar-only", displayed_facility_name="Alpha Recovery",
            official_website_url="https://other.example",
            displayed_phone="+43 999", displayed_address="Unrelated address",
        ),
        SimpleNamespace(
            id="deterministic-match", displayed_facility_name="Alpha Recovery",
            official_website_url="https://www.alpha.example/about",
            displayed_phone="+43 1 234567", displayed_address="1 Main Street",
        ),
    ]
    facility = ExtractedFacility(
        name=ev("Alpha Recovery"), websites=[ev("https://alpha.example")],
        phones=[ev("+43 (1) 234 567")], addresses=[ev("1 Main Street")],
    )
    service = FacilityExtractionService.__new__(FacilityExtractionService)
    matched = await service._matching_directory_observation(
        _ObservationSession(rows), organization_id="org", execution_id="execution",
        facility=facility,
    )
    assert matched == "deterministic-match"


@pytest.mark.asyncio
async def test_similar_name_without_contact_or_location_is_not_attached():
    row = SimpleNamespace(
        id="similar-only", displayed_facility_name="Alpha Recovery",
        official_website_url="https://unrelated.example",
        displayed_phone="+49 000000", displayed_address="Another country",
    )
    facility = ExtractedFacility(
        name=ev("Alpha Recovery"), websites=[ev("https://alpha.example")],
        phones=[ev("+43 123456")], addresses=[ev("1 Main Street")],
    )
    service = FacilityExtractionService.__new__(FacilityExtractionService)
    assert await service._matching_directory_observation(
        _ObservationSession([row]), organization_id="org", execution_id="execution",
        facility=facility,
    ) is None
