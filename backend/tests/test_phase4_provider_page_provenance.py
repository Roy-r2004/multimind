"""Phase 4 provider page provenance unit tests (non-Docker)."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.db.models import ScrapingExecution, ScrapingExecutionStatus, ScrapingSourceCandidate
from app.services.scraping.source_discovery_claim_service import ClaimedQueryJob, generate_claim_token
from app.services.scraping.source_discovery_provider_service import (
    DiscoveryProviderContinuation,
    DiscoveryProviderExecutionResult,
    DiscoveryProviderResultItem,
)
from app.services.scraping.source_discovery_result_service import (
    PreparedDiscoveryBatch,
    PreparedDiscoveryResult,
    reconcile_candidate_page_provenance,
    prepare_provider_results,
)

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "phase4_serper_smoke.py"
_spec = importlib.util.spec_from_file_location("phase4_serper_smoke", SCRIPT_PATH)
assert _spec and _spec.loader
smoke = importlib.util.module_from_spec(_spec)
sys.modules["phase4_serper_smoke"] = smoke
_spec.loader.exec_module(smoke)

FIXED_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)


def _claimed(**overrides: Any) -> ClaimedQueryJob:
    base = ClaimedQueryJob(
        id="query-job-1",
        organization_id="org-1",
        execution_id="exec-1",
        query_text="test query",
        provider="serper",
        claim_token=generate_claim_token(),
        claimed_at=FIXED_NOW,
        lease_expires_at=FIXED_NOW + timedelta(seconds=60),
        attempt_count=1,
        last_attempt_at=FIXED_NOW,
        priority=100,
        generation_ordinal=1,
        discovery_round=1,
        purpose="seed",
        country_code="LB",
        country_name="Lebanon",
        region_code=None,
        region_name=None,
        language_code="en",
        language_name="English",
        source_category="directory",
        scope_level="countrywide",
        important_city=None,
        query_job_fingerprint="f" * 64,
        plan_hash_snapshot="p" * 64,
        requested_at=FIXED_NOW,
        next_page_number=1,
    )
    return replace(base, **overrides) if overrides else base


def _provider_result(
    claimed: ClaimedQueryJob,
    *,
    page_number: int,
    urls: list[str],
    has_more: bool = False,
) -> DiscoveryProviderExecutionResult:
    results = tuple(
        DiscoveryProviderResultItem(
            original_url=url,
            title="Title",
            snippet="Snippet",
            rank=idx,
            provider="serper",
            provider_result_type="organic",
            query_job_id=claimed.id,
            organization_id=claimed.organization_id,
            execution_id=claimed.execution_id,
            claim_token=claimed.claim_token,
            scope_level=claimed.scope_level,
            language_code=claimed.language_code,
            region_code=claimed.region_code,
            region_name=claimed.region_name,
            important_city=claimed.important_city,
            country_code=claimed.country_code,
            discovered_at=FIXED_NOW,
            provider_page_number=page_number,
        )
        for idx, url in enumerate(urls, start=1)
    )
    return DiscoveryProviderExecutionResult(
        outcome="succeeded",
        provider="serper",
        query_job_id=claimed.id,
        organization_id=claimed.organization_id,
        execution_id=claimed.execution_id,
        claim_token=claimed.claim_token,
        discovered_at=FIXED_NOW,
        results=results,
        raw_result_count=len(results),
        accepted_result_count=len(results),
        page_number=page_number,
        continuation=DiscoveryProviderContinuation(
            requested_page_size=10,
            returned_result_count=len(results),
            has_more=has_more,
            page_number=page_number,
            page_fingerprint="b" * 64,
        ),
    )


def test_prepare_results_retain_requested_page():
    claimed = _claimed(next_page_number=2)
    batch = prepare_provider_results(
        claimed,
        _provider_result(claimed, page_number=2, urls=["https://example.org/a"]),
        clock=FIXED_NOW,
    )
    assert batch.ready
    assert batch.provider_page_number == 2
    assert batch.results[0].provider_page_number == 2


def test_prepare_unsafe_rejected_candidate_retains_page():
    claimed = _claimed()
    batch = prepare_provider_results(
        claimed,
        _provider_result(
            claimed,
            page_number=1,
            urls=["http://127.0.0.1/private"],
        ),
        clock=FIXED_NOW,
    )
    assert batch.ready
    assert len(batch.results) == 1
    assert batch.results[0].provider_page_number == 1
    assert batch.results[0].persist_candidate is True
    assert batch.results[0].persist_crawl_node is False


def test_reconcile_repairs_null_page():
    candidate = ScrapingSourceCandidate(
        organization_id="org-1",
        execution_id="exec-1",
        discovery_query_id="query-job-1",
        provider="serper",
        rank=1,
        url="https://example.org",
        canonical_url="https://example.org",
        domain="example.org",
        country_code="LB",
        country_name="Lebanon",
        language_code="en",
        language_name="English",
        source_category="directory",
        provider_page_number=None,
    )
    assert reconcile_candidate_page_provenance(candidate, 1) is None
    assert candidate.provider_page_number == 1


def test_reconcile_keeps_matching_non_null_page():
    candidate = ScrapingSourceCandidate(
        organization_id="org-1",
        execution_id="exec-1",
        discovery_query_id="query-job-1",
        provider="serper",
        rank=1,
        url="https://example.org",
        canonical_url="https://example.org",
        domain="example.org",
        country_code="LB",
        country_name="Lebanon",
        language_code="en",
        language_name="English",
        source_category="directory",
        provider_page_number=1,
    )
    assert reconcile_candidate_page_provenance(candidate, 1) is None
    assert candidate.provider_page_number == 1


def test_reconcile_preserves_first_seen_on_cross_page_duplicate():
    """Legitimate cross-page duplicate: page 1 preserved when same URL reappears on page 2."""
    candidate = ScrapingSourceCandidate(
        organization_id="org-1",
        execution_id="exec-1",
        discovery_query_id="query-job-1",
        provider="serper",
        rank=1,
        url="https://example.org",
        canonical_url="https://example.org",
        domain="example.org",
        country_code="LB",
        country_name="Lebanon",
        language_code="en",
        language_name="English",
        source_category="directory",
        provider_page_number=1,
    )
    assert reconcile_candidate_page_provenance(candidate, 2) is None
    assert candidate.provider_page_number == 1


def test_reconcile_conflicts_on_backward_provenance():
    """Impossible backward provenance: existing page 2 vs requested page 1 fails closed."""
    candidate = ScrapingSourceCandidate(
        organization_id="org-1",
        execution_id="exec-1",
        discovery_query_id="query-job-1",
        provider="serper",
        rank=1,
        url="https://example.org",
        canonical_url="https://example.org",
        domain="example.org",
        country_code="LB",
        country_name="Lebanon",
        language_code="en",
        language_name="English",
        source_category="directory",
        provider_page_number=2,
    )
    assert reconcile_candidate_page_provenance(candidate, 1) == "provider_page_provenance_conflict"
    assert candidate.provider_page_number == 2


@pytest.mark.asyncio
async def test_verify_ok_true_when_all_candidate_pages_match():
    job = MagicMock()
    job.status = MagicMock(value="succeeded")
    job.provider = "serper"
    job.requested_at = FIXED_NOW
    job.attempt_count = 1
    job.next_page_number = 1
    job.pages_completed = 1
    job.pagination_completed = True
    job.last_page_result_count = 9
    job.organization_id = "org-1"

    candidates = [
        MagicMock(
            rank=1,
            provider_page_number=1,
            url="https://example.org/1",
            canonical_url="https://example.org/1",
            status=MagicMock(value="discovered"),
            crawl_node_id="node-1",
            metadata_json={},
        ),
        MagicMock(
            rank=2,
            provider_page_number=1,
            url="https://example.org/2",
            canonical_url="https://example.org/2",
            status=MagicMock(value="discovered"),
            crawl_node_id="node-2",
            metadata_json={},
        ),
    ]

    class VerifySession:
        async def get(self, model: type, key: str) -> Any:
            if model is smoke.ScrapingSourceDiscoveryQuery:
                return job
            return None

        async def execute(self, stmt: Any) -> Any:
            result = MagicMock()
            stmt_text = str(stmt)
            if "scraping_source_candidates" in stmt_text:
                result.scalars.return_value.all.return_value = candidates
            elif "scraping_events" in stmt_text:
                result.all.return_value = []
            else:
                execution = ScrapingExecution(
                    id="exec-1",
                    organization_id="org-1",
                    mission_id="m-1",
                    execution_type="mission_campaign",
                    execution_plan_schema_version="2",
                    status=ScrapingExecutionStatus.PAUSED,
                    current_stage="web_discovery",
                    country_profile_json={smoke.PHASE4_SMOKE_PREPARED_KEY: True},
                )
                result.scalar_one_or_none.return_value = execution
            return result

    class Ctx:
        async def __aenter__(self) -> VerifySession:
            return VerifySession()

        async def __aexit__(self, *args: Any) -> None:
            return None

    payload = await smoke.verify_smoke(
        MagicMock(return_value=Ctx()),
        organization_id="org-1",
        execution_id="exec-1",
        query_job_id="job-1",
    )
    assert payload["ok"] is True
    assert payload["provider_page_numbers"] == [1]
    assert payload["candidate_provider_page_null_count"] == 0
    assert payload["candidate_provider_page_mismatch_count"] == 0


@pytest.mark.asyncio
async def test_verify_ok_false_for_null_provider_page():
    job = MagicMock()
    job.status = MagicMock(value="succeeded")
    job.provider = "serper"
    job.requested_at = FIXED_NOW
    job.attempt_count = 1
    job.next_page_number = 1
    job.pages_completed = 1
    job.pagination_completed = True
    job.last_page_result_count = 1
    job.organization_id = "org-1"

    candidates = [
        MagicMock(
            rank=1,
            provider_page_number=None,
            url="https://example.org/1",
            canonical_url="https://example.org/1",
            status=MagicMock(value="discovered"),
            crawl_node_id="node-1",
            metadata_json={},
        ),
    ]

    class VerifySession:
        async def get(self, model: type, key: str) -> Any:
            if model is smoke.ScrapingSourceDiscoveryQuery:
                return job
            return None

        async def execute(self, stmt: Any) -> Any:
            result = MagicMock()
            stmt_text = str(stmt)
            if "scraping_source_candidates" in stmt_text:
                result.scalars.return_value.all.return_value = candidates
            elif "scraping_events" in stmt_text:
                result.all.return_value = []
            else:
                execution = ScrapingExecution(
                    id="exec-1",
                    organization_id="org-1",
                    mission_id="m-1",
                    execution_type="mission_campaign",
                    execution_plan_schema_version="2",
                    status=ScrapingExecutionStatus.PAUSED,
                    current_stage="web_discovery",
                )
                result.scalar_one_or_none.return_value = execution
            return result

    class Ctx:
        async def __aenter__(self) -> VerifySession:
            return VerifySession()

        async def __aexit__(self, *args: Any) -> None:
            return None

    payload = await smoke.verify_smoke(
        MagicMock(return_value=Ctx()),
        organization_id="org-1",
        execution_id="exec-1",
        query_job_id="job-1",
    )
    assert payload["ok"] is False
    assert payload["candidate_provider_page_null_count"] == 1


@pytest.mark.asyncio
async def test_verify_ok_false_for_mismatched_provider_page():
    job = MagicMock()
    job.status = MagicMock(value="succeeded")
    job.provider = "serper"
    job.requested_at = FIXED_NOW
    job.attempt_count = 1
    job.next_page_number = 1
    job.pages_completed = 1
    job.pagination_completed = True
    job.last_page_result_count = 1
    job.organization_id = "org-1"

    candidates = [
        MagicMock(
            rank=1,
            provider_page_number=2,
            url="https://example.org/1",
            canonical_url="https://example.org/1",
            status=MagicMock(value="discovered"),
            crawl_node_id="node-1",
            metadata_json={},
        ),
    ]

    class VerifySession:
        async def get(self, model: type, key: str) -> Any:
            if model is smoke.ScrapingSourceDiscoveryQuery:
                return job
            return None

        async def execute(self, stmt: Any) -> Any:
            result = MagicMock()
            stmt_text = str(stmt)
            if "scraping_source_candidates" in stmt_text:
                result.scalars.return_value.all.return_value = candidates
            elif "scraping_events" in stmt_text:
                result.all.return_value = []
            else:
                execution = ScrapingExecution(
                    id="exec-1",
                    organization_id="org-1",
                    mission_id="m-1",
                    execution_type="mission_campaign",
                    execution_plan_schema_version="2",
                    status=ScrapingExecutionStatus.PAUSED,
                    current_stage="web_discovery",
                )
                result.scalar_one_or_none.return_value = execution
            return result

    class Ctx:
        async def __aenter__(self) -> VerifySession:
            return VerifySession()

        async def __aexit__(self, *args: Any) -> None:
            return None

    payload = await smoke.verify_smoke(
        MagicMock(return_value=Ctx()),
        organization_id="org-1",
        execution_id="exec-1",
        query_job_id="job-1",
    )
    assert payload["ok"] is False
    assert payload["candidate_provider_page_mismatch_count"] == 1
    assert payload["provider_page_numbers"] == [2]
