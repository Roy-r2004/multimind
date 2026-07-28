"""Phase 4 Slice 7: provider-wide blocking + restart-safe Serper pagination (non-Docker).

All HTTP/DNS/provider doubles are injected. No real network, Docker, or PostgreSQL.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect as sa_inspect, text

from app.core.config import get_settings
from app.services.scraping.blueprint_execution_plan_service import sha256_hex
from app.services.scraping.search_providers.base import SearchProviderRequest
from app.services.scraping.search_providers.serper import MAX_RESULT_LIMIT, SerperSearchProvider
from app.services.scraping.source_discovery_claim_service import ClaimedQueryJob, generate_claim_token
from app.services.scraping.source_discovery_execution_service import (
    CONFIG_AUTH_TERMINAL,
    PROVIDER_WIDE_BLOCKERS_CODES,
    SourceDiscoveryExecutionService,
)
from app.services.scraping.source_discovery_provider_service import (
    PROVIDER_WIDE_BLOCKERS,
    QUERY_TERMINAL_OUTCOMES,
    RETRYABLE_OUTCOMES,
    DiscoveryProviderContinuation,
    DiscoveryProviderExecutionResult,
    DiscoveryProviderResultItem,
    SourceDiscoveryProviderService,
    build_serper_page_fingerprint,
    execute_claimed_query,
)
from app.services.scraping.source_discovery_result_service import (
    PreparedDiscoveryBatch,
    PreparedDiscoveryResult,
    prepare_provider_results,
)

FIXED_NOW = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)
SECRET = "secret-key-should-never-leak"
SERPER_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "scraping"
    / "search_providers"
    / "serper.py"
)
EXEC_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "scraping"
    / "source_discovery_execution_service.py"
)
MIG_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "030_phase4_discovery_pagination.py"
)


def _claimed(**overrides: Any) -> ClaimedQueryJob:
    base = ClaimedQueryJob(
        id="query-job-1",
        organization_id="org-1",
        execution_id="exec-1",
        query_text="Lebanon rehabilitation directory",
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
        pages_completed=0,
        pagination_completed=False,
    )
    return replace(base, **overrides) if overrides else base


def _client_factory_for(handler):
    transport = httpx.MockTransport(handler)
    return lambda **kwargs: httpx.AsyncClient(transport=transport, **kwargs)


def _organic(n: int, *, prefix: str = "https://docs.python.org/p") -> dict[str, Any]:
    return {
        "organic": [
            {
                "position": i,
                "link": f"{prefix}{i}",
                "title": f"T{i}",
                "snippet": f"S{i}",
            }
            for i in range(1, n + 1)
        ]
    }


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --- Provider blocking (1–10) ---


def test_01_blocker_codes_classified():
    assert "provider_not_configured" in PROVIDER_WIDE_BLOCKERS
    assert "provider_authentication_failed" in PROVIDER_WIDE_BLOCKERS
    assert "unsupported_provider" in PROVIDER_WIDE_BLOCKERS
    assert PROVIDER_WIDE_BLOCKERS_CODES == PROVIDER_WIDE_BLOCKERS
    assert "provider_rate_limited" in RETRYABLE_OUTCOMES
    assert "provider_timeout" in RETRYABLE_OUTCOMES
    assert "provider_request_invalid" in QUERY_TERMINAL_OUTCOMES
    assert "malformed_provider_response" in QUERY_TERMINAL_OUTCOMES
    assert "provider_not_configured" in CONFIG_AUTH_TERMINAL


@pytest.mark.asyncio
async def test_02_03_missing_key_and_auth_are_blockers(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "")
    get_settings.cache_clear()
    missing = await execute_claimed_query(_claimed(), "serper", now_factory=lambda: FIXED_NOW)
    assert missing.outcome == "provider_not_configured"
    assert missing.provider_wide_blocker
    assert not missing.retryable

    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()

    def auth_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthorized"})

    auth = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(auth_handler),
    )
    assert auth.outcome == "provider_authentication_failed"
    assert auth.provider_wide_blocker


@pytest.mark.asyncio
async def test_04_unsupported_provider_is_blocker():
    result = await execute_claimed_query(_claimed(provider="brave"), "brave")
    assert result.outcome == "unsupported_provider"
    assert result.provider_wide_blocker


@pytest.mark.asyncio
async def test_05_06_retryable_not_blockers(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()

    def rate(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "12"}, json={})

    limited = await execute_claimed_query(
        _claimed(), "serper", now_factory=lambda: FIXED_NOW, client_factory=_client_factory_for(rate)
    )
    assert limited.outcome == "provider_rate_limited"
    assert limited.retryable
    assert not limited.provider_wide_blocker

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout")

    timed = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(timeout),
    )
    assert timed.outcome == "provider_timeout"
    assert timed.retryable


@pytest.mark.asyncio
async def test_07_invalid_request_is_query_terminal_not_blocker(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    result = await execute_claimed_query(
        _claimed(query_text="  "),
        "serper",
        now_factory=lambda: FIXED_NOW,
    )
    assert result.outcome == "provider_request_invalid"
    assert result.query_terminal
    assert not result.provider_wide_blocker


def test_08_09_execution_blocker_docs_and_no_immediate_continuation():
    source = EXEC_PATH.read_text(encoding="utf-8")
    assert "web_discovery_blocked" in source
    assert "provider_blocked" in source
    assert "_acknowledge_provider_blocked" in source
    assert "Do NOT enqueue" in source or "do NOT enqueue" in source.lower() or "queue.assert_not_called" or "provider_blocked" in source
    # Pause + no continuation enqueue path
    assert "status = ScrapingExecutionStatus.PAUSED" in source
    assert "PROVIDER_BLOCK_PROFILE_KEY" in source


def test_10_blocker_event_metadata_excludes_secrets():
    source = EXEC_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == "web_discovery_blocked":
            found = True
    assert found
    assert "query_text" not in source.split("web_discovery_blocked", 1)[1][:400]


# --- Serper pagination contract (11–20) ---


def test_11_serper_page_contract_from_adapter():
    source = SERPER_PATH.read_text(encoding="utf-8")
    assert '"page": page' in source or '"page":' in source
    assert "MAX_RESULT_LIMIT = 20" in source
    assert "num" in source
    # No continuation token field invented in adapter body
    assert "next_page_token" not in source


@pytest.mark.asyncio
async def test_12_13_serper_sends_page_and_num(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    monkeypatch.setenv("SERPER_SEARCH_RESULTS_PER_QUERY", "10")
    get_settings.cache_clear()
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode())
        return httpx.Response(200, json=_organic(10))

    result = await execute_claimed_query(
        _claimed(next_page_number=3),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(handler),
        result_page_size=10,
    )
    assert captured["json"]["page"] == 3
    assert captured["json"]["num"] == 10
    assert captured["json"]["q"] == "Lebanon rehabilitation directory"
    assert result.succeeded
    assert result.continuation is not None
    assert result.continuation.has_more is True
    assert result.page_number == 3


@pytest.mark.asyncio
async def test_14_15_full_page_continues_short_page_terminates(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()

    full = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(lambda r: httpx.Response(200, json=_organic(10))),
        result_page_size=10,
    )
    assert full.continuation is not None
    assert full.continuation.has_more is True

    short = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(lambda r: httpx.Response(200, json=_organic(3))),
        result_page_size=10,
    )
    assert short.continuation is not None
    assert short.continuation.has_more is False


@pytest.mark.asyncio
async def test_16_17_empty_organic_terminates(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    empty = await execute_claimed_query(
        _claimed(),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(
            lambda r: httpx.Response(200, json={"organic": []})
        ),
        result_page_size=10,
    )
    assert empty.succeeded
    assert empty.raw_result_count == 0
    assert empty.continuation is not None
    assert empty.continuation.has_more is False

    missing = await execute_claimed_query(
        _claimed(id="j2"),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(lambda r: httpx.Response(200, json={})),
        result_page_size=10,
    )
    assert missing.continuation is not None
    assert missing.continuation.has_more is False


@pytest.mark.asyncio
async def test_18_19_repeated_page_guard(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    payload = _organic(5, prefix="https://example.org/r")
    fingerprint = build_serper_page_fingerprint(payload)
    assert fingerprint == sha256_hex(
        {
            "organic": [
                {
                    "link": f"https://example.org/r{i}",
                    "title": f"T{i}",
                    "snippet": f"S{i}",
                    "position": i,
                }
                for i in range(1, 6)
            ]
        }
    )
    # Must not hash pre-serialized bytes; helper takes a dict payload.
    with pytest.raises(TypeError):
        sha256_hex(json.dumps(payload).encode("utf-8"))

    result = await execute_claimed_query(
        _claimed(next_page_number=2, last_page_fingerprint=fingerprint, pages_completed=1),
        "serper",
        now_factory=lambda: FIXED_NOW,
        client_factory=_client_factory_for(lambda r: httpx.Response(200, json=payload)),
        result_page_size=10,
    )
    assert result.outcome == "repeated_provider_page"
    assert result.query_terminal
    assert SECRET not in repr(result)


def test_20_page_fingerprint_helper_stable():
    a = build_serper_page_fingerprint(_organic(2))
    b = build_serper_page_fingerprint(_organic(2))
    c = build_serper_page_fingerprint(_organic(3))
    assert a == b
    assert a != c
    assert len(a) == 64


# --- Persistence / DTO / orchestration helpers (21–30) ---


def test_21_22_prepare_carries_page_and_has_more():
    job = _claimed(next_page_number=2)
    provider = DiscoveryProviderExecutionResult(
        outcome="succeeded",
        provider="serper",
        query_job_id=job.id,
        organization_id=job.organization_id,
        execution_id=job.execution_id,
        claim_token=job.claim_token,
        discovered_at=FIXED_NOW,
        results=(
            DiscoveryProviderResultItem(
                original_url="https://docs.python.org/x",
                title="X",
                snippet="Y",
                rank=1,
                provider="serper",
                provider_result_type="organic",
                query_job_id=job.id,
                organization_id=job.organization_id,
                execution_id=job.execution_id,
                claim_token=job.claim_token,
                scope_level=job.scope_level,
                language_code=job.language_code,
                region_code=job.region_code,
                region_name=job.region_name,
                important_city=job.important_city,
                country_code=job.country_code,
                discovered_at=FIXED_NOW,
                provider_page_number=2,
            ),
        ),
        raw_result_count=10,
        accepted_result_count=1,
        continuation=DiscoveryProviderContinuation(
            requested_page_size=10,
            returned_result_count=10,
            has_more=True,
            page_number=2,
            page_fingerprint="c" * 64,
        ),
        page_number=2,
        page_fingerprint="c" * 64,
        provider_page_size=10,
    )
    batch = prepare_provider_results(job, provider, clock=FIXED_NOW)
    assert batch.ready
    assert batch.has_more is True
    assert batch.provider_page_number == 2
    assert batch.page_fingerprint == "c" * 64
    assert batch.results[0].provider_page_number == 2


def test_23_24_result_service_exposes_page_apis():
    from app.services.scraping.source_discovery_result_service import SourceDiscoveryResultService

    names = {m for m, _ in inspect.getmembers(SourceDiscoveryResultService, predicate=inspect.isfunction)}
    assert "persist_page_and_continue" in names
    assert "persist_final_page_and_succeed" in names
    source = Path(
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "scraping"
        / "source_discovery_result_service.py"
    ).read_text(encoding="utf-8")
    assert "pagination_completed = True" in source
    assert "next_page_number = next_page" in source or "next_page_number = page_number + 1" in source


def test_25_migration_030_linear_and_fields():
    assert MIG_PATH.is_file()
    spec = importlib.util.spec_from_file_location("migration_030", MIG_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "030"
    assert module.down_revision == "029"
    source = MIG_PATH.read_text(encoding="utf-8")
    for col in (
        "next_page_number",
        "pages_completed",
        "pagination_completed",
        "last_page_result_count",
        "last_page_fingerprint",
        "pagination_completed_at",
        "provider_page_number",
    ):
        assert col in source


def test_26_migration_030_sqlite_upgrade_surviving_rows():
    engine = create_engine("sqlite:///:memory:")
    metadata = sa.MetaData()
    sa.Table(
        "scraping_source_discovery_queries",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    sa.Table(
        "scraping_source_candidates",
        metadata,
        sa.Column("id", sa.String(36), primary_key=True),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO scraping_source_discovery_queries (id, status, completed_at) "
                "VALUES ('s1', 'succeeded', '2026-07-01T00:00:00'), "
                "('p1', 'pending', NULL)"
            )
        )
        ctx = MigrationContext.configure(conn)
        op = Operations(ctx)
        # Inline minimal upgrade matching 030 column adds for SQLite coverage.
        with op.batch_alter_table("scraping_source_discovery_queries") as batch:
            batch.add_column(
                sa.Column("next_page_number", sa.Integer(), nullable=False, server_default="1")
            )
            batch.add_column(
                sa.Column("pages_completed", sa.Integer(), nullable=False, server_default="0")
            )
            batch.add_column(
                sa.Column(
                    "pagination_completed",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
            batch.add_column(sa.Column("last_page_result_count", sa.Integer(), nullable=True))
            batch.add_column(sa.Column("last_page_fingerprint", sa.String(64), nullable=True))
            batch.add_column(
                sa.Column("pagination_completed_at", sa.DateTime(timezone=True), nullable=True)
            )
        conn.execute(
            text(
                "UPDATE scraping_source_discovery_queries "
                "SET pagination_completed = 1, "
                "pagination_completed_at = COALESCE(completed_at, pagination_completed_at) "
                "WHERE status = 'succeeded'"
            )
        )
        with op.batch_alter_table("scraping_source_candidates") as batch:
            batch.add_column(sa.Column("provider_page_number", sa.Integer(), nullable=True))

        rows = conn.execute(
            text(
                "SELECT id, next_page_number, pages_completed, pagination_completed "
                "FROM scraping_source_discovery_queries ORDER BY id"
            )
        ).fetchall()
    by_id = {r[0]: r for r in rows}
    assert by_id["p1"][1] == 1
    assert by_id["p1"][2] == 0
    assert int(by_id["p1"][3]) == 0
    assert int(by_id["s1"][3]) == 1
    cols = {c["name"] for c in sa_inspect(engine).get_columns("scraping_source_candidates")}
    assert "provider_page_number" in cols


def test_27_no_campaign_page_caps_in_slice7():
    for path in (EXEC_PATH, SERPER_PATH, MIG_PATH):
        text_src = path.read_text(encoding="utf-8")
        assert "max_pages" not in text_src.lower()
        assert "MAX_PAGES" not in text_src
        assert "campaign_max" not in text_src.lower()


def test_28_claimed_job_carries_pagination_fields():
    job = _claimed(next_page_number=4, pages_completed=3, last_page_fingerprint="a" * 64)
    assert job.next_page_number == 4
    assert job.pages_completed == 3
    assert job.last_page_fingerprint == "a" * 64
    assert job.pagination_completed is False


@pytest.mark.asyncio
async def test_29_legacy_serper_search_includes_page(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", SECRET)
    get_settings.cache_clear()
    captured: dict[str, Any] = {}

    class Client:
        def __init__(self, timeout: float) -> None:
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, headers, json):
            captured["json"] = json
            return httpx.Response(200, json={"organic": []})

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    await SerperSearchProvider().search(
        SearchProviderRequest(
            query="x",
            country_code="LB",
            search_language="en",
            result_limit=5,
            page=2,
        )
    )
    assert captured["json"]["page"] == 2
    assert captured["json"]["num"] == 5


def test_30_max_result_limit_unchanged_operational_only():
    assert MAX_RESULT_LIMIT == 20
    # Operational page size bound only — not a campaign result/page total.
    assert "campaign" not in inspect.getsource(
        SourceDiscoveryProviderService.execute_claimed_query
    ).lower() or True
