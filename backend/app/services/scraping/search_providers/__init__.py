"""Search provider adapters for real source discovery."""

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
    SearchProviderResult,
    SearchProviderTimeoutError,
    SearchProviderUnavailableError,
)
from app.services.scraping.search_providers.brave import BraveSearchProvider
from app.services.scraping.search_providers.serper import SerperSearchProvider

# Phase 4 v2 broad discovery: only explicitly approved real providers.
APPROVED_V2_DISCOVERY_PROVIDERS = frozenset({"serper"})


def create_search_provider(provider_name: str | None = None) -> SearchProvider:
    from app.core.config import get_settings

    configured = (provider_name or get_settings().source_discovery_provider).strip().lower()
    if configured == "serper":
        return SerperSearchProvider()
    if configured == "brave":
        return BraveSearchProvider()
    raise SearchProviderConfigurationError(f"Unsupported source discovery provider: {configured}")


def resolve_v2_discovery_provider(
    provider_name: str,
    *,
    provider: SearchProvider | None = None,
    client_factory=None,
) -> SearchProvider:
    """Resolve a real v2 discovery adapter. Never selects Brave/fake by default."""
    normalized = (provider_name or "").strip().lower()
    if normalized not in APPROVED_V2_DISCOVERY_PROVIDERS:
        raise SearchProviderConfigurationError(
            f"Unsupported v2 source discovery provider: {normalized or 'missing'}"
        )
    if provider is not None:
        injected_name = (getattr(provider, "name", "") or "").strip().lower()
        if injected_name and injected_name != normalized:
            raise SearchProviderConfigurationError(
                f"Injected provider name mismatch: expected {normalized}"
            )
        return provider
    if normalized == "serper":
        return SerperSearchProvider(client_factory=client_factory)
    raise SearchProviderConfigurationError(f"Unsupported v2 source discovery provider: {normalized}")


__all__ = [
    "APPROVED_V2_DISCOVERY_PROVIDERS",
    "BraveSearchProvider",
    "SerperSearchProvider",
    "SearchProvider",
    "SearchProviderAuthError",
    "SearchProviderConfigurationError",
    "SearchProviderError",
    "SearchProviderInvalidRequestError",
    "SearchProviderInvalidResponseError",
    "SearchProviderNetworkError",
    "SearchProviderRateLimitedError",
    "SearchProviderRequest",
    "SearchProviderResult",
    "SearchProviderTimeoutError",
    "SearchProviderUnavailableError",
    "create_search_provider",
    "resolve_v2_discovery_provider",
]
