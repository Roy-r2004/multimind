"""Focused candidate decision and deduplication lifecycle tests."""

import pytest
from sqlalchemy import func, select

from app.db.models import (
    ScrapingFacilityCandidate,
    ScrapingFacilityCandidateDuplicate,
    ScrapingFacilityCandidateEvidence,
)
from app.services.scraping.facility_candidate_decision_service import (
    link_probable_duplicates,
    verify_candidate,
)
from app.services.scraping.facility_extraction_provider import (
    ExtractedEvidenceValue,
    ExtractedFacility,
    FacilityExtractionOutput,
)
from app.services.scraping.facility_extraction_service import (
    FacilityExtractionContext,
    FacilityExtractionService,
)
from test_scraping_facility_extraction import FakeProvider, create_document, create_execution


def ev(value: str) -> ExtractedEvidenceValue:
    return ExtractedEvidenceValue(value=value, evidence_quote=value)


async def _extract(db, auth, execution, *, address: str, key: str):
    website = "https://alpha.example"
    body = f"Alpha Recovery\nFrance\nParis\n{address}\n{website}\nCounselling\nLicense ABC"
    document = await create_document(db, auth, execution, body=body)
    output = FacilityExtractionOutput(
        document_relevant=True,
        facilities=[ExtractedFacility(
            name=ev("Alpha Recovery"), physical_country=ev("France"),
            city_or_region=ev("Paris"), addresses=[ev(address)],
            websites=[ev(website)], services=[ev("Counselling")],
            license_or_registration=[ev("License ABC")],
        )])
    summary = await FacilityExtractionService(FakeProvider(output)).extract_one_chunk(
        db, FacilityExtractionContext(
            organization_id=auth.org_id, execution_id=execution.id,
            source_document_id=document.id, idempotency_key=key,
        ))
    return await db.scalar(select(ScrapingFacilityCandidate).where(
        ScrapingFacilityCandidate.extraction_attempt_id == summary.attempt_id))


@pytest.mark.asyncio
async def test_exact_duplicates_share_canonical_identity_without_losing_evidence(db, auth):
    execution = await create_execution(db, auth)
    left = await _extract(db, auth, execution, address="1 Main Street, Paris, France", key="exact-left")
    right = await _extract(db, auth, execution, address="1 Main Street, Paris, France", key="exact-right")
    left_decision = await verify_candidate(
        db, organization_id=auth.org_id, execution_id=execution.id, candidate_id=left.id)
    right_decision = await verify_candidate(
        db, organization_id=auth.org_id, execution_id=execution.id, candidate_id=right.id)
    await db.commit()
    assert left_decision.final_status == right_decision.final_status == "accepted"
    assert left_decision.country_decision == "inside_requested_country"
    assert left_decision.country_reason
    assert left_decision.country_evidence_json
    assert right_decision.canonical_candidate_id == left.id
    assert left.raw_payload["services"][0]["value"] == "Counselling"
    assert left.raw_payload["license_or_registration"][0]["value"] == "License ABC"
    assert await db.scalar(select(func.count()).select_from(
        ScrapingFacilityCandidateEvidence).where(
            ScrapingFacilityCandidateEvidence.facility_candidate_id.in_((left.id, right.id)))) > 2
    replay = await verify_candidate(
        db, organization_id=auth.org_id, execution_id=execution.id, candidate_id=right.id)
    assert replay.id == right_decision.id


@pytest.mark.asyncio
async def test_probable_duplicates_remain_separate_and_relationship_is_idempotent(db, auth):
    execution = await create_execution(db, auth)
    left = await _extract(db, auth, execution, address="1 Main Street, Paris, France", key="prob-left")
    branch = await _extract(db, auth, execution, address="9 River Road, Paris, France", key="prob-right")
    await verify_candidate(db, organization_id=auth.org_id, execution_id=execution.id, candidate_id=left.id)
    await verify_candidate(db, organization_id=auth.org_id, execution_id=execution.id, candidate_id=branch.id)
    assert left.id != branch.id
    assert await link_probable_duplicates(
        db, organization_id=auth.org_id, execution_id=execution.id, candidate_id=branch.id) == 1
    assert await link_probable_duplicates(
        db, organization_id=auth.org_id, execution_id=execution.id, candidate_id=left.id) == 0
    relationship = await db.scalar(select(ScrapingFacilityCandidateDuplicate))
    assert {relationship.left_candidate_id, relationship.right_candidate_id} == {left.id, branch.id}
    assert relationship.relationship == "probable_duplicate"
