from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.db.models import (
    
    RehabilitationFacility,
    RehabilitationFieldEvidence,
    RehabilitationSource,
    ScrapingBlueprint,
    ScrapingBlueprintStatus,
    ScrapingExecution,
    ScrapingExecutionStatus,
    ScrapingMission,
    ScrapingRun,
    ScrapingRunStatus,
    ScrapingSourceCandidate,
    SourceDiscoveryQueryStatus,
)
from app.schemas.api import SourceDiscoveryContext, SourceDiscoveryPlannedQuery
from app.services.scraping.search_providers.base import (
    SearchProviderAuthError,
    SearchProviderConfigurationError,
    SearchProviderRequest,
    SearchProviderResult,
)
from app.services.scraping.search_providers.brave import BraveSearchProvider
from app.services.scraping.source_discovery_service import SourceDiscoveryService
from app.services.scraping.url_canonicalization import UrlRejected, canonicalize_discovery_url
from tests.conftest import create_model_set, create_other_auth, valid_blueprint


@dataclass
class FakePlanner:
    queries: list[SourceDiscoveryPlannedQuery]

    async def plan_queries(self, context: SourceDiscoveryContext) -> list[SourceDiscoveryPlannedQuery]:
        return self.queries


class FakeProvider:
    name = "fake"

    def __init__(self, results: list[SearchProviderResult] | None = None, error: Exception | None = None) -> None:
        self.results = results or []
        self.error = error
        self.requests: list[SearchProviderRequest] = []

    async def search(self, request: SearchProviderRequest) -> list[SearchProviderResult]:
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.results


async def create_execution(db: AsyncSession, auth) -> ScrapingExecution:
    model_set = await create_model_set(db, auth)
    mission = ScrapingMission(
        org_id=auth.org_id,
        created_by=auth.user.id,
        model_set_id=model_set.slug,
        title="Source discovery mission",
        original_prompt="Find rehabilitation sources",
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
        mode="mock",
        status=ScrapingExecutionStatus.QUEUED,
        country_code="FR",
        country_name="France",
    )
    db.add(execution)
    await db.flush()
    return execution


def context(auth, execution: ScrapingExecution | None = None, *, provider: str = "fake") -> SourceDiscoveryContext:
    return SourceDiscoveryContext(
        organization_id=auth.org_id,
        execution_id=execution.id if execution else None,
        country_code="FR",
        country_name="France",
        region_code="idf",
        region_name="Ile-de-France",
        language_code="fr",
        language_name="French",
        source_category="official registry",
        mission_goal="Find rehabilitation facility source candidates",
        requested_fields=["name", "license", "phone"],
        blueprint_context={"source_strategy": [{"source_type": "official registry"}]},
        provider=provider,
    )


def planned_queries() -> list[SourceDiscoveryPlannedQuery]:
    return [
        SourceDiscoveryPlannedQuery(
            query="registre officiel centres rehabilitation Ile-de-France",
            language_code="fr",
            purpose="official registry discovery",
        ),
        SourceDiscoveryPlannedQuery(
            query="registre officiel centres rehabilitation Ile-de-France",
            language_code="fr",
            purpose="duplicate should be ignored by planner service tests",
        ),
        SourceDiscoveryPlannedQuery(
            query="France rehabilitation licensing registry Ile-de-France",
            language_code="en",
            purpose="English high-trust discovery",
        ),
    ]


@pytest.mark.asyncio
async def test_missing_brave_api_key_fails_closed(monkeypatch):
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        provider = BraveSearchProvider()
        with pytest.raises(SearchProviderConfigurationError):
            await provider.search(
                SearchProviderRequest(
                    query="official registry",
                    country_code="FR",
                    search_language="fr",
                    result_limit=3,
                )
            )
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_brave_uses_auth_header_and_country_language(monkeypatch):
    captured: dict[str, Any] = {}

    class Client:
        def __init__(self, timeout: float) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, *, headers, params):
            captured["url"] = url
            captured["headers"] = headers
            captured["params"] = params
            return httpx.Response(
                200,
                json={"web": {"results": [{"url": "https://example.fr/source", "title": "Title", "description": "Snippet"}]}},
            )

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "secret-key")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    try:
        results = await BraveSearchProvider().search(
            SearchProviderRequest(
                query="registre officiel",
                country_code="FR",
                search_language="fr",
                result_limit=2,
            )
        )
    finally:
        get_settings.cache_clear()

    assert captured["headers"]["X-Subscription-Token"] == "secret-key"
    assert "secret-key" not in repr(results)
    assert captured["params"]["country"] == "FR"
    assert captured["params"]["search_lang"] == "fr"
    assert captured["params"]["count"] == 2


@pytest.mark.asyncio
async def test_brave_auth_failure_is_not_retried(monkeypatch):
    calls = 0

    class Client:
        def __init__(self, timeout: float) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, *, headers, params):
            nonlocal calls
            calls += 1
            return httpx.Response(401, json={"error": "bad key"})

    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "secret-key")
    get_settings.cache_clear()
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    try:
        with pytest.raises(SearchProviderAuthError):
            await BraveSearchProvider().search(
                SearchProviderRequest(query="x", country_code="FR", search_language="fr", result_limit=1)
            )
    finally:
        get_settings.cache_clear()
    assert calls == 1


def test_canonicalize_discovery_url_rejects_unsafe_urls():
    for url in [
        "mock://source/1",
        "data:text/plain,hello",
        "file:///tmp/x",
        "javascript:alert(1)",
        "http://localhost/a",
        "http://127.0.0.1/a",
        "http://10.0.0.2/a",
        "http://169.254.169.254/latest",
        "https://facility-001.example.invalid",
        "https://example.com/path",
        "https://user:pass@real.example/path",
    ]:
        with pytest.raises(UrlRejected):
            canonicalize_discovery_url(url)


def test_canonicalize_discovery_url_normalizes_fragments_ports_and_tracking():
    canonical = canonicalize_discovery_url(
        "HTTPS://Sub.Domain.org:443/path?utm_source=x&id=42&fbclid=y#section"
    )
    assert canonical.canonical_url == "https://sub.domain.org/path?id=42"
    assert canonical.domain == "sub.domain.org"


@pytest.mark.asyncio
async def test_source_discovery_persists_real_candidates_idempotently(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    provider = FakeProvider(
        [
            SearchProviderResult(rank=1, url="https://sante.gouv.fr/annuaire?id=1&utm_source=x#top", title="Official registry", snippet="A" * 1200),
            SearchProviderResult(rank=2, url="https://sante.gouv.fr/annuaire?id=1", title="Duplicate", snippet="Duplicate"),
            SearchProviderResult(rank=3, url="mock://source/1", title="Bad", snippet="Bad"),
        ]
    )
    service = SourceDiscoveryService(
        planner=FakePlanner(planned_queries()[:1]),
        providers={"fake": provider},
    )
    summary = await service.discover(db, context(auth, execution))
    summary2 = await service.discover(db, context(auth, execution))

    assert summary.candidate_count == 1
    assert summary.duplicate_candidate_count == 1
    assert summary.rejected_result_count == 1
    assert summary2.candidate_count == 0
    candidates = await service.list_candidates(db, auth, execution.id)
    assert len(candidates) == 1
    assert candidates[0].canonical_url == "https://sante.gouv.fr/annuaire?id=1"
    assert candidates[0].domain == "sante.gouv.fr"
    assert len(candidates[0].snippet) == 1000
    assert candidates[0].initial_trust_tier == "high"
    assert provider.requests[0].country_code == "FR"
    assert provider.requests[0].search_language == "fr"


@pytest.mark.asyncio
async def test_same_url_may_be_used_in_separate_executions(db: AsyncSession, auth):
    first = await create_execution(db, auth)
    second = await create_execution(db, auth)
    provider = FakeProvider(
        [SearchProviderResult(rank=1, url="https://real-source.fr/page", title="Title", snippet="Snippet")]
    )
    service = SourceDiscoveryService(
        planner=FakePlanner(planned_queries()[:1]),
        providers={"fake": provider},
    )

    first_summary = await service.discover(db, context(auth, first))
    second_summary = await service.discover(db, context(auth, second))

    assert first_summary.candidate_count == 1
    assert second_summary.candidate_count == 1


@pytest.mark.asyncio
async def test_query_records_persist_zero_results_and_failure(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    zero_service = SourceDiscoveryService(
        planner=FakePlanner(planned_queries()[:1]),
        providers={"fake": FakeProvider([])},
    )
    zero_summary = await zero_service.discover(db, context(auth, execution))
    assert zero_summary.succeeded_query_count == 1
    assert zero_summary.candidate_count == 0

    failing_service = SourceDiscoveryService(
        planner=FakePlanner(planned_queries()[:1]),
        providers={"fake": FakeProvider(error=SearchProviderAuthError("bad credentials"))},
    )
    failure_summary = await failing_service.discover(db, context(auth, execution))
    assert failure_summary.failed_query_count == 1

    queries = await zero_service.list_queries(db, auth, execution.id)
    statuses = {query.status for query in queries}
    assert SourceDiscoveryQueryStatus.SUCCEEDED.value in statuses
    assert SourceDiscoveryQueryStatus.FAILED.value in statuses
    assert all("secret" not in (query.error_message or "") for query in queries)


@pytest.mark.asyncio
async def test_provider_errors_do_not_create_candidates_or_downstream_rows(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    service = SourceDiscoveryService(
        planner=FakePlanner(planned_queries()[:1]),
        providers={"fake": FakeProvider(error=SearchProviderAuthError("bad credentials"))},
    )
    await service.discover(db, context(auth, execution))

    candidate_count = await db.scalar(select(func.count()).select_from(ScrapingSourceCandidate))
    facility_count = await db.scalar(select(func.count()).select_from(RehabilitationFacility))
    source_count = await db.scalar(select(func.count()).select_from(RehabilitationSource))
    evidence_count = await db.scalar(select(func.count()).select_from(RehabilitationFieldEvidence))
    assert candidate_count == 0
    assert facility_count == 0
    assert source_count == 0
    assert evidence_count == 0


@pytest.mark.asyncio
async def test_source_discovery_listing_is_org_scoped(db: AsyncSession, auth):
    execution = await create_execution(db, auth)
    service = SourceDiscoveryService(
        planner=FakePlanner(planned_queries()[:1]),
        providers={"fake": FakeProvider([SearchProviderResult(rank=1, url="https://real.fr/a", title="T", snippet="S")])},
    )
    await service.discover(db, context(auth, execution))
    other_auth = await create_other_auth(db)

    with pytest.raises(NotFoundError):
        await service.list_candidates(db, other_auth, execution.id)
    with pytest.raises(NotFoundError):
        await service.list_queries(db, other_auth, execution.id)


def test_query_planner_output_is_bounded_and_deduped():
    from app.services.scraping.source_discovery_service import _dedupe_planned_queries

    deduped = _dedupe_planned_queries(planned_queries(), max_queries=2)
    assert len(deduped) == 2
    assert deduped[0].query != deduped[1].query


def test_source_discovery_service_does_not_import_mock_production_modules():
    import app.services.scraping.source_discovery_service as module

    names = set(module.__dict__)
    assert "mock_tools" not in names
    assert "mock_facility_generator" not in names
