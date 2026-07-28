"""Phase 4 Slice 4: non-Docker tests for real Serper provider execution boundary."""

from __future__ import annotations

import ast
import inspect
import json
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.services.scraping.search_providers import (
    APPROVED_V2_DISCOVERY_PROVIDERS,
    SerperSearchProvider,
    create_search_provider,
    resolve_v2_discovery_provider,
)
from app.services.scraping.search_providers.base import (
    SearchProviderConfigurationError,
    SearchProviderRequest,
)
from app.services.scraping.source_discovery_claim_service import ClaimedQueryJob, generate_claim_token
from app.services.scraping.source_discovery_provider_service import (
    DiscoveryProviderExecutionResult,
    SourceDiscoveryProviderService,
    execute_claimed_query,
)

SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "scraping"
    / "source_discovery_provider_service.py"
)
SERPER_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "scraping"
    / "search_providers"
    / "serper.py"
)
FIXED_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)
SECRET = "secret-key-should-never-leak"


def _claimed(**overrides: Any) -> ClaimedQueryJob:
    base = ClaimedQueryJob(
        id="query-job-1",
        organization_id="org-1",
        execution_id="exec-1",
        query_text="Lebanon rehabilitation directory Beirut",
        provider="serper",
        claim_token=generate_claim_token(),
        claimed_at=FIXED_NOW,
        lease_expires_at=FIXED_NOW + timedelta(seconds=60),
        attempt_count=1,
        last_attempt_at=FIXED_NOW,
        priority=100,
        generation_ordinal=3,
        discovery_round=1,
        purpose="seed",
        country_code="LB",
        country_name="Lebanon",
        region_code="BEY",
        region_name="Beirut",
        language_code="en",
        language_name="English",
        source_category="directory",
        scope_level="region",
        important_city="Beirut",
        query_job_fingerprint="f" * 64,
        plan_hash_snapshot="p" * 64,
        requested_at=FIXED_NOW,
    )
    return replace(base, **overrides) if overrides else base


def _client_factory_for(handler):
    transport = httpx.MockTransport(handler)
    return lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)


def _organic_ok() -> dict[str, Any]:
    return {
        "organic": [
            {
                "position": 2,
                "link": "https://example.org/rehab",
                "title": "Rehab Registry",
                "snippet": "Official listing",
            }
        ]
    }


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_01_configured_serper_provider_resolves(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    monkeypatch.delenv("SOURCE_DISCOVERY_PROVIDER", raising=False)
    get_settings.cache_clear()
    provider = resolve_v2_discovery_provider("serper")
    assert isinstance(provider, SerperSearchProvider)
    assert provider.name == "serper"
    assert "serper" in APPROVED_V2_DISCOVERY_PROVIDERS
    assert create_search_provider("serper").name == "serper"


@pytest.mark.asyncio
async def test_02_missing_serper_configuration_returns_not_configured(monkeypatch):
    # Empty override beats a developer .env value; delenv alone is not enough.
    monkeypatch.setenv("SERPER_API_KEY", "")
    get_settings.cache_clear()
    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(lambda r: httpx.Response(200, json=_organic_ok())),
    )
    assert result.outcome == "provider_not_configured"
    assert result.terminal
    assert result.results == ()


def test_03_unsupported_provider_rejected():
    with pytest.raises(SearchProviderConfigurationError):
        resolve_v2_discovery_provider("brave")
    with pytest.raises(SearchProviderConfigurationError):
        resolve_v2_discovery_provider("fake")
    with pytest.raises(SearchProviderConfigurationError):
        resolve_v2_discovery_provider("perplexity")


@pytest.mark.asyncio
async def test_04_no_fallback_to_fake_or_brave(monkeypatch):
    monkeypatch.setenv("SOURCE_DISCOVERY_PROVIDER", "brave")
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        assert "brave" not in str(request.url).lower()
        return httpx.Response(200, json=_organic_ok())

    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    assert result.outcome == "succeeded"
    assert result.provider == "serper"
    assert "google.serper.dev" in captured["url"]

    blocked = await execute_claimed_query(
        _claimed(provider="brave"), "brave", now_factory=lambda: FIXED_NOW
    )
    assert blocked.outcome == "unsupported_provider"


@pytest.mark.asyncio
async def test_05_claimed_provider_must_match_executor(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    result = await execute_claimed_query(
        _claimed(provider="serper"), "brave", now_factory=lambda: FIXED_NOW
    )
    assert result.outcome == "unsupported_provider"


@pytest.mark.asyncio
async def test_06_empty_query_rejected_safely(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    result = await execute_claimed_query(
        _claimed(query_text="   "),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(
            lambda r: (_ for _ in ()).throw(AssertionError("no http"))
        ),
    )
    assert result.outcome == "provider_request_invalid"
    assert SECRET not in str(result)


@pytest.mark.asyncio
async def test_07_missing_claim_token_rejected_safely(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    result = await execute_claimed_query(
        _claimed(claim_token=""), "serper", now_factory=lambda: FIXED_NOW
    )
    assert result.outcome == "provider_request_invalid"


@pytest.mark.asyncio
async def test_08_to_16_successful_serper_immutable_dtos_and_provenance(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    monkeypatch.setenv("SERPER_SEARCH_RESULTS_PER_QUERY", "7")
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode())
        assert payload["q"] == "Lebanon rehabilitation directory Beirut"
        assert payload["gl"] == "lb"
        assert payload["hl"] == "en"
        assert payload["num"] == 7
        assert payload["page"] == 1
        assert request.headers.get("x-api-key") == SECRET
        return httpx.Response(200, json=_organic_ok())

    job = _claimed()
    result = await execute_claimed_query(
        job,
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    assert result.outcome == "succeeded"
    assert result.succeeded
    assert is_dataclass(result)
    assert len(result.results) == 1
    item = result.results[0]
    assert item.original_url == "https://example.org/rehab"
    assert item.title == "Rehab Registry"
    assert item.snippet == "Official listing"
    assert item.rank == 2
    assert item.provider == "serper"
    assert item.provider_result_type == "organic"
    assert item.query_job_id == job.id
    assert item.organization_id == job.organization_id
    assert item.execution_id == job.execution_id
    assert item.claim_token == job.claim_token
    assert item.scope_level == "region"
    assert item.language_code == "en"
    assert item.region_code == "BEY"
    assert item.region_name == "Beirut"
    assert item.important_city == "Beirut"
    assert item.discovered_at == FIXED_NOW
    assert result.discovered_at == FIXED_NOW
    assert SECRET not in repr(result)


@pytest.mark.asyncio
async def test_17_18_missing_title_snippet_blank(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"organic": [{"position": 1, "link": "https://example.org/only-url"}]}
        )

    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    assert result.outcome == "succeeded"
    assert result.results[0].title == ""
    assert result.results[0].snippet == ""
    assert result.results[0].original_url == "https://example.org/only-url"


@pytest.mark.asyncio
async def test_19_20_21_malformed_items_skipped_with_counts(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "organic": [
                    "not-a-dict",
                    {"position": 1, "link": None, "title": "x"},
                    {"position": 2, "link": 123, "title": "y"},
                    {
                        "position": 9,
                        "link": "https://example.org/good",
                        "title": "Good",
                        "snippet": "Ok",
                    },
                ]
            },
        )

    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    assert result.outcome == "succeeded"
    assert result.raw_result_count == 4
    assert result.accepted_result_count == 1
    assert result.skipped_malformed_count == 3
    assert result.results[0].original_url == "https://example.org/good"
    assert result.results[0].rank == 9


@pytest.mark.asyncio
async def test_22_empty_organic_is_success(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(lambda r: httpx.Response(200, json={"organic": []})),
    )
    assert result.outcome == "succeeded"
    assert result.results == ()
    assert result.raw_result_count == 0


@pytest.mark.asyncio
async def test_23_24_rate_limited_with_retry_after(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, headers={"Retry-After": "12"}, json={"message": f"throttle {SECRET}"}
        )

    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    assert result.outcome == "provider_rate_limited"
    assert result.retryable
    assert result.retry_after_seconds == 12.0
    assert result.retry_after_at == FIXED_NOW + timedelta(seconds=12)
    assert result.http_status == 429
    assert SECRET not in str(result)
    assert "throttle" not in str(result)


@pytest.mark.asyncio
async def test_25_timeout_maps(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException(f"slow {SECRET}")

    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    assert result.outcome == "provider_timeout"
    assert SECRET not in str(result)


@pytest.mark.asyncio
async def test_26_network_failure_maps(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"dns-or-connect {SECRET}")

    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    assert result.outcome == "provider_network_error"
    assert SECRET not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503])
async def test_27_server_errors_retryable(monkeypatch, status):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status, json={"error": SECRET})

    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    assert result.outcome == "provider_server_error"
    assert result.retryable
    assert result.http_status == status
    assert calls["n"] == 2
    assert SECRET not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_28_auth_failures(monkeypatch, status):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(status, json={"error": SECRET})

    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    assert result.outcome == "provider_authentication_failed"
    assert result.terminal
    assert calls["n"] == 1
    assert SECRET not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 422])
async def test_29_invalid_request(monkeypatch, status):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": f"bad {SECRET}"})

    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    assert result.outcome == "provider_request_invalid"
    assert SECRET not in str(result)


@pytest.mark.asyncio
async def test_30_invalid_json_malformed(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json", headers={"content-type": "text/plain"})

    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    assert result.outcome == "malformed_provider_response"


@pytest.mark.asyncio
async def test_31_provider_error_payload_maps_safely(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"organic": {"not": "a-list", "secret": SECRET}})

    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    assert result.outcome == "malformed_provider_response"
    assert SECRET not in repr(result)


@pytest.mark.asyncio
async def test_32_33_34_no_body_key_or_raw_exception(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    query = "super-secret-query-text-xyz"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"body": SECRET, "query": query})

    result = await execute_claimed_query(
        _claimed(query_text=query),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    blob = repr(result) + str(result)
    assert SECRET not in blob
    assert result.diagnostic_code == "provider_authentication_failed"


def test_35_no_database_session_accepted_or_opened():
    sig = inspect.signature(SourceDiscoveryProviderService.execute_claimed_query)
    assert "db" not in sig.parameters
    assert "session" not in sig.parameters
    source = SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            if "sqlalchemy" in node.module or node.module.endswith("session"):
                imported.append(node.module)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "sqlalchemy" in alias.name:
                    imported.append(alias.name)
    assert imported == []
    assert "AsyncSession" not in source
    assert "AsyncSessionLocal" not in source


def test_36_37_38_39_40_no_lifecycle_canonicalize_classify_planner_or_caps():
    source = SERVICE_PATH.read_text(encoding="utf-8")
    forbidden = [
        "mark_succeeded",
        "mark_failed",
        "heartbeat_claim",
        "canonicalize",
        "classify_source",
        "SourceDiscoveryQueryPlanner",
        "plan_queries",
        "mission_campaign_mock_worker",
        "max_queries_per_discovery",
        "serper_search_max_queries",
        "campaign_result_limit",
        "fixed_attempt",
        "STAGES",
    ]
    for token in forbidden:
        assert token not in source, token


@pytest.mark.asyncio
async def test_41_42_no_real_http_or_dns_in_tests(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(lambda r: httpx.Response(200, json={"organic": []})),
    )
    assert result.outcome == "succeeded"


@pytest.mark.asyncio
async def test_43_44_no_fabrication_and_immutable_dtos(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(
            lambda r: httpx.Response(
                200,
                json={
                    "organic": [
                        {
                            "link": "https://example.org/a",
                            "title": "T",
                            "snippet": "S",
                            "position": 1,
                        }
                    ]
                },
            )
        ),
    )
    assert is_dataclass(result)
    assert is_dataclass(result.results[0])
    with pytest.raises(Exception):
        result.outcome = "provider_timeout"  # type: ignore[misc]
    with pytest.raises(Exception):
        result.results[0].original_url = "https://fabricated.invalid"  # type: ignore[misc]
    assert result.results[0].title == "T"


def test_45_no_fixed_attempt_retry_ceiling_in_executor():
    source = SERVICE_PATH.read_text(encoding="utf-8")
    assert "stop_after_attempt" not in source
    assert "max_attempts" not in source
    assert "attempt_ceiling" not in source
    serper_src = SERPER_PATH.read_text(encoding="utf-8")
    assert "stop_after_attempt(2)" in serper_src


@pytest.mark.asyncio
async def test_url_preserved_exactly_without_canonicalization(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    raw = "HTTPS://Example.ORG:443/path?utm_source=x&id=1#frag"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"organic": [{"link": raw, "title": "x", "snippet": "y"}]}
        )

    result = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
    )
    assert result.results[0].original_url == raw


@pytest.mark.asyncio
async def test_injected_adapter_double_works_without_network():
    class Double:
        name = "serper"

        async def fetch_payload(self, request: SearchProviderRequest) -> dict[str, Any]:
            assert request.query == "Lebanon rehabilitation directory Beirut"
            return {
                "organic": [
                    {
                        "link": "https://double.example/a",
                        "title": "D",
                        "snippet": "S",
                        "position": 4,
                    }
                ]
            }

    result = await execute_claimed_query(
        _claimed(),
        "serper",
        provider=Double(),  # type: ignore[arg-type]
        now_factory=lambda: FIXED_NOW,
    )
    assert result.outcome == "succeeded"
    assert result.results[0].rank == 4


def test_execute_signature_has_no_session_param():
    sig = inspect.signature(execute_claimed_query)
    for name in sig.parameters:
        assert name not in {"db", "session", "async_session"}


def test_service_does_not_import_claim_mutations():
    source = SERVICE_PATH.read_text(encoding="utf-8")
    assert "SourceDiscoveryClaimService" not in source
    assert "discovery_url_service" not in source


def test_result_dto_fields_are_safe():
    names = {f.name for f in fields(DiscoveryProviderExecutionResult)}
    forbidden = {"api_key", "headers", "raw_body", "exception", "stack", "prompt", "sql"}
    assert names.isdisjoint(forbidden)
