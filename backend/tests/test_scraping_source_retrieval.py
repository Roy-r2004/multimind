from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import get_settings
from app.core.dependencies import get_auth_context
from app.db.models import (
    RehabilitationFieldEvidence,
    RehabilitationFacility,
    RehabilitationSource,
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingMission,
    ScrapingRun,
    ScrapingRunStatus,
    ScrapingSourceCandidate,
    ScrapingSourceDiscoveryQuery,
    ScrapingSourceDocument,
    ScrapingSourceRetrievalAttempt,
    SourceCandidateStatus,
    SourceDiscoveryQueryStatus,
)
from app.db.session import get_db
from app.main import create_app
from app.services.scraping.source_retrieval_service import (
    SourceRetrievalContext,
    SourceRetrievalError,
    SourceRetrievalService,
)
from conftest import create_model_set, create_other_auth, valid_blueprint


async def create_execution(db: AsyncSession, auth: Any) -> ScrapingExecution:
    model_set = await create_model_set(db, auth, slug=f"model-{auth.org_id[:8]}")
    mission = ScrapingMission(
        org_id=auth.org_id,
        created_by=auth.user.id,
        model_set_id=model_set.slug,
        title="Retrieval mission",
        original_prompt="Find real sources",
        country_code="FR",
        country_name="France",
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
        status=ScrapingExecutionStatus.QUEUED,
        country_code="FR",
        country_name="France",
    )
    db.add(execution)
    await db.flush()
    return execution


async def create_candidate(
    db: AsyncSession,
    auth: Any,
    execution: ScrapingExecution,
    *,
    url: str = "https://example.test/page",
) -> ScrapingSourceCandidate:
    query = ScrapingSourceDiscoveryQuery(
        organization_id=auth.org_id,
        execution_id=execution.id,
        country_code="FR",
        country_name="France",
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
    candidate = ScrapingSourceCandidate(
        organization_id=auth.org_id,
        execution_id=execution.id,
        discovery_query_id=query.id,
        provider="serper",
        rank=1,
        url=url,
        canonical_url=url,
        domain=httpx.URL(url).host,
        title="Candidate",
        snippet="Snippet",
        country_code="FR",
        country_name="France",
        region_name="Ile-de-France",
        language_code="fr",
        language_name="French",
        source_category="official registry",
        initial_relevance_score=1,
        initial_trust_tier="high",
        status=SourceCandidateStatus.DISCOVERED,
        discovered_at=datetime.now(UTC),
    )
    db.add(candidate)
    await db.flush()
    return candidate


def resolver(addresses: dict[str, list[str]]):
    async def _resolve(hostname: str, _port: int) -> list[str]:
        if hostname not in addresses:
            raise OSError("not found")
        return addresses[hostname]

    return _resolve


def service(handler, addresses: dict[str, list[str]] | None = None) -> SourceRetrievalService:
    transport = httpx.MockTransport(handler)
    return SourceRetrievalService(
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs),
        resolver=resolver(addresses or {"example.test": ["93.184.216.34"]}),
    )


def context(auth: Any, execution: ScrapingExecution, candidate: ScrapingSourceCandidate, key: str) -> SourceRetrievalContext:
    return SourceRetrievalContext(
        organization_id=auth.org_id,
        execution_id=execution.id,
        source_candidate_id=candidate.id,
        idempotency_key=key,
    )


def test_migration_upgrade_and_downgrade_create_and_remove_retrieval_tables():
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "014_source_retrieval.py"
    spec = importlib.util.spec_from_file_location("migration_014_source_retrieval", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        ops = Operations(ctx)
        original = migration.op
        migration.op = ops
        try:
            migration.upgrade()
            tables = set(inspect(conn).get_table_names())
            assert "scraping_source_retrieval_attempts" in tables
            assert "scraping_source_documents" in tables
            migration.downgrade()
            tables = set(inspect(conn).get_table_names())
            assert "scraping_source_retrieval_attempts" not in tables
            assert "scraping_source_documents" not in tables
        finally:
            migration.op = original


@pytest.mark.asyncio
async def test_supported_textual_content_is_persisted_idempotently(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        assert request.method == "GET"
        assert "authorization" not in request.headers
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"hello")

    retrieval = service(handler)
    first = await retrieval.retrieve(db, context(auth, execution, candidate, "idem-1"))
    second = await retrieval.retrieve(db, context(auth, execution, candidate, "idem-1"))
    assert first.status == "succeeded"
    assert second.attempt_id == first.attempt_id
    assert first.content_sha256 == hashlib_sha256(b"hello")
    assert await db.scalar(select(func.count()).select_from(ScrapingSourceDocument)) == 1


@pytest.mark.asyncio
async def test_changed_content_creates_document_history(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)
    bodies = [b"first", b"second"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "application/json"}, content=bodies.pop(0))

    retrieval = service(handler)
    one = await retrieval.retrieve(db, context(auth, execution, candidate, "idem-a"))
    two = await retrieval.retrieve(db, context(auth, execution, candidate, "idem-b"))
    assert one.status == two.status == "succeeded"
    assert one.document_id != two.document_id
    assert await db.scalar(select(func.count()).select_from(ScrapingSourceDocument)) == 2


@pytest.mark.asyncio
async def test_identical_content_deduplicates_across_idempotency_keys(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/xml"}, content=b"<a/>")

    retrieval = service(handler)
    one = await retrieval.retrieve(db, context(auth, execution, candidate, "idem-a"))
    two = await retrieval.retrieve(db, context(auth, execution, candidate, "idem-b"))
    assert one.document_id == two.document_id
    assert await db.scalar(select(func.count()).select_from(ScrapingSourceRetrievalAttempt)) == 2
    assert await db.scalar(select(func.count()).select_from(ScrapingSourceDocument)) == 1


@pytest.mark.asyncio
async def test_non_http_url_is_rejected_by_retrieval_validator():
    retrieval = service(lambda _: httpx.Response(200))

    with pytest.raises(SourceRetrievalError) as exc_info:
        await retrieval._validate_url("ftp://example.test/file")

    assert exc_info.value.status.value == "unsafe_url"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "status"),
    [
        ("https://user:pass@example.test/file", "unsafe_url"),
        ("https://localhost/file", "unsafe_url"),
        ("http://10.0.0.1/file", "private_or_reserved_address"),
        ("http://[::1]/file", "private_or_reserved_address"),
        ("http://169.254.169.254/latest", "private_or_reserved_address"),
        ("https://example.test:8443/file", "unsafe_url"),
    ],
)
async def test_unsafe_urls_are_rejected(db: AsyncSession, auth, url: str, status: str):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution, url=url)
    summary = await service(lambda _: httpx.Response(200)).retrieve(
        db, context(auth, execution, candidate, f"key:{url}")
    )
    assert summary.status == status
    assert summary.document_id is None


@pytest.mark.asyncio
async def test_dns_answers_must_all_be_safe(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)
    mixed = service(lambda _: httpx.Response(200), {"example.test": ["93.184.216.34", "10.0.0.2"]})
    assert (await mixed.retrieve(db, context(auth, execution, candidate, "mixed"))).status == "private_or_reserved_address"
    safe = service(lambda request: httpx.Response(404 if request.url.path == "/robots.txt" else 200, headers={"content-type": "text/plain"}, content=b"ok"))
    assert (await safe.retrieve(db, context(auth, execution, candidate, "safe"))).status == "succeeded"


@pytest.mark.asyncio
async def test_dns_resolution_failure_is_classified(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)
    summary = await service(lambda _: httpx.Response(200), {"other.test": ["93.184.216.34"]}).retrieve(
        db, context(auth, execution, candidate, "dns-failed")
    )
    assert summary.status == "dns_resolution_failed"
    assert summary.document_id is None


@pytest.mark.asyncio
async def test_redirects_are_manual_revalidated_and_limited(db: AsyncSession, auth, monkeypatch):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)
    monkeypatch.setattr(get_settings(), "source_retrieval_max_redirects", 1)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/page":
            return httpx.Response(302, headers={"location": "https://next.test/ok"})
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>ok</html>")

    retrieval = service(handler, {"example.test": ["93.184.216.34"], "next.test": ["93.184.216.35"]})
    summary = await retrieval.retrieve(db, context(auth, execution, candidate, "redir"))
    assert summary.status == "succeeded", summary.model_dump()
    assert summary.redirect_count == 1
    assert summary.final_url == "https://next.test/ok"
    assert summary.document_id is not None
    assert any("next.test/ok" in url for url in requests)

    private_redirect = service(
        lambda request: httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(302, headers={"location": "http://10.0.0.1/"}),
        {"example.test": ["93.184.216.34"]},
    )
    private_summary = await private_redirect.retrieve(
        db, context(auth, execution, candidate, "redir-private")
    )
    assert private_summary.status == "unsafe_redirect", private_summary.model_dump()
    assert private_summary.document_id is None


@pytest.mark.asyncio
async def test_redirect_limit_is_enforced(db: AsyncSession, auth, monkeypatch):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)
    monkeypatch.setattr(get_settings(), "source_retrieval_max_redirects", 0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "https://example.test/again"})

    summary = await service(handler).retrieve(db, context(auth, execution, candidate, "limit"))
    assert summary.status == "redirect_limit_exceeded", summary.model_dump()
    assert summary.document_id is None


@pytest.mark.asyncio
async def test_second_redirect_is_rejected_when_limit_is_one(db: AsyncSession, auth, monkeypatch):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)
    monkeypatch.setattr(get_settings(), "source_retrieval_max_redirects", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "example.test":
            return httpx.Response(302, headers={"location": "https://next.test/again"})
        return httpx.Response(302, headers={"location": "https://final.test/ok"})

    retrieval = service(
        handler,
        {
            "example.test": ["93.184.216.34"],
            "next.test": ["93.184.216.35"],
            "final.test": ["93.184.216.36"],
        },
    )
    summary = await retrieval.retrieve(db, context(auth, execution, candidate, "redir-second"))
    assert summary.status == "redirect_limit_exceeded", summary.model_dump()
    assert summary.redirect_count == 1
    assert summary.document_id is None


@pytest.mark.asyncio
async def test_redirect_target_scheme_is_revalidated(db: AsyncSession, auth, monkeypatch):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)
    monkeypatch.setattr(get_settings(), "source_retrieval_max_redirects", 1)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": "ftp://example.test/file"})

    summary = await service(handler).retrieve(db, context(auth, execution, candidate, "redir-ftp"))
    assert summary.status == "unsafe_redirect", summary.model_dump()
    assert summary.document_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("robots_response", "status", "robots_status"),
    [
        (httpx.Response(200, content=b"User-agent: *\nDisallow: /\n"), "blocked_by_robots", "disallowed"),
        (httpx.Response(401), "blocked_by_robots", "blocked"),
        (httpx.Response(403), "blocked_by_robots", "blocked"),
        (httpx.Response(503), "blocked_by_robots", "unavailable"),
    ],
)
async def test_robots_fail_closed(db: AsyncSession, auth, robots_response: httpx.Response, status: str, robots_status: str):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return robots_response
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"blocked")

    summary = await service(handler).retrieve(db, context(auth, execution, candidate, f"robots:{robots_status}"))
    assert summary.status == status
    assert summary.robots_status == robots_status
    assert summary.document_id is None


@pytest.mark.asyncio
async def test_robots_404_permits_retrieval(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)
    retrieval = service(
        lambda request: httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")
    )
    assert (await retrieval.retrieve(db, context(auth, execution, candidate, "robots-404"))).status == "succeeded"


@pytest.mark.asyncio
async def test_robots_timeout_fails_closed(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            raise httpx.TimeoutException("slow")
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")

    summary = await service(handler).retrieve(db, context(auth, execution, candidate, "robots-timeout"))
    assert summary.status == "blocked_by_robots"
    assert summary.robots_status == "unavailable"
    assert summary.document_id is None


@pytest.mark.asyncio
async def test_tls_verification_is_not_disabled(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)
    kwargs_seen: list[dict[str, Any]] = []
    transport = httpx.MockTransport(
        lambda request: httpx.Response(404)
        if request.url.path == "/robots.txt"
        else httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")
    )

    def client_factory(**kwargs):
        kwargs_seen.append(kwargs)
        return httpx.AsyncClient(transport=transport, **kwargs)

    retrieval = SourceRetrievalService(client_factory=client_factory, resolver=resolver({"example.test": ["93.184.216.34"]}))
    await retrieval.retrieve(db, context(auth, execution, candidate, "tls"))
    assert kwargs_seen
    assert all(kwargs["verify"] is True for kwargs in kwargs_seen)


@pytest.mark.asyncio
async def test_size_cap_uses_streamed_decompressed_bytes(db: AsyncSession, auth, monkeypatch):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)
    monkeypatch.setattr(get_settings(), "source_retrieval_max_bytes", 4)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": "text/plain", "content-length": "1"}, content=b"too large")

    summary = await service(handler).retrieve(db, context(auth, execution, candidate, "large"))
    assert summary.status == "response_too_large"
    assert summary.bytes_received and summary.bytes_received > 4
    assert await db.scalar(select(func.count()).select_from(ScrapingSourceDocument)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["application/pdf", "image/png", "application/zip"])
async def test_unsupported_content_type_does_not_create_document(db: AsyncSession, auth, content_type: str):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)
    retrieval = service(
        lambda request: httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(200, headers={"content-type": content_type}, content=b"x")
    )
    summary = await retrieval.retrieve(db, context(auth, execution, candidate, content_type))
    assert summary.status == "unsupported_content_type"
    assert await db.scalar(select(func.count()).select_from(ScrapingSourceDocument)) == 0


@pytest.mark.asyncio
async def test_org_owned_candidate_required_and_list_endpoints_are_isolated(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)
    other_auth = await create_other_auth(db)
    retrieval = service(
        lambda request: httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(200, headers={"content-type": "text/plain"}, content=b"visible")
    )
    await retrieval.retrieve(db, context(auth, execution, candidate, "visible"))
    with pytest.raises(Exception, match="ScrapingSourceCandidate not found"):
        await retrieval.retrieve(
            db,
            SourceRetrievalContext(
                organization_id=other_auth.org_id,
                execution_id=execution.id,
                source_candidate_id=candidate.id,
                idempotency_key="wrong-org",
            ),
        )

    app = create_app()

    async def override_db():
        yield db

    async def override_auth():
        return auth

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_auth_context] = override_auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        attempts = (await client.get(f"/api/v1/scraping/executions/{execution.id}/retrieval-attempts")).json()
        documents = (await client.get(f"/api/v1/scraping/executions/{execution.id}/source-documents")).json()
    assert len(attempts) == 1
    assert "idempotency_key" not in attempts[0]
    assert len(documents) == 1
    assert "content_text" not in documents[0]
    assert "extracted_text" not in documents[0]


@pytest.mark.asyncio
async def test_retrieval_creates_no_facilities_sources_or_evidence_and_imports_no_mock_modules(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    candidate = await create_candidate(db, auth, execution)
    retrieval = service(
        lambda request: httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")
    )
    await retrieval.retrieve(db, context(auth, execution, candidate, "no-side-effects"))
    assert await db.scalar(select(func.count()).select_from(RehabilitationFacility)) == 0
    assert await db.scalar(select(func.count()).select_from(RehabilitationSource)) == 0
    assert await db.scalar(select(func.count()).select_from(RehabilitationFieldEvidence)) == 0

    import app.services.scraping.source_retrieval_service as module

    assert "llm" not in module.__dict__
    assert "mock_tools" not in module.__dict__
    assert "mock_facility_generator" not in module.__dict__


def test_source_retrieval_context_rejects_caller_url_substitution():
    with pytest.raises(Exception):
        SourceRetrievalContext(
            organization_id="org",
            execution_id="exe",
            source_candidate_id="candidate",
            idempotency_key="key",
            url="https://attacker.test",
        )


def test_sha256_is_deterministic():
    assert hashlib_sha256(b"same") == hashlib_sha256(b"same")
    assert hashlib_sha256(b"same") != hashlib_sha256(b"changed")


def hashlib_sha256(content: bytes) -> str:
    import hashlib

    return hashlib.sha256(content).hexdigest()
