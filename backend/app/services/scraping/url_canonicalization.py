"""URL validation and canonicalization for discovery-stage source candidates."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_DISCOVERY_URL_LENGTH = 2048
TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "gbraid",
    "wbraid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
}
RESERVED_HOSTS = {
    "example.com",
    "example.org",
    "example.net",
    "example.edu",
    "example.invalid",
    "mock.example",
}
RESERVED_SUFFIXES = (
    ".example.com",
    ".example.org",
    ".example.net",
    ".example.edu",
    ".example.invalid",
)
LOCALHOST_HOSTS = {"localhost", "localhost.localdomain"}
METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
}


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
    original = (raw_url or "").strip()
    if not original:
        raise UrlRejected("empty_url")
    if len(original) > MAX_DISCOVERY_URL_LENGTH:
        raise UrlRejected("url_too_long")

    parsed = urlsplit(original)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise UrlRejected("unsupported_scheme")
    if parsed.username or parsed.password:
        raise UrlRejected("embedded_credentials")
    if not parsed.hostname:
        raise UrlRejected("missing_hostname")

    hostname = parsed.hostname.rstrip(".").lower()
    _validate_hostname(hostname)

    port = parsed.port
    netloc = hostname
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"

    path = parsed.path or "/"
    query = _canonical_query(parsed.query)
    canonical = urlunsplit((scheme, netloc, path, query, ""))
    if len(canonical) > MAX_DISCOVERY_URL_LENGTH:
        raise UrlRejected("canonical_url_too_long")

    return CanonicalUrl(original_url=original, canonical_url=canonical, domain=hostname)


def _canonical_query(query: str) -> str:
    if not query:
        return ""
    pairs: list[tuple[str, str]] = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in TRACKING_QUERY_KEYS:
            continue
        if any(lowered.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            continue
        pairs.append((key, value))
    return urlencode(pairs, doseq=True)


def _validate_hostname(hostname: str) -> None:
    if hostname in LOCALHOST_HOSTS or hostname.endswith(".localhost"):
        raise UrlRejected("localhost_hostname")
    if hostname in RESERVED_HOSTS or any(hostname.endswith(suffix) for suffix in RESERVED_SUFFIXES):
        raise UrlRejected("reserved_example_domain")

    try:
        ip = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return

    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip in METADATA_IPS
    ):
        raise UrlRejected("unsafe_ip_literal")
