"""Provider-neutral web search contracts for source discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class SearchProviderError(Exception):
    code = "provider_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message or self.code)
        self.http_status = http_status


class SearchProviderConfigurationError(SearchProviderError):
    code = "configuration_missing"


class SearchProviderAuthError(SearchProviderError):
    code = "authentication_failed"


class SearchProviderRateLimitedError(SearchProviderError):
    code = "rate_limited"

    def __init__(
        self,
        message: str | None = None,
        *,
        http_status: int | None = 429,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message, http_status=http_status)
        self.retry_after_seconds = retry_after_seconds


class SearchProviderTimeoutError(SearchProviderError):
    code = "request_timeout"


class SearchProviderUnavailableError(SearchProviderError):
    code = "provider_unavailable"


class SearchProviderNetworkError(SearchProviderError):
    code = "network_error"


class SearchProviderInvalidRequestError(SearchProviderError):
    code = "request_invalid"


class SearchProviderInvalidResponseError(SearchProviderError):
    code = "invalid_response"


@dataclass(frozen=True)
class SearchProviderRequest:
    query: str
    country_code: str
    search_language: str
    result_limit: int
    # 1-indexed Serper page cursor when the adapter supports pagination.
    page: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchProviderResult:
    rank: int
    url: str
    title: str
    snippet: str
    provider_result_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SearchProvider(Protocol):
    name: str

    async def search(self, request: SearchProviderRequest) -> list[SearchProviderResult]: ...
