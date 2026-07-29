"""Persistence service for Python-authoritative Phase 7 candidate decisions."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ScrapingExecution,
    ScrapingFacilityCandidate,
    ScrapingFacilityCandidateDecision,
    ScrapingFacilityCandidateDuplicate,
)
from app.services.scraping.facility_candidate_verification import (
    CountryEvidence,
    exact_identity_keys,
    final_status,
    identity_fingerprint,
    normalize_address,
    normalize_city_region,
    normalize_email,
    normalize_facility_type,
    normalize_name,
    normalize_phone,
    normalize_website,
    probable_duplicate_score,
    verify_country,
)

ALGORITHM_VERSION = "phase7-deterministic-v1"


def _first(payload: dict, key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, list):
        value = value[0] if value else None
        if isinstance(value, dict):
            value = value.get("value")
    return str(value).strip() if value else None


async def verify_candidate(
    db: AsyncSession, *, organization_id: str, execution_id: str, candidate_id: str,
) -> ScrapingFacilityCandidateDecision:
    execution = await db.scalar(select(ScrapingExecution).where(
        ScrapingExecution.id == execution_id,
        ScrapingExecution.organization_id == organization_id,
    ))
    candidate = await db.scalar(select(ScrapingFacilityCandidate).where(
        ScrapingFacilityCandidate.id == candidate_id,
        ScrapingFacilityCandidate.organization_id == organization_id,
        ScrapingFacilityCandidate.execution_id == execution_id,
    ))
    if execution is None or candidate is None:
        raise LookupError("owned execution or candidate not found")
    existing = await db.scalar(select(ScrapingFacilityCandidateDecision).where(
        ScrapingFacilityCandidateDecision.organization_id == organization_id,
        ScrapingFacilityCandidateDecision.execution_id == execution_id,
        ScrapingFacilityCandidateDecision.facility_candidate_id == candidate_id,
    ))
    if existing:
        return existing
    raw = candidate.raw_payload or {}
    address, city, country = _first(raw, "addresses"), _first(raw, "city_or_region"), _first(raw, "physical_country")
    website, phone, email = _first(raw, "websites"), _first(raw, "phones"), _first(raw, "emails")
    evidence = []
    if address:
        evidence.append(CountryEvidence("full_address", address, "candidate.addresses"))
    if city:
        evidence.append(CountryEvidence("city_or_region", city, "candidate.city_or_region"))
    if country:
        evidence.append(CountryEvidence("explicit_physical_location", country, "candidate.physical_country"))
    country_result = verify_country(execution.country_code, evidence)
    canonical_website, domain = normalize_website(website)
    normalized = {
        "name": normalize_name(candidate.raw_name),
        "aliases": sorted(filter(None, (normalize_name(_first({"v": value}, "v")) for value in raw.get("aliases", [])))),
        "facility_type": normalize_facility_type(_first(raw, "facility_type")),
        "address": normalize_address(address),
        "city_or_region": normalize_city_region(city),
        "website": canonical_website,
        "domain": domain,
        "phone": normalize_phone(phone),
        "email": normalize_email(email),
        # Values stay observed; no service inference or vocabulary expansion.
        "services": raw.get("services", []),
        "programs": raw.get("programs", []),
        "license_or_registration": raw.get("license_or_registration", []),
    }
    fingerprint = identity_fingerprint(
        name=candidate.raw_name, address=address, website=website, phone=phone, email=email
    )
    status, reason = final_status(
        country_result, has_name=bool(normalized["name"]),
        has_physical_locator=bool(normalized["address"] or normalized["city_or_region"]),
    )
    canonical = await db.scalar(select(ScrapingFacilityCandidateDecision).where(
        ScrapingFacilityCandidateDecision.organization_id == organization_id,
        ScrapingFacilityCandidateDecision.execution_id == execution_id,
        ScrapingFacilityCandidateDecision.identity_fingerprint == fingerprint,
    ).order_by(ScrapingFacilityCandidateDecision.created_at, ScrapingFacilityCandidateDecision.id).limit(1))
    row = ScrapingFacilityCandidateDecision(
        organization_id=organization_id, execution_id=execution_id,
        facility_candidate_id=candidate_id,
        canonical_candidate_id=canonical.facility_candidate_id if canonical else candidate_id,
        requested_country_code=execution.country_code,
        country_decision=country_result.decision.value,
        country_reason=country_result.reason_code,
        country_evidence_json=[item.__dict__ for item in country_result.evidence],
        normalized_payload=normalized, identity_fingerprint=fingerprint,
        final_status=status.value, final_reason=reason, algorithm_version=ALGORITHM_VERSION,
    )
    db.add(row)
    await db.flush()
    return row


async def link_probable_duplicates(
    db: AsyncSession, *, organization_id: str, execution_id: str, candidate_id: str,
) -> int:
    current = await db.scalar(select(ScrapingFacilityCandidateDecision).where(
        ScrapingFacilityCandidateDecision.organization_id == organization_id,
        ScrapingFacilityCandidateDecision.execution_id == execution_id,
        ScrapingFacilityCandidateDecision.facility_candidate_id == candidate_id,
    ))
    if current is None:
        return 0
    others = list((await db.execute(select(ScrapingFacilityCandidateDecision).where(
        ScrapingFacilityCandidateDecision.organization_id == organization_id,
        ScrapingFacilityCandidateDecision.execution_id == execution_id,
        ScrapingFacilityCandidateDecision.facility_candidate_id != candidate_id,
    ))).scalars())
    created = 0
    for other in others:
        # Exact identities have canonical_candidate_id linkage and need no review row.
        if current.identity_fingerprint == other.identity_fingerprint:
            continue
        score, reasons = probable_duplicate_score(current.normalized_payload, other.normalized_payload)
        if score < .55:
            continue
        left, right = sorted((candidate_id, other.facility_candidate_id))
        existing = await db.scalar(select(ScrapingFacilityCandidateDuplicate.id).where(
            ScrapingFacilityCandidateDuplicate.organization_id == organization_id,
            ScrapingFacilityCandidateDuplicate.execution_id == execution_id,
            ScrapingFacilityCandidateDuplicate.left_candidate_id == left,
            ScrapingFacilityCandidateDuplicate.right_candidate_id == right,
        ))
        if existing:
            continue
        db.add(ScrapingFacilityCandidateDuplicate(
            organization_id=organization_id, execution_id=execution_id,
            left_candidate_id=left, right_candidate_id=right,
            relationship="probable_duplicate", score=score,
            reasons_json=reasons, algorithm_version=ALGORITHM_VERSION,
        ))
        try:
            await db.flush()
            created += 1
        except IntegrityError:
            await db.rollback()
    return created
