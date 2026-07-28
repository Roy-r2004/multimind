"""Phase 4 Slice 4: real configured provider execution for claimed query jobs.

Runs outside any SQLAlchemy session/transaction. Claim lifecycle and result
persistence are owned by adjacent slices — this module only executes one
provider request against an already-claimed immutable job DTO.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from app.core.config import get_settings
from app.services.scraping.blueprint_execution_plan_service import sha256_hex
from app.services.scraping.search_providers import (
    APPROVED_V2_DISCOVERY_PROVIDERS,
    resolve_v2_discovery_provider,
)
from app.services.scraping.search_providers.base import (
    SearchProvider,
    SearchProviderAuthError,
    SearchProviderConfigurationError,
    SearchProviderError,
    SearchProviderInvalidRequestError,
    SearchProviderInvalidResponseError,
    SearchProviderNetworkError,
    SearchProviderRateLimitedError,
    SearchProviderRequest,
    SearchProviderTimeoutError,
    SearchProviderUnavailableError,
)
from app.services.scraping.search_providers.serper import MAX_RESULT_LIMIT, MAX_SNIPPET_LENGTH, MAX_TITLE_LENGTH
from app.services.scraping.source_discovery_claim_service import ClaimedQueryJob

NowFactory = Callable[[], datetime]

DiscoveryProviderOutcome = Literal[
    "succeeded",
    "provider_rate_limited",
    "provider_timeout",
    "provider_unavailable",
    "provider_network_error",
    "provider_server_error",
    "provider_not_configured",
    "provider_authentication_failed",
    "provider_request_invalid",
    "malformed_provider_response",
    "unsupported_provider",
    "repeated_provider_page",
]

RETRYABLE_OUTCOMES: frozenset[str] = frozenset(
    {
        "provider_rate_limited",
        "provider_timeout",
        "provider_unavailable",
        "provider_network_error",
        "provider_server_error",
    }
)

PROVIDER_WIDE_BLOCKERS: frozenset[str] = frozenset(
    {
        "provider_not_configured",
        "provider_authentication_failed",
        "unsupported_provider",
    }
)

QUERY_TERMINAL_OUTCOMES: frozenset[str] = frozenset(
    {
        "provider_request_invalid",
        "malformed_provider_response",
        "repeated_provider_page",
    }
)

# Legacy alias: query-terminal + provider-wide (execution routes blockers separately).
TERMINAL_OUTCOMES: frozenset[str] = frozenset(PROVIDER_WIDE_BLOCKERS | QUERY_TERMINAL_OUTCOMES)


class _PayloadSearchProvider(Protocol):
    name: str

    async def fetch_payload(self, request: SearchProviderRequest) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DiscoveryProviderContinuation:
    """Safe page/request metadata when genuinely available from the call.

    Serper contract (from adapter): 1-indexed ``page`` body field, ``num`` page
    size capped at MAX_RESULT_LIMIT (20). No continuation token in the response.
    ``has_more`` is True only when a full page of organic results returned
    (may have another page). Empty or short organic pages are terminal.
    """

    requested_page_size: int | None = None
    returned_result_count: int | None = None
    next_page_token: str | None = None
    has_more: bool | None = None
    page_number: int | None = None
    page_fingerprint: str | None = None


@dataclass(frozen=True)
class DiscoveryProviderResultItem:
    original_url: str
    title: str
    snippet: str
    rank: int
    provider: str
    provider_result_type: str | None
    query_job_id: str
    organization_id: str
    execution_id: str
    claim_token: str
    scope_level: str
    language_code: str
    region_code: str | None
    region_name: str | None
    important_city: str | None
    country_code: str
    discovered_at: datetime
    provider_metadata: Mapping[str, Any] = field(default_factory=dict)
    continuation: DiscoveryProviderContinuation | None = None
    provider_page_number: int | None = None


@dataclass(frozen=True)
class DiscoveryProviderExecutionResult:
    outcome: DiscoveryProviderOutcome
    provider: str
    query_job_id: str
    organization_id: str
    execution_id: str
    claim_token: str
    discovered_at: datetime
    results: tuple[DiscoveryProviderResultItem, ...] = ()
    raw_result_count: int = 0
    accepted_result_count: int = 0
    skipped_malformed_count: int = 0
    http_status: int | None = None
    diagnostic_code: str | None = None
    retry_after_seconds: float | None = None
    retry_after_at: datetime | None = None
    continuation: DiscoveryProviderContinuation | None = None
    provider_page_size: int | None = None
    page_number: int | None = None
    page_fingerprint: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome == "succeeded"

    @property
    def retryable(self) -> bool:
        return self.outcome in RETRYABLE_OUTCOMES

    @property
    def terminal(self) -> bool:
        return self.outcome in TERMINAL_OUTCOMES

    @property
    def provider_wide_blocker(self) -> bool:
        return self.outcome in PROVIDER_WIDE_BLOCKERS

    @property
    def query_terminal(self) -> bool:
        return self.outcome in QUERY_TERMINAL_OUTCOMES


class SourceDiscoveryProviderService:
    """Execute one claimed discovery query against a real configured provider."""

    def __init__(
        self,
        *,
        provider: SearchProvider | None = None,
        client_factory: Callable[..., Any] | None = None,
        now_factory: NowFactory | None = None,
    ) -> None:
        self._injected_provider = provider
        self._client_factory = client_factory
        self._now_factory = now_factory or (lambda: datetime.now(UTC))

    async def execute_claimed_query(
        self,
        claimed_job: ClaimedQueryJob,
        provider_name: str,
        *,
        result_page_size: int | None = None,
    ) -> DiscoveryProviderExecutionResult:
        """Run provider HTTP for a detached claim. No DB session/transaction."""
        now = self._now_factory()
        validation = _validate_claimed_job(claimed_job, provider_name)
        if validation is not None:
            return _empty_result(
                claimed_job=claimed_job,
                provider_name=_safe_provider_label(provider_name, claimed_job),
                outcome=validation,
                discovered_at=now,
                diagnostic_code=validation,
            )

        normalized_provider = provider_name.strip().lower()
        try:
            adapter = resolve_v2_discovery_provider(
                normalized_provider,
                provider=self._injected_provider,
                client_factory=self._client_factory,
            )
        except SearchProviderConfigurationError:
            return _empty_result(
                claimed_job=claimed_job,
                provider_name=normalized_provider,
                outcome="unsupported_provider",
                discovered_at=now,
                diagnostic_code="unsupported_provider",
            )

        page_size = _provider_page_size(normalized_provider, result_page_size)
        page_number = _page_number(getattr(claimed_job, "next_page_number", None))
        request = SearchProviderRequest(
            query=claimed_job.query_text,
            country_code=claimed_job.country_code,
            search_language=claimed_job.language_code,
            result_limit=page_size,
            page=page_number,
            metadata={
                "scope_level": claimed_job.scope_level,
                "region_code": claimed_job.region_code,
                "important_city": claimed_job.important_city,
                "source_category": claimed_job.source_category,
                "page": page_number,
            },
        )

        try:
            payload = await _fetch_payload(adapter, request)
            parsed = _parse_organic_results(
                payload,
                claimed_job=claimed_job,
                provider_name=normalized_provider,
                discovered_at=now,
                page_size=page_size,
                page_number=page_number,
            )
        except SearchProviderError as exc:
            return _map_provider_error(
                claimed_job=claimed_job,
                provider_name=normalized_provider,
                exc=exc,
                discovered_at=now,
                page_size=page_size,
                page_number=page_number,
            )

        fingerprint = parsed.page_fingerprint
        prior_fingerprint = getattr(claimed_job, "last_page_fingerprint", None)
        if (
            isinstance(prior_fingerprint, str)
            and prior_fingerprint
            and fingerprint == prior_fingerprint
            and page_number > 1
        ):
            return _empty_result(
                claimed_job=claimed_job,
                provider_name=normalized_provider,
                outcome="repeated_provider_page",
                discovered_at=now,
                diagnostic_code="repeated_provider_page",
                page_size=page_size,
                page_number=page_number,
                page_fingerprint=fingerprint,
            )

        # Serper: full page ⇒ more pages may exist; empty/short organic ⇒ terminal.
        has_more = parsed.raw_result_count >= page_size and parsed.raw_result_count > 0
        continuation = DiscoveryProviderContinuation(
            requested_page_size=page_size,
            returned_result_count=parsed.raw_result_count,
            next_page_token=None,
            has_more=has_more,
            page_number=page_number,
            page_fingerprint=fingerprint,
        )
        return DiscoveryProviderExecutionResult(
            outcome="succeeded",
            provider=normalized_provider,
            query_job_id=claimed_job.id,
            organization_id=claimed_job.organization_id,
            execution_id=claimed_job.execution_id,
            claim_token=claimed_job.claim_token,
            discovered_at=now,
            results=parsed.results,
            raw_result_count=parsed.raw_result_count,
            accepted_result_count=parsed.accepted_result_count,
            skipped_malformed_count=parsed.skipped_malformed_count,
            diagnostic_code="succeeded",
            continuation=continuation,
            provider_page_size=page_size,
            page_number=page_number,
            page_fingerprint=fingerprint,
        )


@dataclass(frozen=True)
class _ParsedOrganic:
    results: tuple[DiscoveryProviderResultItem, ...]
    raw_result_count: int
    accepted_result_count: int
    skipped_malformed_count: int
    page_fingerprint: str


def _validate_claimed_job(
    claimed_job: ClaimedQueryJob,
    provider_name: str,
) -> DiscoveryProviderOutcome | None:
    if not isinstance(claimed_job, ClaimedQueryJob):
        return "provider_request_invalid"
    if not _non_empty_str(claimed_job.organization_id):
        return "provider_request_invalid"
    if not _non_empty_str(claimed_job.execution_id):
        return "provider_request_invalid"
    if not _non_empty_str(claimed_job.id):
        return "provider_request_invalid"
    if not _non_empty_str(claimed_job.claim_token):
        return "provider_request_invalid"
    if not _non_empty_str(claimed_job.query_text):
        return "provider_request_invalid"

    requested = (provider_name or "").strip().lower()
    claimed = (claimed_job.provider or "").strip().lower()
    if not requested or requested not in APPROVED_V2_DISCOVERY_PROVIDERS:
        return "unsupported_provider"
    if claimed != requested:
        return "unsupported_provider"
    return None


def _provider_page_size(provider_name: str, override: int | None) -> int:
    """Operational provider page size only — not a campaign-wide result cap."""
    settings = get_settings()
    if override is not None:
        configured = override
    elif provider_name == "serper":
        configured = settings.serper_search_results_per_query
    else:
        configured = 10
    return min(max(int(configured), 1), MAX_RESULT_LIMIT)


def _page_number(value: Any) -> int:
    if value is None:
        return 1
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


def build_serper_page_fingerprint(payload: dict[str, Any]) -> str:
    """Fingerprint via ``sha256_hex(payload_dict)`` — pass a dict, never raw bytes.

    Uses the organic result identity fields only (no query text / secrets).
    """
    raw = payload.get("organic", []) if isinstance(payload, dict) else []
    if raw is None:
        raw = []
    organic_items: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                organic_items.append({"_malformed": True})
                continue
            organic_items.append(
                {
                    "link": item.get("link"),
                    "title": item.get("title"),
                    "snippet": item.get("snippet"),
                    "position": item.get("position"),
                }
            )
    return sha256_hex({"organic": organic_items})


async def _fetch_payload(adapter: SearchProvider, request: SearchProviderRequest) -> dict[str, Any]:
    fetch = getattr(adapter, "fetch_payload", None)
    if callable(fetch):
        payload = await fetch(request)
        if not isinstance(payload, dict):
            raise SearchProviderInvalidResponseError("Provider payload root is invalid")
        return payload
    if not hasattr(adapter, "search"):
        raise SearchProviderConfigurationError("Provider adapter cannot execute search")
    results = await adapter.search(request)
    organic: list[dict[str, Any]] = []
    for item in results:
        organic.append(
            {
                "position": item.rank,
                "link": item.url,
                "title": item.title,
                "snippet": item.snippet,
            }
        )
    return {"organic": organic}


def _parse_organic_results(
    payload: dict[str, Any],
    *,
    claimed_job: ClaimedQueryJob,
    provider_name: str,
    discovered_at: datetime,
    page_size: int,
    page_number: int,
) -> _ParsedOrganic:
    if not isinstance(payload, dict):
        raise SearchProviderInvalidResponseError("Provider payload root is invalid")
    raw_results = payload.get("organic", [])
    if raw_results is None:
        raw_results = []
    if not isinstance(raw_results, list):
        raise SearchProviderInvalidResponseError("Serper Search organic results section is invalid")

    fingerprint = build_serper_page_fingerprint(payload)
    continuation = DiscoveryProviderContinuation(
        requested_page_size=page_size,
        returned_result_count=len(raw_results),
        next_page_token=None,
        has_more=None,
        page_number=page_number,
        page_fingerprint=fingerprint,
    )
    accepted: list[DiscoveryProviderResultItem] = []
    skipped = 0
    for fallback_rank, raw in enumerate(raw_results, start=1):
        if not isinstance(raw, dict):
            skipped += 1
            continue
        url = raw.get("link")
        if not isinstance(url, str) or not url.strip():
            skipped += 1
            continue
        title = _optional_bounded_text(raw.get("title"), MAX_TITLE_LENGTH)
        snippet = _optional_bounded_text(raw.get("snippet"), MAX_SNIPPET_LENGTH)
        rank = _rank(raw.get("position"), fallback_rank)
        accepted.append(
            DiscoveryProviderResultItem(
                original_url=url,
                title=title,
                snippet=snippet,
                rank=rank,
                provider=provider_name,
                provider_result_type="organic",
                query_job_id=claimed_job.id,
                organization_id=claimed_job.organization_id,
                execution_id=claimed_job.execution_id,
                claim_token=claimed_job.claim_token,
                scope_level=claimed_job.scope_level,
                language_code=claimed_job.language_code,
                region_code=claimed_job.region_code,
                region_name=claimed_job.region_name,
                important_city=claimed_job.important_city,
                country_code=claimed_job.country_code,
                discovered_at=discovered_at,
                provider_metadata={"position": rank, "page": page_number},
                continuation=continuation,
                provider_page_number=page_number,
            )
        )

    return _ParsedOrganic(
        results=tuple(accepted),
        raw_result_count=len(raw_results),
        accepted_result_count=len(accepted),
        skipped_malformed_count=skipped,
        page_fingerprint=fingerprint,
    )


def _map_provider_error(
    *,
    claimed_job: ClaimedQueryJob,
    provider_name: str,
    exc: SearchProviderError,
    discovered_at: datetime,
    page_size: int,
    page_number: int | None = None,
) -> DiscoveryProviderExecutionResult:
    outcome: DiscoveryProviderOutcome
    retry_after_seconds: float | None = None
    retry_after_at: datetime | None = None
    http_status = getattr(exc, "http_status", None)

    if isinstance(exc, SearchProviderConfigurationError):
        message = str(exc).lower()
        if "unsupported" in message:
            outcome = "unsupported_provider"
        else:
            outcome = "provider_not_configured"
    elif isinstance(exc, SearchProviderAuthError):
        outcome = "provider_authentication_failed"
    elif isinstance(exc, SearchProviderRateLimitedError):
        outcome = "provider_rate_limited"
        retry_after_seconds = exc.retry_after_seconds
        if retry_after_seconds is not None:
            retry_after_at = discovered_at + timedelta(seconds=retry_after_seconds)
    elif isinstance(exc, SearchProviderTimeoutError):
        outcome = "provider_timeout"
    elif isinstance(exc, SearchProviderNetworkError):
        outcome = "provider_network_error"
    elif isinstance(exc, SearchProviderInvalidRequestError):
        outcome = "provider_request_invalid"
    elif isinstance(exc, SearchProviderInvalidResponseError):
        if http_status in {400, 422}:
            outcome = "provider_request_invalid"
        else:
            outcome = "malformed_provider_response"
    elif isinstance(exc, SearchProviderUnavailableError):
        if isinstance(http_status, int) and http_status >= 500:
            outcome = "provider_server_error"
        else:
            outcome = "provider_unavailable"
    else:
        outcome = "provider_unavailable"

    return _empty_result(
        claimed_job=claimed_job,
        provider_name=provider_name,
        outcome=outcome,
        discovered_at=discovered_at,
        diagnostic_code=outcome,
        http_status=http_status if isinstance(http_status, int) else None,
        retry_after_seconds=retry_after_seconds,
        retry_after_at=retry_after_at,
        page_size=page_size,
        page_number=page_number,
    )


def _empty_result(
    *,
    claimed_job: ClaimedQueryJob | None,
    provider_name: str,
    outcome: DiscoveryProviderOutcome,
    discovered_at: datetime,
    diagnostic_code: str | None = None,
    http_status: int | None = None,
    retry_after_seconds: float | None = None,
    retry_after_at: datetime | None = None,
    page_size: int | None = None,
    page_number: int | None = None,
    page_fingerprint: str | None = None,
) -> DiscoveryProviderExecutionResult:
    return DiscoveryProviderExecutionResult(
        outcome=outcome,
        provider=provider_name,
        query_job_id=getattr(claimed_job, "id", "") if claimed_job is not None else "",
        organization_id=getattr(claimed_job, "organization_id", "") if claimed_job is not None else "",
        execution_id=getattr(claimed_job, "execution_id", "") if claimed_job is not None else "",
        claim_token=getattr(claimed_job, "claim_token", "") if claimed_job is not None else "",
        discovered_at=discovered_at,
        diagnostic_code=diagnostic_code,
        http_status=http_status,
        retry_after_seconds=retry_after_seconds,
        retry_after_at=retry_after_at,
        provider_page_size=page_size,
        page_number=page_number,
        page_fingerprint=page_fingerprint,
    )


def _safe_provider_label(provider_name: str, claimed_job: ClaimedQueryJob) -> str:
    label = (provider_name or "").strip().lower()
    if label:
        return label
    return (claimed_job.provider or "").strip().lower() or "unknown"


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _optional_bounded_text(value: Any, max_length: int) -> str:
    """Preserve blank for missing title/snippet — never fabricate content."""
    if value is None:
        return ""
    if not isinstance(value, str):
        text = str(value).strip()
    else:
        text = value.strip()
    return text[:max_length]


def _rank(value: Any, fallback: int) -> int:
    if isinstance(value, int) and value >= 1:
        return value
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed >= 1 else fallback


async def execute_claimed_query(
    claimed_job: ClaimedQueryJob,
    provider_name: str,
    *,
    provider: SearchProvider | None = None,
    client_factory: Callable[..., Any] | None = None,
    now_factory: NowFactory | None = None,
    result_page_size: int | None = None,
) -> DiscoveryProviderExecutionResult:
    """Module-level API matching the Phase 4 conceptual contract."""
    service = SourceDiscoveryProviderService(
        provider=provider,
        client_factory=client_factory,
        now_factory=now_factory,
    )
    return await service.execute_claimed_query(
        claimed_job,
        provider_name,
        result_page_size=result_page_size,
    )


__all__ = [
    "DiscoveryProviderContinuation",
    "DiscoveryProviderExecutionResult",
    "DiscoveryProviderOutcome",
    "DiscoveryProviderResultItem",
    "PROVIDER_WIDE_BLOCKERS",
    "QUERY_TERMINAL_OUTCOMES",
    "RETRYABLE_OUTCOMES",
    "SourceDiscoveryProviderService",
    "TERMINAL_OUTCOMES",
    "build_serper_page_fingerprint",
    "execute_claimed_query",
]
