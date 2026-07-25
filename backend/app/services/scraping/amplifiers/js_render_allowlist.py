"""Bounded JavaScript-render allowlist checks."""

from __future__ import annotations

from urllib.parse import urlparse


def is_js_render_allowed(url: str, allowlisted_domains: list[str]) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"http", "https"} or not hostname:
        return False
    for domain in allowlisted_domains:
        normalized = (domain or "").strip().casefold()
        if not normalized:
            continue
        if hostname == normalized or hostname.endswith(f".{normalized}"):
            return True
    return False
