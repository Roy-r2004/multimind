"""One bounded, restart-safe Phase 5 worker slice."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.services.scraping.directory_expansion_service import (
    claim_directory_expansion_batch, identify_and_prepare_directory,
    persist_prepared_expansion, reload_prepared_directory_content,
)
from app.services.scraping.phase5_contracts import (
    RetryableFailure, TerminalActionFailure,
)
from app.services.scraping.phase5_job_service import (
    claim_batch, persist_retrieval_resources, record_blocked_failure,
    record_retryable_failure, record_terminal_failure,
)
from app.services.scraping.phase5_orchestration_service import (
    create_expansion_for_retrieval, create_typed_fallback_job,
    phase5_readiness, seed_initial_http_jobs,
)
from app.services.scraping.phase5_retrieval_service import (
    BrowserActionType, FirecrawlRetriever, NormalHttpRetriever,
    PlaywrightRetriever,
)


class Phase5ExecutionService:
    def __init__(self, *, session_factory=AsyncSessionLocal, http=None,
                 firecrawl=None, playwright=None, now_factory=None):
        self.sessions = session_factory
        self.http = http or NormalHttpRetriever()
        self.firecrawl = firecrawl or FirecrawlRetriever()
        self.playwright = playwright or PlaywrightRetriever()
        self.now = now_factory or (lambda: datetime.now(UTC))

    async def run_work_slice(self, organization_id: str, execution_id: str) -> dict:
        settings, now = get_settings(), self.now()
        async with self.sessions.begin() as session:
            seeded = await seed_initial_http_jobs(
                session, organization_id=organization_id, execution_id=execution_id,
                requested_at=now, batch_size=settings.phase5_claim_batch_size)
        counts = {"seeded": seeded.created, "retrieval_claims": 0,
                  "retrieval_results": 0, "expansion_slices": 0,
                  "stale_claims": 0, "blocked": 0}
        for tool, retriever in (
            ("http", self.http), ("firecrawl", self.firecrawl),
            ("playwright", self.playwright),
        ):
            async with self.sessions.begin() as session:
                claims = await claim_batch(
                    session, organization_id=organization_id,
                    execution_id=execution_id, now=now,
                    lease_duration=timedelta(seconds=settings.phase5_lease_seconds),
                    batch_size=settings.phase5_claim_batch_size,
                    selected_tool=tool)
            for claim in claims:
                counts["retrieval_claims"] += 1
                if tool == "playwright":
                    action_state = claim.operational_metadata.get("action_state", {})
                    action = {
                        "pagination": BrowserActionType.PAGINATE,
                        "load_more": BrowserActionType.LOAD_MORE,
                        "structured_api": BrowserActionType.MAP_INTERACTION,
                    }.get(action_state.get("relationship"), BrowserActionType.NAVIGATE)
                    result = await retriever.retrieve(
                        url=claim.canonical_url, action_type=action,
                        continuation_state=action_state,
                        requested_at=now, fetched_at=self.now())
                else:
                    result = await retriever.retrieve(
                        url=claim.canonical_url, requested_at=now, fetched_at=self.now())
                if result.outcome == "succeeded":
                    async with self.sessions.begin() as session:
                        persisted = await persist_retrieval_resources(
                            session, claimed_job=claim, resources=result.resources,
                            completed_at=self.now())
                        if persisted and persisted[0].outcome == "stale_claim":
                            counts["stale_claims"] += 1
                            continue
                        for item in persisted:
                            if item.record_id:
                                await create_expansion_for_retrieval(
                                    session, retrieval_result_id=item.record_id,
                                    organization_id=organization_id,
                                    execution_id=execution_id, requested_at=self.now())
                        counts["retrieval_results"] += len(persisted)
                    continue
                failure = TerminalActionFailure(
                    category=(result.failure_category.value
                              if result.failure_category else "retrieval_failure"),
                    public_message=result.public_message or "Retrieval action failed.")
                async with self.sessions.begin() as session:
                    if result.outcome == "retryable_failure":
                        outcome = await record_retryable_failure(
                            session, job_id=claim.id, organization_id=organization_id,
                            execution_id=execution_id, claim_token=claim.claim_token,
                            failure=RetryableFailure(
                                category=failure.category,
                                public_message=failure.public_message,
                                next_retry_at=self.now() + timedelta(minutes=1)))
                    elif result.outcome == "blocked":
                        counts["blocked"] += 1
                        outcome = await record_blocked_failure(
                            session, job_id=claim.id, organization_id=organization_id,
                            execution_id=execution_id, claim_token=claim.claim_token,
                            failure=failure, completed_at=self.now())
                    else:
                        outcome = await record_terminal_failure(
                            session, job_id=claim.id, organization_id=organization_id,
                            execution_id=execution_id, claim_token=claim.claim_token,
                            failure=failure, completed_at=self.now())
                    counts["stale_claims"] += outcome.outcome == "stale_claim"
        async with self.sessions.begin() as session:
            expansions = await claim_directory_expansion_batch(
                session, organization_id=organization_id, execution_id=execution_id,
                now=self.now(),
                lease_duration=timedelta(seconds=settings.phase5_lease_seconds),
                batch_size=settings.phase5_claim_batch_size)
        for claim in expansions:
            async with self.sessions() as session:
                source = await reload_prepared_directory_content(
                    session, claimed_job=claim, observed_at=self.now())
            prepared = identify_and_prepare_directory(source)
            async with self.sessions.begin() as session:
                persisted = await persist_prepared_expansion(session, prepared)
                if persisted.outcome == "stale_claim":
                    counts["stale_claims"] += 1
                    continue
                counts["expansion_slices"] += 1
                if prepared.outcome.value in {
                    "requires_managed_rendering",
                    "unsupported_content_representation",
                    "requires_browser_interaction",
                }:
                    await create_typed_fallback_job(
                        session, expansion_job_id=claim.id,
                        organization_id=organization_id, execution_id=execution_id,
                        requested_at=self.now())
        async with self.sessions() as session:
            readiness = await phase5_readiness(
                session, organization_id=organization_id, execution_id=execution_id)
        return {**counts, "ready_for_review": readiness.ready_for_review,
                "remaining_runnable": readiness.runnable,
                "blocked_work": readiness.blocked,
                "next_retry_at": readiness.next_retry_at}


phase5_execution_service = Phase5ExecutionService()
