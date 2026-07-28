"""Phase 4 Slice 3: non-Docker unit tests for claim lifecycle helpers/DTOs.

PostgreSQL claim concurrency is covered by test_phase4_discovery_claims_postgres.py
(Docker-only; not run here).
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import fields, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.db.models import ScrapingExecution, ScrapingExecutionStatus
from app.services.scraping import source_discovery_claim_service as claim_mod
from app.services.scraping.source_discovery_claim_service import (
    DEFAULT_MAX_CLAIM_BATCH_SIZE,
    FALLBACK_ERROR_CODE,
    LAST_ERROR_CODE_MAX_LENGTH,
    ClaimedQueryJob,
    SourceDiscoveryClaimService,
    cancel_supersedes_pause,
    evaluate_claim_lifecycle,
    exponential_backoff_policy,
    fixed_backoff_policy,
    generate_claim_token,
    immediate_retry_policy,
    normalize_lifecycle_error_code,
    require_lifecycle_error_code,
    validate_lease_duration,
    validate_positive_batch_size,
    validate_provider_id,
    validate_retry_delay,
)

SERVICE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "services"
    / "scraping"
    / "source_discovery_claim_service.py"
)


def test_01_claim_batch_size_validation():
    assert validate_positive_batch_size(1) == 1
    assert validate_positive_batch_size(DEFAULT_MAX_CLAIM_BATCH_SIZE) == DEFAULT_MAX_CLAIM_BATCH_SIZE
    with pytest.raises(ValueError, match=">= 1"):
        validate_positive_batch_size(0)
    with pytest.raises(ValueError, match=">="):
        validate_positive_batch_size(-3)
    with pytest.raises(ValueError, match="<="):
        validate_positive_batch_size(DEFAULT_MAX_CLAIM_BATCH_SIZE + 1)
    with pytest.raises(ValueError, match="positive integer"):
        validate_positive_batch_size(True)  # type: ignore[arg-type]


def test_02_lease_duration_validation():
    assert validate_lease_duration(timedelta(seconds=1)) == timedelta(seconds=1)
    with pytest.raises(ValueError, match="positive"):
        validate_lease_duration(timedelta(0))
    with pytest.raises(ValueError, match="positive"):
        validate_lease_duration(timedelta(seconds=-1))
    with pytest.raises(ValueError, match="timedelta"):
        validate_lease_duration(30)  # type: ignore[arg-type]


def test_03_retry_time_validation():
    assert validate_retry_delay(timedelta(0)) == timedelta(0)
    assert validate_retry_delay(timedelta(seconds=5)) == timedelta(seconds=5)
    with pytest.raises(ValueError, match="non-negative"):
        validate_retry_delay(timedelta(seconds=-1))
    now = datetime(2026, 1, 1, tzinfo=UTC)
    policy = fixed_backoff_policy(timedelta(seconds=45))
    assert policy(now, 1) == now + timedelta(seconds=45)
    assert policy(now, 99) == now + timedelta(seconds=45)


def test_04_error_code_normalization():
    assert normalize_lifecycle_error_code("provider_rate_limited") == "provider_rate_limited"
    assert normalize_lifecycle_error_code("Provider_Timeout") == "provider_timeout"
    assert normalize_lifecycle_error_code("  lease-expired!! ") == "lease_expired"
    assert len(normalize_lifecycle_error_code("a" * 200)) == LAST_ERROR_CODE_MAX_LENGTH
    assert require_lifecycle_error_code("provider_unavailable") == "provider_unavailable"
    assert require_lifecycle_error_code("malformed_provider_response") == (
        "malformed_provider_response"
    )
    assert require_lifecycle_error_code("cancelled_before_request") == "cancelled_before_request"
    assert require_lifecycle_error_code("unsafe_result_url") == "unsafe_result_url"


def test_05_raw_error_strings_rejected_or_sanitized():
    assert normalize_lifecycle_error_code("ValueError: boom\nSELECT * FROM secrets") == (
        "valueerror_boom_select_from_secrets"
    )
    # Charset-invalid / exception-like inputs are rejected by the strict path.
    with pytest.raises(ValueError):
        require_lifecycle_error_code("ValueError: secret key=sk-live")
    with pytest.raises(ValueError):
        require_lifecycle_error_code("https://api.example/key=abc")
    with pytest.raises(ValueError):
        require_lifecycle_error_code("PROVIDER_TIMEOUT")
    with pytest.raises(ValueError):
        require_lifecycle_error_code("")
    assert normalize_lifecycle_error_code(None) == FALLBACK_ERROR_CODE
    assert normalize_lifecycle_error_code("!!!") == FALLBACK_ERROR_CODE


def test_06_fresh_claim_token_generation():
    tokens = {generate_claim_token() for _ in range(20)}
    assert len(tokens) == 20
    for token in tokens:
        assert len(token) == 36
        assert token.count("-") == 4


def test_07_claimed_dto_contains_only_required_safe_fields():
    assert is_dataclass(ClaimedQueryJob)
    names = {f.name for f in fields(ClaimedQueryJob)}
    required = {
        "id",
        "organization_id",
        "execution_id",
        "query_text",
        "provider",
        "claim_token",
        "claimed_at",
        "lease_expires_at",
        "attempt_count",
        "last_attempt_at",
        "priority",
        "generation_ordinal",
        "discovery_round",
        "query_job_fingerprint",
        "plan_hash_snapshot",
        "requested_at",
    }
    assert required <= names
    forbidden = {
        "error_message",
        "metadata_json",
        "session",
        "orm",
        "password",
        "api_key",
        "stack_trace",
    }
    assert names.isdisjoint(forbidden)
    job = ClaimedQueryJob(
        id="q1",
        organization_id="o1",
        execution_id="e1",
        query_text="rehab Beirut",
        provider="serper",
        claim_token=generate_claim_token(),
        claimed_at=datetime(2026, 1, 1, tzinfo=UTC),
        lease_expires_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        attempt_count=1,
        last_attempt_at=datetime(2026, 1, 1, tzinfo=UTC),
        priority=100,
        generation_ordinal=0,
        discovery_round=1,
        purpose="seed",
        country_code="LB",
        country_name="Lebanon",
        region_code=None,
        region_name="Beirut",
        language_code="en",
        language_name="English",
        source_category="directory",
        scope_level="region",
        important_city=None,
        query_job_fingerprint="a" * 64,
        plan_hash_snapshot="b" * 64,
        requested_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(Exception):
        job.attempt_count = 99  # type: ignore[misc]


def test_08_no_provider_call_in_service_module():
    source = SERVICE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
                for alias in node.names:
                    imported.add(alias.name)
    banned = {
        "serper",
        "brave",
        "httpx",
        "aiohttp",
        "requests",
        "urllib",
        "SearchProvider",
        "create_search_provider",
        "SourceDiscoveryService",
        "SourceDiscoveryQueryPlanner",
    }
    assert imported.isdisjoint(banned)
    # No network-ish call names in the module body.
    lowered = source.lower()
    for needle in ("httpx.", "aiohttp.", "requests.", "urllib.request", "socket."):
        assert needle not in lowered


def test_09_no_campaign_wide_maximum_attempt_rule():
    source = SERVICE_PATH.read_text(encoding="utf-8")
    for banned in (
        "max_attempts",
        "MAX_ATTEMPTS",
        "attempt_ceiling",
        "max_campaign_attempts",
        "campaign_query_limit",
        "max_provider_calls",
    ):
        assert banned not in source
    # Service methods must not compare attempt_count against a fixed terminal threshold.
    assert "attempt_count >=" not in source
    assert "attempt_count >" not in source


def test_10_retry_policy_is_deterministic_and_injectable():
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    fixed = fixed_backoff_policy(timedelta(seconds=10))
    assert fixed(now, 1) == fixed(now, 1) == now + timedelta(seconds=10)
    expo = exponential_backoff_policy(base=timedelta(seconds=2), factor=2.0)
    assert expo(now, 1) == now + timedelta(seconds=2)
    assert expo(now, 2) == now + timedelta(seconds=4)
    assert expo(now, 3) == now + timedelta(seconds=8)
    assert immediate_retry_policy()(now, 5) == now
    capped = exponential_backoff_policy(
        base=timedelta(seconds=10), factor=10.0, max_delay=timedelta(seconds=15)
    )
    assert capped(now, 5) == now + timedelta(seconds=15)

    service = SourceDiscoveryClaimService(
        session_factory=MagicMock(),
        default_retry_policy=fixed_backoff_policy(timedelta(seconds=7)),
    )
    assert service._default_retry_policy(now, 3) == now + timedelta(seconds=7)


def test_cancel_supersedes_pause_matches_worker_semantics():
    execution = MagicMock(spec=ScrapingExecution)
    execution.status = ScrapingExecutionStatus.PAUSE_REQUESTED
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    later = datetime(2026, 1, 2, tzinfo=UTC)

    execution.cancel_requested_at = None
    execution.pause_requested_at = earlier
    assert cancel_supersedes_pause(execution) is False

    execution.cancel_requested_at = later
    execution.pause_requested_at = earlier
    assert cancel_supersedes_pause(execution) is True

    execution.cancel_requested_at = earlier
    execution.pause_requested_at = later
    assert cancel_supersedes_pause(execution) is False

    execution.status = ScrapingExecutionStatus.CANCEL_REQUESTED
    execution.cancel_requested_at = None
    execution.pause_requested_at = earlier
    assert cancel_supersedes_pause(execution) is True


def test_evaluate_claim_lifecycle_blocks_terminal_and_paused():
    execution = MagicMock(spec=ScrapingExecution)
    execution.execution_type = "mission_campaign"
    execution.execution_plan_schema_version = "2"
    execution.cancel_requested_at = None
    execution.pause_requested_at = None

    execution.status = ScrapingExecutionStatus.RUNNING
    assert evaluate_claim_lifecycle(execution) is None

    execution.status = ScrapingExecutionStatus.PAUSED
    assert evaluate_claim_lifecycle(execution) == "paused"

    execution.status = ScrapingExecutionStatus.PAUSE_REQUESTED
    assert evaluate_claim_lifecycle(execution) == "paused"

    execution.cancel_requested_at = datetime(2026, 1, 2, tzinfo=UTC)
    execution.pause_requested_at = datetime(2026, 1, 1, tzinfo=UTC)
    assert evaluate_claim_lifecycle(execution) == "cancelled"

    execution.status = ScrapingExecutionStatus.COMPLETED
    assert evaluate_claim_lifecycle(execution) == "completed"
    execution.status = ScrapingExecutionStatus.FAILED
    assert evaluate_claim_lifecycle(execution) == "failed"
    execution.status = ScrapingExecutionStatus.CANCELLED
    assert evaluate_claim_lifecycle(execution) == "cancelled"

    execution.status = ScrapingExecutionStatus.RUNNING
    execution.execution_plan_schema_version = "1"
    assert evaluate_claim_lifecycle(execution) == "not_eligible"

    # Resumed campaigns may retain historical pause_requested_at audit timestamps.
    execution.execution_plan_schema_version = "2"
    execution.pause_requested_at = datetime(2026, 1, 1, tzinfo=UTC)
    execution.cancel_requested_at = None
    assert evaluate_claim_lifecycle(execution) is None


def test_provider_id_validation():
    assert validate_provider_id("Serper") == "serper"
    assert validate_provider_id("brave") == "brave"
    with pytest.raises(ValueError):
        validate_provider_id("")
    with pytest.raises(ValueError):
        validate_provider_id("serper/http")


def test_public_api_surface_is_documented():
    svc = SourceDiscoveryClaimService
    for name in (
        "claim_eligible_jobs",
        "renew_claim",
        "mark_succeeded",
        "requeue_retryable_failure",
        "mark_terminal_failure",
        "recover_expired_claims",
        "inspect_remaining_work",
    ):
        assert callable(getattr(svc, name))
    # No max-attempt parameter on failure transitions.
    sig = inspect.signature(svc.requeue_retryable_failure)
    assert "max_attempts" not in sig.parameters
    assert "attempt_ceiling" not in sig.parameters


def test_module_does_not_import_legacy_discovery_orchestration():
    assert "source_discovery_service" not in claim_mod.__dict__
    source = SERVICE_PATH.read_text(encoding="utf-8")
    assert "from app.services.scraping.source_discovery_service" not in source
    assert "import source_discovery_service" not in source
    assert "SourceDiscoveryService" not in source
    assert "SourceDiscoveryQueryPlanner" not in source
    assert "run_scraping_execution" not in source
    assert "from app.services.scraping.mission_campaign_mock_worker" not in source
    assert "import mission_campaign_mock_worker" not in source
