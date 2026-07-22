"""Search provider adapters for real source discovery."""

from app.services.scraping.search_providers.base import (
    SearchProvider,
    SearchProviderAuthError,
    SearchProviderConfigurationError,
    SearchProviderError,
    SearchProviderInvalidResponseError,
    SearchProviderRateLimitedError,
    SearchProviderRequest,
    SearchProviderResult,
    SearchProviderTimeoutError,
    SearchProviderUnavailableError,
)
from app.services.scraping.search_providers.brave import BraveSearchProvider
from app.services.scraping.search_providers.serper import SerperSearchProvider


def create_search_provider(provider_name: str | None = None) -> SearchProvider:
    from app.core.config import get_settings

    configured = (provider_name or get_settings().source_discovery_provider).strip().lower()
    if configured == "serper":
        return SerperSearchProvider()
    if configured == "brave":
        return BraveSearchProvider()
    raise SearchProviderConfigurationError(f"Unsupported source discovery provider: {configured}")

__all__ = [
    "BraveSearchProvider",
    "SerperSearchProvider",
    "SearchProvider",
    "SearchProviderAuthError",
    "SearchProviderConfigurationError",
    "SearchProviderError",
    "SearchProviderInvalidResponseError",
    "SearchProviderRateLimitedError",
    "SearchProviderRequest",
    "SearchProviderResult",
    "SearchProviderTimeoutError",
    "SearchProviderUnavailableError",
    "create_search_provider",
]
