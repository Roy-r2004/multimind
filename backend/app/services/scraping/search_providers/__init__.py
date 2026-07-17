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

__all__ = [
    "BraveSearchProvider",
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
]
