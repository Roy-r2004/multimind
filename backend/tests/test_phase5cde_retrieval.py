"""Network-free Phase 5C-E retrieval and fallback tests."""

from datetime import UTC, datetime

import httpx
import pytest

from app.core.config import Settings
from app.services.scraping.phase5_contracts import Phase5WorkKind
from app.services.scraping.phase5_contracts import prepare_phase5_job
from app.services.scraping.phase5_retrieval_service import (
    BrowserActionType, FirecrawlRetriever, NormalHttpRetriever,
    PlaywrightRetriever, RetrievalFailureCategory, fallback_work_kind,
    prepare_resource,
)
from app.services.scraping.source_retrieval_service import FetchResult

NOW = datetime(2026, 7, 28, tzinfo=UTC)


class HttpBoundary:
    async def _validate_url(self, url):
        if "127.0.0.1" in url:
            from app.db.models import SourceRetrievalAttemptStatus
            from app.services.scraping.source_retrieval_service import SourceRetrievalError
            raise SourceRetrievalError(
                SourceRetrievalAttemptStatus.PRIVATE_OR_RESERVED_ADDRESS, "unsafe")
        return type("Validated", (), {"url": url})()

    async def _fetch(self, validated):
        return FetchResult(
            final_url=validated.url, redirect_count=1, http_status=200,
            headers=httpx.Headers({"content-type": "text/html"}),
            content_type="text/html", declared_content_length=13,
            bytes_received=13, content=b"<h1>Safe</h1>")


@pytest.mark.asyncio
async def test_http_prepares_bounded_fingerprinted_resource():
    result = await NormalHttpRetriever(HttpBoundary()).retrieve(
        url="https://docs.python.org/list", requested_at=NOW, fetched_at=NOW)
    assert result.outcome == "succeeded"
    resource = result.resources[0]
    assert resource.retrieval_method is Phase5WorkKind.HTTP_RETRIEVAL
    assert resource.content_length == 13
    assert resource.redirect_count == 1
    assert len(resource.content_sha256) == len(resource.response_fingerprint) == 64


@pytest.mark.asyncio
async def test_http_unsafe_target_is_sanitized_terminal_failure():
    result = await NormalHttpRetriever(HttpBoundary()).retrieve(
        url="http://127.0.0.1/private", requested_at=NOW, fetched_at=NOW)
    assert result.outcome == "terminal_failure"
    assert result.failure_category is RetrievalFailureCategory.UNSAFE_URL
    assert "127.0.0.1" not in result.model_dump_json()


def test_resource_identity_separates_retrieval_methods():
    common = dict(
        requested_url="https://docs.python.org/list",
        final_url="https://docs.python.org/list", content_type="text/html",
        body=b"same", requested_at=NOW, fetched_at=NOW)
    http = prepare_resource(**common, retrieval_method=Phase5WorkKind.HTTP_RETRIEVAL)
    firecrawl = prepare_resource(
        **common, retrieval_method=Phase5WorkKind.FIRECRAWL_RETRIEVAL)
    assert http.content_sha256 == firecrawl.content_sha256
    assert http.response_fingerprint != firecrawl.response_fingerprint


class FirecrawlDouble:
    async def scrape(self, *, url):
        return {"id": "safe-request", "status": "completed", "resources": [
            {"url": url, "markdown": "# One", "role": "cleaned"},
            {"url": f"{url}?page=2", "html": "<p>Two</p>", "role": "page"},
            {"url": "http://127.0.0.1/private", "html": "unsafe"},
        ]}


@pytest.mark.asyncio
async def test_firecrawl_double_returns_multiple_safe_resources():
    result = await FirecrawlRetriever(
        FirecrawlDouble(), Settings(debug=False)).retrieve(
            url="https://docs.python.org/list", requested_at=NOW, fetched_at=NOW)
    assert result.outcome == "succeeded"
    assert len(result.resources) == 2
    assert {item.result_ordinal for item in result.resources} == {0, 1}
    assert all(item.retrieval_method is Phase5WorkKind.FIRECRAWL_RETRIEVAL
               for item in result.resources)


@pytest.mark.asyncio
async def test_firecrawl_production_fails_closed_without_configuration():
    result = await FirecrawlRetriever(
        settings=Settings(debug=False, firecrawl_api_key=None)).retrieve(
            url="https://docs.python.org/list", requested_at=NOW, fetched_at=NOW)
    assert result.outcome == "blocked"
    assert result.provider_wide_blocker
    assert result.failure_category is RetrievalFailureCategory.MISSING_CONFIGURATION


class BrowserDouble:
    async def perform(self, *, url, action_type, continuation_state):
        return {
            "url": url, "content": "<div class='facility-card'>One</div>",
            "continuation_state": {"page": 2, "has_more": True, "secret": "drop"},
        }


@pytest.mark.asyncio
async def test_playwright_state_is_sanitized_and_repeat_is_detected():
    retriever = PlaywrightRetriever(BrowserDouble())
    first = await retriever.retrieve(
        url="https://docs.python.org/list", action_type=BrowserActionType.LOAD_MORE,
        continuation_state={}, requested_at=NOW, fetched_at=NOW)
    state = first.resources[0].continuation_state
    assert "secret" not in state
    repeated = await retriever.retrieve(
        url="https://docs.python.org/list", action_type=BrowserActionType.LOAD_MORE,
        continuation_state=state, requested_at=NOW, fetched_at=NOW)
    assert repeated.outcome == "terminal_failure"
    assert repeated.failure_category is RetrievalFailureCategory.REDIRECT_LOOP


def test_fallback_policy_is_typed_and_does_not_call_every_tool():
    assert fallback_work_kind("supported_directory") is None
    assert fallback_work_kind("not_a_directory") is None
    assert fallback_work_kind(
        "requires_managed_rendering") is Phase5WorkKind.FIRECRAWL_RETRIEVAL
    assert fallback_work_kind(
        "requires_browser_interaction") is Phase5WorkKind.PLAYWRIGHT_RETRIEVAL


def test_browser_action_identity_is_stable_across_retry_and_separate_by_state():
    common = dict(
        organization_id="o", execution_id="e", crawl_node_id="n",
        original_url="https://docs.python.org/list",
        source_classification="directory",
        work_kind=Phase5WorkKind.PLAYWRIGHT_RETRIEVAL,
        selected_tool="playwright", requested_at=NOW)
    first = prepare_phase5_job(**common, action_state_fingerprint="a" * 64)
    replay = prepare_phase5_job(**common, action_state_fingerprint="a" * 64)
    next_action = prepare_phase5_job(**common, action_state_fingerprint="b" * 64)
    assert first.fingerprint == replay.fingerprint
    assert first.fingerprint != next_action.fingerprint


def test_retrieval_module_has_no_implicit_browser_or_firecrawl_sdk_calls():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "app" / "services" / "scraping" /
              "phase5_retrieval_service.py").read_text()
    assert "import firecrawl" not in source
    assert "playwright_enabled: bool = False" in (
        Path(__file__).parents[1] / "app" / "core" / "config.py").read_text()
