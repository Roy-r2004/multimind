from __future__ import annotations

import hashlib
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_auth_context
from app.db.models import (
    FacilityCandidateEvidenceVerificationStatus,
    FacilityCandidatePublicationStatus,
    FacilityCandidateStagingStatus,
    FacilityExtractionAttemptStatus,
    RehabilitationFacility,
    RehabilitationFacilityAlias,
    RehabilitationFacilityAttribute,
    RehabilitationFacilityContact,
    RehabilitationFacilityLicense,
    RehabilitationFacilityLocation,
    RehabilitationFacilityOperatingHours,
    RehabilitationFacilitySourceLink,
    RehabilitationFieldEvidence,
    RehabilitationPossibleDuplicate,
    RehabilitationSource,
    RehabilitationUnresolvedField,
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingFacilityCandidate,
    ScrapingFacilityCandidateEvidence,
    ScrapingFacilityCandidatePublication,
    ScrapingFacilityExtractionAttempt,
    ScrapingMission,
    ScrapingRun,
    ScrapingRunStatus,
    ScrapingSourceCandidate,
    ScrapingSourceDiscoveryQuery,
    ScrapingSourceDocument,
    ScrapingSourceDocumentChunk,
    ScrapingSourceDocumentText,
    ScrapingSourceRetrievalAttempt,
    SourceCandidateStatus,
    SourceDiscoveryQueryStatus,
    SourceDocumentTextPreparationStatus,
    SourceRetrievalAttemptStatus,
)
from app.db.session import get_db
from app.main import create_app
from app.services.scraping.facility_candidate_publication_service import (
    FacilityCandidatePublicationContext,
    FacilityCandidatePublicationService,
    facility_candidate_publication_service,
)
from conftest import create_model_set, create_other_auth, valid_blueprint


async def create_execution(
    db: AsyncSession, auth: Any, *, country_code: str = "FR"
) -> ScrapingExecution:
    model_set = await create_model_set(
        db,
        auth,
        slug=f"publish-{auth.org_id[:8]}-{country_code.lower()}",
    )
    country_name = "France" if country_code == "FR" else "Germany"
    mission = ScrapingMission(
        org_id=auth.org_id,
        created_by=auth.user.id,
        model_set_id=model_set.slug,
        title="Publication mission",
        original_prompt="Find real facilities",
        country_code=country_code,
        country_name=country_name,
    )
    db.add(mission)
    await db.flush()
    blueprint = ScrapingBlueprint(
        mission_id=mission.id,
        version=1,
        status=ScrapingBlueprintStatus.APPROVED,
        blueprint_json=valid_blueprint(),
        model_set_id=model_set.slug,
        judge_model_id="gpt-4.1",
    )
    db.add(blueprint)
    await db.flush()
    mission.active_blueprint_id = blueprint.id
    run = ScrapingRun(
        organization_id=auth.org_id,
        mission_id=mission.id,
        blueprint_id=blueprint.id,
        model_set_id=model_set.slug,
        status=ScrapingRunStatus.PLANNED,
    )
    db.add(run)
    await db.flush()
    execution = ScrapingExecution(
        organization_id=auth.org_id,
        mission_id=mission.id,
        blueprint_id=blueprint.id,
        team_plan_id=run.id,
        execution_type="initial_full_country",
        mode="real",
        status=ScrapingExecutionStatus.COMPLETED,
        country_code=country_code,
        country_name=country_name,
    )
    db.add(execution)
    await db.flush()
    return execution


async def create_staged_candidate(
    db: AsyncSession,
    auth: Any,
    execution: ScrapingExecution,
    *,
    name: str = "  Centre   Alpha  ",
    body: str | None = None,
    attempt_status: FacilityExtractionAttemptStatus = FacilityExtractionAttemptStatus.SUCCEEDED,
    include_name_evidence: bool = True,
    extra_evidence: list[tuple[str, str, str, str]] | None = None,
) -> ScrapingFacilityCandidate:
    source_body = body or (
        "Centre Alpha is an addiction treatment facility. "
        "Address: 10 Rue Exemple, Paris. "
        "Phone: +33 1 22 33 44 55. "
        "Email: Contact@Centre-Alpha.FR. "
        "Website: HTTPS://Centre-Alpha.FR/path#team."
    )
    query = ScrapingSourceDiscoveryQuery(
        organization_id=auth.org_id,
        execution_id=execution.id,
        country_code=execution.country_code,
        country_name=execution.country_name,
        region_name="Ile-de-France",
        language_code="fr",
        language_name="French",
        source_category="official registry",
        query_text="rehab registry",
        provider="serper",
        status=SourceDiscoveryQueryStatus.SUCCEEDED,
        requested_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    db.add(query)
    await db.flush()
    source_candidate = ScrapingSourceCandidate(
        organization_id=auth.org_id,
        execution_id=execution.id,
        discovery_query_id=query.id,
        provider="serper",
        rank=1,
        url=f"https://example.test/{name.strip().replace(' ', '-').lower()}",
        canonical_url=f"https://example.test/{name.strip().replace(' ', '-').lower()}",
        domain="example.test",
        title="Candidate",
        snippet="Snippet",
        country_code=execution.country_code,
        country_name=execution.country_name,
        region_name="Ile-de-France",
        language_code="fr",
        language_name="French",
        source_category="official registry",
        initial_relevance_score=1,
        initial_trust_tier="high",
        status=SourceCandidateStatus.DISCOVERED,
        discovered_at=datetime.now(UTC),
    )
    db.add(source_candidate)
    await db.flush()
    retrieval = ScrapingSourceRetrievalAttempt(
        organization_id=auth.org_id,
        execution_id=execution.id,
        source_candidate_id=source_candidate.id,
        status=SourceRetrievalAttemptStatus.SUCCEEDED,
        requested_url=source_candidate.canonical_url,
        final_url=source_candidate.canonical_url,
        redirect_count=0,
        http_status=200,
        content_type="text/plain",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        idempotency_key=f"retrieval-{source_candidate.id}",
    )
    db.add(retrieval)
    await db.flush()
    document = ScrapingSourceDocument(
        organization_id=auth.org_id,
        execution_id=execution.id,
        source_candidate_id=source_candidate.id,
        retrieval_attempt_id=retrieval.id,
        final_url=source_candidate.canonical_url,
        content_type="text/plain",
        charset="utf-8",
        content_sha256=hashlib.sha256(source_body.encode()).hexdigest(),
        content_text=source_body,
        extracted_text=None,
        byte_size=len(source_body.encode()),
        retrieval_timestamp=datetime.now(UTC),
    )
    db.add(document)
    await db.flush()
    prepared = ScrapingSourceDocumentText(
        organization_id=auth.org_id,
        execution_id=execution.id,
        source_document_id=document.id,
        source_candidate_id=source_candidate.id,
        parser_version="test",
        source_content_hash=document.content_sha256,
        prepared_text_hash=document.content_sha256,
        detected_language="fr",
        title="Prepared title",
        prepared_text=source_body,
        character_count=len(source_body),
        original_character_count=len(source_body),
        truncated=False,
        preparation_status=SourceDocumentTextPreparationStatus.PREPARED,
    )
    db.add(prepared)
    await db.flush()
    chunk = ScrapingSourceDocumentChunk(
        organization_id=auth.org_id,
        execution_id=execution.id,
        source_document_id=document.id,
        prepared_text_id=prepared.id,
        chunk_index=0,
        character_start=0,
        character_end=len(source_body),
        chunk_text=source_body,
        chunk_hash=document.content_sha256,
    )
    db.add(chunk)
    await db.flush()
    attempt = ScrapingFacilityExtractionAttempt(
        organization_id=auth.org_id,
        execution_id=execution.id,
        source_document_id=document.id,
        prepared_text_id=prepared.id,
        chunk_id=chunk.id,
        provider="fake",
        model="fake/model",
        prompt_version="test",
        status=attempt_status,
        attempt_number=1,
        idempotency_key=f"extract-{chunk.id}",
        requested_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        output_candidate_count=1,
    )
    db.add(attempt)
    await db.flush()
    candidate = ScrapingFacilityCandidate(
        organization_id=auth.org_id,
        execution_id=execution.id,
        source_document_id=document.id,
        prepared_text_id=prepared.id,
        chunk_id=chunk.id,
        extraction_attempt_id=attempt.id,
        raw_name=name,
        raw_payload={"name": name},
        staging_status=FacilityCandidateStagingStatus.EXTRACTED,
        candidate_fingerprint=hashlib.sha256(f"{document.id}:{name}".encode()).hexdigest(),
    )
    db.add(candidate)
    await db.flush()
    rows = []
    if include_name_evidence:
        evidence_name = " ".join(name.split())
        rows.append(("name", evidence_name, evidence_name, "verified"))
    rows.extend(extra_evidence or [])
    for field_name, raw_value, quote, status in rows:
        start = source_body.find(quote)
        if start < 0:
            start = 0
        verification_status = (
            FacilityCandidateEvidenceVerificationStatus.VERIFIED
            if status == "verified"
            else FacilityCandidateEvidenceVerificationStatus.REJECTED_QUOTE_NOT_FOUND
        )
        db.add(
            ScrapingFacilityCandidateEvidence(
                organization_id=auth.org_id,
                execution_id=execution.id,
                facility_candidate_id=candidate.id,
                source_document_id=document.id,
                prepared_text_id=prepared.id,
                chunk_id=chunk.id,
                field_name=field_name,
                raw_value=raw_value,
                evidence_quote=quote,
                quote_start=start,
                quote_end=start + len(quote),
                evidence_hash=hashlib.sha256(
                    f"{field_name}:{raw_value}:{quote}".encode()
                ).hexdigest(),
                verification_status=verification_status,
            )
        )
    await db.flush()
    return candidate


async def publish(
    db: AsyncSession, auth: Any, execution: ScrapingExecution, candidate: ScrapingFacilityCandidate
):
    return await facility_candidate_publication_service.publish_one_candidate(
        db,
        FacilityCandidatePublicationContext(
            organization_id=auth.org_id,
            execution_id=execution.id,
            facility_candidate_id=candidate.id,
        ),
    )


def test_migration_016_creates_publication_table_and_downgrades_cleanly():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "016_facility_candidate_publications.py"
    )
    spec = importlib.util.spec_from_file_location("migration_016", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.revision == "016"
    assert module.down_revision == "015"

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE organizations (id VARCHAR(36) PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE scraping_executions (id VARCHAR(36) PRIMARY KEY)")
        conn.exec_driver_sql(
            "CREATE TABLE scraping_facility_candidates (id VARCHAR(36) PRIMARY KEY)"
        )
        conn.exec_driver_sql("CREATE TABLE rehabilitation_facilities (id VARCHAR(36) PRIMARY KEY)")
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        ctx = MigrationContext.configure(conn)
        ops = Operations(ctx)
        original_op = module.op
        module.op = ops
        try:
            module.upgrade()
            inspector = inspect(conn)
            assert "scraping_facility_candidate_publications" in inspector.get_table_names()
            unique_names = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints(
                    "scraping_facility_candidate_publications"
                )
            }
            assert "uq_facility_candidate_publication_candidate" in unique_names
            module.downgrade()
            assert "scraping_facility_candidate_publications" not in inspect(conn).get_table_names()
        finally:
            module.op = original_op


@pytest.mark.asyncio
async def test_successful_publication_creates_final_facility_source_and_name_evidence(
    db: AsyncSession, auth
):
    execution = await create_execution(db, auth)
    candidate = await create_staged_candidate(
        db,
        auth,
        execution,
        extra_evidence=[("aliases", "Rejected Alias", "Rejected Alias", "rejected")],
    )

    summary = await publish(db, auth, execution, candidate)

    assert summary.status == "published"
    facility = await db.get(RehabilitationFacility, summary.final_facility_id)
    assert facility is not None
    assert facility.canonical_name == "Centre Alpha"
    assert facility.is_mock is False
    assert facility.country_code == "FR"
    assert await db.scalar(select(func.count()).select_from(RehabilitationSource)) == 1
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacilitySourceLink)) == 1
    evidence = await db.scalar(select(RehabilitationFieldEvidence))
    assert evidence is not None
    assert evidence.field_path == "canonical_name"
    assert evidence.evidence_text == "Centre Alpha"
    assert evidence.is_mock is False
    all_evidence_text = (
        await db.execute(select(RehabilitationFieldEvidence.evidence_text))
    ).scalars().all()
    assert "Rejected Alias" not in all_evidence_text
    assert await db.scalar(select(func.count()).select_from(RehabilitationPossibleDuplicate)) == 0
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacilityAlias)) == 0
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacilityAttribute)) == 0
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacilityLicense)) == 0
    assert (
        await db.scalar(select(func.count()).select_from(RehabilitationFacilityOperatingHours))
        == 0
    )


@pytest.mark.asyncio
async def test_optional_fields_create_valid_children_and_invalid_contacts_become_unresolved(
    db: AsyncSession, auth
):
    execution = await create_execution(db, auth)
    candidate = await create_staged_candidate(
        db,
        auth,
        execution,
        extra_evidence=[
            ("addresses", "10 Rue Exemple, Paris", "10 Rue Exemple, Paris", "verified"),
            ("phones", "+33 1 22 33 44 55", "+33 1 22 33 44 55", "verified"),
            ("emails", "Contact@Centre-Alpha.FR", "Contact@Centre-Alpha.FR", "verified"),
            (
                "websites",
                "HTTPS://Centre-Alpha.FR/path#team",
                "HTTPS://Centre-Alpha.FR/path#team",
                "verified",
            ),
            ("emails", "not an email", "not an email", "verified"),
            ("phones", "call us", "call us", "verified"),
            ("websites", "ftp://example.test", "ftp://example.test", "verified"),
            ("services", "Detoxification", "Detoxification", "verified"),
            ("services", "Counseling", "Counseling", "verified"),
        ],
    )

    summary = await publish(db, auth, execution, candidate)

    assert summary.status == "published"
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacilityLocation)) == 1
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacilityContact)) == 3
    assert await db.scalar(select(func.count()).select_from(RehabilitationUnresolvedField)) == 3
    attributes = (
        await db.execute(select(RehabilitationFacilityAttribute))
    ).scalars().all()
    assert len(attributes) == 2
    assert {attr.attribute_group for attr in attributes} == {"treatment_service"}
    assert {attr.display_name for attr in attributes} == {"Detoxification", "Counseling"}
    contacts = (
        await db.execute(
            select(RehabilitationFacilityContact).order_by(
                RehabilitationFacilityContact.contact_type
            )
        )
    ).scalars().all()
    assert {contact.contact_type for contact in contacts} == {"email", "phone", "website"}
    assert any(contact.value == "contact@centre-alpha.fr" for contact in contacts)
    assert any(contact.normalized_value == "+33122334455" for contact in contacts)
    facility = await db.get(RehabilitationFacility, summary.final_facility_id)
    assert facility.primary_website == "https://centre-alpha.fr/path"

    from app.services.scraping.execution_service import execution_service

    listed = await execution_service.list_facilities(db, auth, execution.id)
    assert len(listed) == 1
    assert listed[0].location_count == 1
    assert listed[0].contact_count == 3
    assert listed[0].treatment_service_count == 2
    detail = await execution_service.get_facility(db, auth, execution.id, listed[0].id)
    assert len(detail.locations) == 1
    assert len(detail.contacts) == 3
    assert len([a for a in detail.attributes if a.attribute_group == "treatment_service"]) == 2
    assert detail.sources
    assert any("treatment_service" in (row.field_path or "") for row in detail.evidence)


@pytest.mark.asyncio
async def test_missing_verified_name_is_skipped_without_final_facility(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_staged_candidate(db, auth, execution, include_name_evidence=False)

    summary = await publish(db, auth, execution, candidate)

    assert summary.status == "skipped"
    assert summary.reason_code == "missing_verified_name"
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacility)) == 0


@pytest.mark.asyncio
async def test_extraction_not_succeeded_is_not_published(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_staged_candidate(
        db,
        auth,
        execution,
        attempt_status=FacilityExtractionAttemptStatus.FAILED,
    )

    summary = await publish(db, auth, execution, candidate)

    assert summary.status == "skipped"
    assert summary.reason_code == "extraction_not_succeeded"
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacility)) == 0


@pytest.mark.asyncio
async def test_country_fallback_and_conflict(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    fallback = await create_staged_candidate(db, auth, execution)
    fallback_summary = await publish(db, auth, execution, fallback)
    publication = await db.get(
        ScrapingFacilityCandidatePublication,
        fallback_summary.publication_id,
    )
    assert fallback_summary.country_code == "FR"
    assert publication.metadata_json["country_source"] == "execution_scope"

    conflict = await create_staged_candidate(
        db,
        auth,
        execution,
        name="Centre Beta",
        body="Centre Beta Germany",
        extra_evidence=[("country", "DE", "Germany", "verified")],
    )
    conflict_summary = await publish(db, auth, execution, conflict)
    assert conflict_summary.status == "skipped"
    assert conflict_summary.reason_code == "country_scope_conflict"


@pytest.mark.asyncio
async def test_publication_is_org_isolated_for_service_and_api(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_staged_candidate(db, auth, execution)
    other = await create_other_auth(db)

    with pytest.raises(Exception):
        await facility_candidate_publication_service.publish_one_candidate(
            db,
            FacilityCandidatePublicationContext(
                organization_id=other.org_id,
                execution_id=execution.id,
                facility_candidate_id=candidate.id,
            ),
        )

    await publish(db, auth, execution, candidate)
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_auth_context] = lambda: other
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/scraping/executions/{execution.id}/facility-candidate-publications"
        )
    app.dependency_overrides.clear()
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_publication_retry_is_idempotent(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_staged_candidate(db, auth, execution)

    first = await publish(db, auth, execution, candidate)
    models = [
        RehabilitationFacility,
        RehabilitationSource,
        RehabilitationFacilitySourceLink,
        RehabilitationFieldEvidence,
        ScrapingFacilityCandidatePublication,
    ]
    counts = {
        model.__name__: await db.scalar(select(func.count()).select_from(model))
        for model in models
    }
    second = await publish(db, auth, execution, candidate)

    assert second.reused_existing_publication is True
    assert second.final_facility_id == first.final_facility_id
    assert counts == {
        model.__name__: await db.scalar(select(func.count()).select_from(model))
        for model in models
    }


@pytest.mark.asyncio
async def test_concurrent_publication_creates_at_most_one_final_facility(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_staged_candidate(db, auth, execution)
    await db.commit()
    

    db.add(
        ScrapingFacilityCandidatePublication(
            organization_id=auth.org_id,
            execution_id=execution.id,
            facility_candidate_id=candidate.id,
            normalization_version="facility-publication-v1",
            status=FacilityCandidatePublicationStatus.PENDING,
        )
    )
    await db.flush()
    db.add(
        ScrapingFacilityCandidatePublication(
            organization_id=auth.org_id,
            execution_id=execution.id,
            facility_candidate_id=candidate.id,
            normalization_version="facility-publication-v1",
            status=FacilityCandidatePublicationStatus.PENDING,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()
    await db.refresh(execution)
    await db.refresh(candidate)
    summary = await publish(db, auth, execution, candidate)
    assert summary.status == "published"
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacility)) == 1


@pytest.mark.asyncio
async def test_transaction_rollback_leaves_no_partial_final_rows(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_staged_candidate(db, auth, execution)
    await db.commit()

    class FailingPublicationService(FacilityCandidatePublicationService):
        async def _after_facility_created(
            self, db: AsyncSession, facility: RehabilitationFacility
        ) -> None:
            raise RuntimeError("simulated child write failure")

    summary = await FailingPublicationService().publish_one_candidate(
        db,
        FacilityCandidatePublicationContext(
            organization_id=auth.org_id,
            execution_id=execution.id,
            facility_candidate_id=candidate.id,
        ),
    )

    assert summary.status == "failed"
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacility)) == 0
    assert await db.scalar(select(func.count()).select_from(RehabilitationSource)) == 0


@pytest.mark.asyncio
async def test_exact_evidence_metadata_source_reuse_and_possible_duplicate_flagging(
    db: AsyncSession, auth
):
    execution = await create_execution(db, auth)
    first = await create_staged_candidate(db, auth, execution, name="Centre Alpha")
    second = await create_staged_candidate(db, auth, execution, name="Centre Alpha Plus")
    second_doc = await db.get(ScrapingSourceDocument, second.source_document_id)
    first_doc = await db.get(ScrapingSourceDocument, first.source_document_id)
    second_doc.final_url = first_doc.final_url
    await db.flush()

    first_summary = await publish(db, auth, execution, first)
    second_summary = await publish(db, auth, execution, second)

    assert first_summary.final_facility_id != second_summary.final_facility_id
    assert await db.scalar(select(func.count()).select_from(RehabilitationSource)) == 1
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacility)) == 2
    assert await db.scalar(select(func.count()).select_from(RehabilitationPossibleDuplicate)) == 1
    publication = await db.get(ScrapingFacilityCandidatePublication, first_summary.publication_id)
    mapping = publication.metadata_json["evidence_mappings"][0]
    staging_evidence = await db.get(
        ScrapingFacilityCandidateEvidence,
        mapping["staging_evidence_id"],
    )
    assert mapping["quote_start"] == staging_evidence.quote_start
    assert mapping["quote_end"] == staging_evidence.quote_end
    assert mapping["evidence_hash_prefix"] == staging_evidence.evidence_hash[:12]
    assert publication.metadata_json["publication_confidence"] is not None


@pytest.mark.asyncio
async def test_exact_name_candidates_merge_into_one_facility(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    first = await create_staged_candidate(db, auth, execution, name="Centre Alpha")
    second = await create_staged_candidate(db, auth, execution, name="  Centre   Alpha  ")
    await db.flush()

    first_summary = await publish(db, auth, execution, first)
    second_summary = await publish(db, auth, execution, second)

    assert first_summary.final_facility_id == second_summary.final_facility_id
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacility)) == 1
    second_publication = await db.get(
        ScrapingFacilityCandidatePublication, second_summary.publication_id
    )
    assert second_publication.metadata_json["merged_into_existing"] is True


@pytest.mark.asyncio
async def test_worker_auto_publishes_staged_candidates(db: AsyncSession, auth, monkeypatch):
    from app.core.config import get_settings
    from app.services.scraping.execution_orchestrator import SourceDiscoveryExecutionOrchestrator

    settings = get_settings()
    monkeypatch.setattr(settings, "facility_extraction_enabled", True)
    monkeypatch.setattr(settings, "facility_publication_enabled", True)
    monkeypatch.setattr(settings, "facility_publication_max_candidates_per_execution", 10)

    execution = await create_execution(db, auth)
    candidate = await create_staged_candidate(db, auth, execution, name="Centre Auto Publish")
    await db.commit()

    orchestrator = SourceDiscoveryExecutionOrchestrator(db)
    await orchestrator._run_facility_publication_phase(execution)
    await orchestrator._refresh_metrics(execution)
    await db.commit()

    assert await db.scalar(select(func.count()).select_from(RehabilitationFacility)) == 1
    assert await db.scalar(
        select(func.count()).select_from(ScrapingFacilityCandidatePublication)
    ) == 1
    assert execution.records_extracted >= 1
    assert execution.records_verified >= 1
    assert candidate.id  # keep fixture referenced


def test_publication_is_connected_to_worker_but_not_excel_export():
    backend_root = Path(__file__).resolve().parents[1]

    orchestrator_text = (
        backend_root / "app/services/scraping/execution_orchestrator.py"
    ).read_text(encoding="utf-8")

    export_text = (
        backend_root / "app/services/scraping/execution_export_service.py"
    ).read_text(encoding="utf-8")

    assert "facility_candidate_publication_service" in orchestrator_text
    assert "_run_facility_publication_phase" in orchestrator_text

    # Publishing must not automatically enable or modify Excel export.
    assert "FacilityCandidatePublicationService" not in export_text
    assert "facility_candidate_publication_service" not in export_text