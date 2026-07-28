"""URL validation and canonicalization for discovery-stage source candidates.

Legacy public contract for existing discovery/provider callers.
Phase 4 Slice 2 logic lives in ``discovery_url_service``; this module
delegates while preserving ``UrlRejected`` / ``CanonicalUrl``.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.scraping.discovery_url_service import (
    MAX_DISCOVERY_URL_LENGTH,
    TRACKING_QUERY_KEYS,
    TRACKING_QUERY_PREFIXES,
    canonicalize_discovery_target,
)

# Re-exported for any callers/tests that imported constants from here.
__all__ = [
    "CanonicalUrl",
    "MAX_DISCOVERY_URL_LENGTH",
    "TRACKING_QUERY_KEYS",
    "TRACKING_QUERY_PREFIXES",
    "UrlRejected",
    "canonicalize_discovery_url",
]


class UrlRejected(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CanonicalUrl:
    original_url: str
    canonical_url: str
    domain: str


def canonicalize_discovery_url(raw_url: str) -> CanonicalUrl:
    """Legacy API: raise ``UrlRejected`` on failure; ``domain`` is the hostname."""
    result = canonicalize_discovery_target(raw_url)
    if not result.is_valid or not result.is_statically_safe or not result.canonical_url or not result.hostname:
        # Prefer structured codes from the new service; keep a stable fallback.
        raise UrlRejected(result.error_code or "invalid_url")
    return CanonicalUrl(
        original_url=result.original_url,
        canonical_url=result.canonical_url,
        domain=result.hostname,
    )
