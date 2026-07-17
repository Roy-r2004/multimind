"""Real source discovery foundation for universal acquisition."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.dependencies import AuthContext
from app.core.exceptions import NotFoundError, ValidationError
from app.db.models import (
    ScrapingExecution,
    ScrapingSourceCandidate,
    ScrapingSourceDiscoveryQuery,
    SourceCandidateStatus,
    SourceDiscoveryQueryStatus,
)
from app.llm.catalog import get_model
from app.llm.prompt_engine import get_prompt_engine
from app.llm.providers import LLMProvider, get_provider_registry
from app.schemas.api import (
    SourceCandidateResponse,
    SourceDiscoveryContext,
    SourceDiscoveryPlannedQuery,
    SourceDiscoveryQueryPlan,
    SourceDiscoveryQueryResponse,
    SourceDiscoverySummary,
)
from app.services.scraping.search_providers import BraveSearchProvider
from app.services.scraping.search_providers.base import (
    SearchProvider,
    SearchProviderError,
    SearchProviderRequest,
    SearchProviderResult,
)
from app.services.scraping.url_canonicalization import UrlRejected, canonicalize_discovery_url

MAX_QUERY_LENGTH = 240
MAX_ERROR_MESSAGE_LENGTH = 500


class SourceDiscoveryQueryPlanner:
    async def plan_queries(self, context: SourceDiscoveryContext) -> list[SourceDiscoveryPlannedQuery]:
        settings = get_settings()
        max_queries = min(max(settings.brave_search_max_queries_per_discovery, 1), 8)
        model = get_model("gpt-4.1")
        provider = get_provider_registry().get_provider(model.provider)
        prompt = get_prompt_engine().render(
            "scraping/source_discovery_query_planner.j2",
            max_queries=max_queries,
            max_query_length=MAX_QUERY_LENGTH,
            mission_goal=context.mission_goal,
            requested_fields=context.requested_fields,
            country_name=context.country_name,
            country_code=context.country_code,
            region_name=context.region_name,
            region_code=context.region_code,
            language_name=context.language_name,
            language_code=context.language_code,
            source_category=context.source_category,
            blueprint_context=context.blueprint_context,
        )
        response = await provider.complete(
            system="You return strict JSON for source-discovery query planning.",
            user=prompt,
            model=model.provider_model,
            max_tokens=1500,
        )
        try:
            raw = LLMProvider.parse_json_response(response.text)
            plan = SourceDiscoveryQueryPlan.model_validate(raw)
        except (PydanticValidationError, Exception) as exc:
            raise ValidationError("Source discovery query planning failed.") from exc
        return _dedupe_planned_queries(plan.queries, max_queries=max_queries)


class SourceDiscoveryService:
    def __init__(
        self,
        *,
        planner: SourceDiscoveryQueryPlanner | None = None,
        providers: dict[str, SearchProvider] | None = None,
    ) -> None:
        self.planner = planner or SourceDiscoveryQueryPlanner()
        self.providers = providers or {"brave": BraveSearchProvider()}

    async def discover(
        self,
        db: AsyncSession,
        context: SourceDiscoveryContext,
    ) -> SourceDiscoverySummary:
        planned_queries = await self.planner.plan_queries(context)
        provider = self._provider(context.provider)
        summary = SourceDiscoverySummary(
            provider=provider.name,
            planned_query_count=len(planned_queries),
            query_count=0,
            succeeded_query_count=0,
            failed_query_count=0,
            candidate_count=0,
            duplicate_candidate_count=0,
            rejected_result_count=0,
        )

        for planned in planned_queries:
            query_row = await self._create_query_row(db, context, provider.name, planned)
            summary.query_count += 1
            try:
                results = await provider.search(
                    SearchProviderRequest(
                        query=planned.query,
                        country_code=context.country_code,
                        search_language=planned.language_code or context.language_code,
                        result_limit=get_settings().brave_search_results_per_query,
                        metadata={
                            "source_category": context.source_category,
                            "region_code": context.region_code,
                        },
                    )
                )
            except SearchProviderError as exc:
                await self._mark_query_failed(db, query_row, exc.code, str(exc))
                summary.failed_query_count += 1
                continue

            persisted, duplicates, rejected = await self._persist_candidates(
                db,
                context,
                provider.name,
                query_row,
                results,
            )
            query_row.status = SourceDiscoveryQueryStatus.SUCCEEDED
            query_row.completed_at = datetime.now(UTC)
            query_row.result_count = persisted
            query_row.error_code = None
            query_row.error_message = None
            await db.flush()
            await db.commit()
            summary.succeeded_query_count += 1
            summary.candidate_count += persisted
            summary.duplicate_candidate_count += duplicates
            summary.rejected_result_count += rejected

        return summary

    async def list_queries(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        coverage_cell_id: str | None = None,
        provider: str | None = None,
        source_category: str | None = None,
        language_code: str | None = None,
        region_code: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SourceDiscoveryQueryResponse]:
        await self._assert_execution_access(db, auth, execution_id)
        query = (
            select(ScrapingSourceDiscoveryQuery)
            .where(
                ScrapingSourceDiscoveryQuery.organization_id == auth.org_id,
                ScrapingSourceDiscoveryQuery.execution_id == execution_id,
            )
            .order_by(ScrapingSourceDiscoveryQuery.created_at.desc())
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 500))
        )
        if coverage_cell_id:
            query = query.where(ScrapingSourceDiscoveryQuery.coverage_cell_id == coverage_cell_id)
        if provider:
            query = query.where(ScrapingSourceDiscoveryQuery.provider == provider)
        if source_category:
            query = query.where(ScrapingSourceDiscoveryQuery.source_category == source_category)
        if language_code:
            query = query.where(ScrapingSourceDiscoveryQuery.language_code == language_code)
        if region_code:
            query = query.where(ScrapingSourceDiscoveryQuery.region_code == region_code)
        if status:
            query = query.where(ScrapingSourceDiscoveryQuery.status == SourceDiscoveryQueryStatus(status))
        result = await db.execute(query)
        return [self._query_response(row) for row in result.scalars().all()]

    async def list_candidates(
        self,
        db: AsyncSession,
        auth: AuthContext,
        execution_id: str,
        *,
        coverage_cell_id: str | None = None,
        provider: str | None = None,
        source_category: str | None = None,
        language_code: str | None = None,
        region_code: str | None = None,
        status: str | None = None,
        domain: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SourceCandidateResponse]:
        await self._assert_execution_access(db, auth, execution_id)
        query = (
            select(ScrapingSourceCandidate)
            .where(
                ScrapingSourceCandidate.organization_id == auth.org_id,
                ScrapingSourceCandidate.execution_id == execution_id,
            )
            .order_by(ScrapingSourceCandidate.discovered_at.desc(), ScrapingSourceCandidate.rank)
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 500))
        )
        if coverage_cell_id:
            query = query.where(ScrapingSourceCandidate.coverage_cell_id == coverage_cell_id)
        if provider:
            query = query.where(ScrapingSourceCandidate.provider == provider)
        if source_category:
            query = query.where(ScrapingSourceCandidate.source_category == source_category)
        if language_code:
            query = query.where(ScrapingSourceCandidate.language_code == language_code)
        if region_code:
            query = query.where(ScrapingSourceCandidate.region_code == region_code)
        if status:
            query = query.where(ScrapingSourceCandidate.status == SourceCandidateStatus(status))
        if domain:
            query = query.where(ScrapingSourceCandidate.domain == domain.lower())
        result = await db.execute(query)
        return [self._candidate_response(row) for row in result.scalars().all()]

    def _provider(self, provider_name: str) -> SearchProvider:
        provider = self.providers.get(provider_name)
        if provider is None:
            raise ValidationError(f"Unsupported source discovery provider: {provider_name}")
        return provider

    async def _create_query_row(
        self,
        db: AsyncSession,
        context: SourceDiscoveryContext,
        provider: str,
        planned: SourceDiscoveryPlannedQuery,
    ) -> ScrapingSourceDiscoveryQuery:
        now = datetime.now(UTC)
        row = ScrapingSourceDiscoveryQuery(
            organization_id=context.organization_id,
            execution_id=context.execution_id,
            coverage_cell_id=context.coverage_cell_id,
            task_id=context.task_id,
            country_code=context.country_code,
            country_name=context.country_name,
            region_code=context.region_code,
            region_name=context.region_name,
            language_code=planned.language_code or context.language_code,
            language_name=context.language_name,
            source_category=context.source_category,
            query_text=planned.query,
            provider=provider,
            status=SourceDiscoveryQueryStatus.RUNNING,
            requested_at=now,
            result_count=0,
            metadata_json={"purpose": planned.purpose},
        )
        db.add(row)
        await db.flush()
        await db.commit()
        await db.refresh(row)
        return row

    async def _mark_query_failed(
        self,
        db: AsyncSession,
        row: ScrapingSourceDiscoveryQuery,
        code: str,
        message: str,
    ) -> None:
        row.status = SourceDiscoveryQueryStatus.FAILED
        row.completed_at = datetime.now(UTC)
        row.error_code = code[:80]
        row.error_message = _bounded_error(message)
        row.result_count = 0
        await db.flush()
        await db.commit()

    async def _persist_candidates(
        self,
        db: AsyncSession,
        context: SourceDiscoveryContext,
        provider: str,
        query_row: ScrapingSourceDiscoveryQuery,
        results: list[SearchProviderResult],
    ) -> tuple[int, int, int]:
        persisted = 0
        duplicates = 0
        rejected = 0
        seen_in_query: set[str] = set()
        for result in results:
            try:
                canonical = canonicalize_discovery_url(result.url)
            except UrlRejected:
                rejected += 1
                continue
            if canonical.canonical_url in seen_in_query:
                duplicates += 1
                continue
            seen_in_query.add(canonical.canonical_url)
            existing = await self._existing_candidate(db, context, canonical.canonical_url)
            if existing is not None:
                duplicates += 1
                continue
            db.add(
                ScrapingSourceCandidate(
                    organization_id=context.organization_id,
                    execution_id=context.execution_id,
                    coverage_cell_id=context.coverage_cell_id,
                    discovery_query_id=query_row.id,
                    provider=provider,
                    provider_result_id=result.provider_result_id,
                    rank=result.rank,
                    url=canonical.original_url,
                    canonical_url=canonical.canonical_url,
                    domain=canonical.domain,
                    title=result.title[:300],
                    snippet=result.snippet[:1000],
                    country_code=context.country_code,
                    country_name=context.country_name,
                    region_code=context.region_code,
                    region_name=context.region_name,
                    language_code=query_row.language_code,
                    language_name=context.language_name,
                    source_category=context.source_category,
                    initial_relevance_score=Decimal(str(_relevance_for_rank(result.rank))),
                    initial_trust_tier=_trust_tier(context.source_category),
                    status=SourceCandidateStatus.DISCOVERED,
                    discovered_at=datetime.now(UTC),
                    metadata_json=_safe_metadata(result.metadata),
                )
            )
            persisted += 1
        await db.flush()
        return persisted, duplicates, rejected

    async def _existing_candidate(
        self,
        db: AsyncSession,
        context: SourceDiscoveryContext,
        canonical_url: str,
    ) -> ScrapingSourceCandidate | None:
        query = select(ScrapingSourceCandidate).where(
            ScrapingSourceCandidate.organization_id == context.organization_id,
            ScrapingSourceCandidate.canonical_url == canonical_url,
        )
        if context.execution_id is None:
            query = query.where(ScrapingSourceCandidate.execution_id.is_(None))
        else:
            query = query.where(ScrapingSourceCandidate.execution_id == context.execution_id)
        if context.coverage_cell_id is None:
            query = query.where(ScrapingSourceCandidate.coverage_cell_id.is_(None))
        else:
            query = query.where(ScrapingSourceCandidate.coverage_cell_id == context.coverage_cell_id)
        result = await db.execute(query.limit(1))
        return result.scalar_one_or_none()

    async def _assert_execution_access(
        self, db: AsyncSession, auth: AuthContext, execution_id: str
    ) -> None:
        result = await db.execute(
            select(ScrapingExecution.id).where(
                ScrapingExecution.id == execution_id,
                ScrapingExecution.organization_id == auth.org_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise NotFoundError("ScrapingExecution", execution_id)

    def _query_response(self, row: ScrapingSourceDiscoveryQuery) -> SourceDiscoveryQueryResponse:
        return SourceDiscoveryQueryResponse(
            id=row.id,
            organization_id=row.organization_id,
            execution_id=row.execution_id,
            coverage_cell_id=row.coverage_cell_id,
            task_id=row.task_id,
            country_code=row.country_code,
            country_name=row.country_name,
            region_code=row.region_code,
            region_name=row.region_name,
            language_code=row.language_code,
            language_name=row.language_name,
            source_category=row.source_category,
            query_text=row.query_text,
            provider=row.provider,
            status=row.status.value,
            requested_at=row.requested_at,
            completed_at=row.completed_at,
            result_count=row.result_count,
            error_code=row.error_code,
            error_message=row.error_message,
            metadata_json=row.metadata_json,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _candidate_response(self, row: ScrapingSourceCandidate) -> SourceCandidateResponse:
        return SourceCandidateResponse(
            id=row.id,
            organization_id=row.organization_id,
            execution_id=row.execution_id,
            coverage_cell_id=row.coverage_cell_id,
            discovery_query_id=row.discovery_query_id,
            provider=row.provider,
            provider_result_id=row.provider_result_id,
            rank=row.rank,
            url=row.url,
            canonical_url=row.canonical_url,
            domain=row.domain,
            title=row.title,
            snippet=row.snippet,
            country_code=row.country_code,
            country_name=row.country_name,
            region_code=row.region_code,
            region_name=row.region_name,
            language_code=row.language_code,
            language_name=row.language_name,
            source_category=row.source_category,
            initial_relevance_score=float(row.initial_relevance_score),
            initial_trust_tier=row.initial_trust_tier,
            status=row.status.value,
            discovered_at=row.discovered_at,
            metadata_json=row.metadata_json,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


def _dedupe_planned_queries(
    queries: list[SourceDiscoveryPlannedQuery],
    *,
    max_queries: int,
) -> list[SourceDiscoveryPlannedQuery]:
    deduped: list[SourceDiscoveryPlannedQuery] = []
    seen: set[str] = set()
    for query in queries:
        normalized = " ".join(query.query.casefold().split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(query)
        if len(deduped) >= max_queries:
            break
    if not deduped:
        raise ValidationError("Source discovery planner returned no usable queries.")
    return deduped


def _relevance_for_rank(rank: int) -> float:
    return max(0.1, min(1.0, 1.0 - ((max(rank, 1) - 1) * 0.05)))


def _trust_tier(source_category: str) -> str:
    normalized = source_category.casefold()
    if any(term in normalized for term in ("official", "government", "registry", "license", "regulator", "ministry")):
        return "high"
    if any(term in normalized for term in ("directory", "association", "hospital", "ngo")):
        return "medium"
    return "unknown"


def _bounded_error(message: str) -> str:
    return str(message or "")[:MAX_ERROR_MESSAGE_LENGTH]


def _safe_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if "key" in key.lower() or "token" in key.lower() or "secret" in key.lower():
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[key[:80]] = value if not isinstance(value, str) else value[:500]
    return safe


source_discovery_service = SourceDiscoveryService()
