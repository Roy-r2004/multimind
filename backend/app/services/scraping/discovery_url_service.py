"""Deterministic discovery URL canonicalization, safety, and classification.

Phase 4 Slice 2 authority for:
- canonical URL form
- static + injectable-DNS discovery safety
- initial source classification
- canonical URL fingerprint payload

No LLM involvement. No content fetch. No production fake-resolver fallback.

Domain note: the repository has no public-suffix / registrable-domain dependency.
``normalized_domain`` is therefore the ASCII-normalized hostname for DNS names and
``None`` for IP literals. It is **not** eTLD+1 (``co.uk``-style suffixes are not
split). Callers that need true registrable domains must introduce a PSL library later.

Discovery safety prevents unsafe URLs from being scheduled into the crawl graph.
Later retrieval must still revalidate every connection and redirect destination;
this module does not claim DNS-rebinding immunity and does not cache DNS forever.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.db.models import CrawlNodeSourceClassification
from app.services.scraping.blueprint_execution_plan_service import sha256_hex

MAX_DISCOVERY_URL_LENGTH = 2048
CANONICAL_URL_HASH_SCHEMA = "phase4_canonical_url_v1"

TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref_src",
        "ref_url",
    }
)

# Kept for discovery/mock hygiene (aligned with legacy url_canonicalization).
RESERVED_HOSTS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "example.edu",
        "example.invalid",
        "mock.example",
    }
)
RESERVED_SUFFIXES = (
    ".example.com",
    ".example.org",
    ".example.net",
    ".example.edu",
    ".example.invalid",
)

LOCALHOST_HOSTS = frozenset({"localhost", "localhost.localdomain"})
METADATA_HOSTS = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "169.254.169.254.nip.io",
    }
)
METADATA_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("100.100.100.200"),
    }
)

_CONTROL_OR_WHITESPACE = re.compile(r"[\x00-\x1f\x7f\s]")

SOCIAL_HOST_SUFFIXES = (
    "facebook.com",
    "fb.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "pinterest.com",
    "reddit.com",
    "threads.net",
)

COMMERCIAL_LISTING_HOST_SUFFIXES = (
    "yelp.com",
    "tripadvisor.com",
    "booking.com",
    "pagesjaunes.fr",
    "yellowpages.com",
    "yellowpages.ca",
    "houzz.com",
    "angi.com",
    "thumbtack.com",
)

DIRECTORY_HOST_SUFFIXES = (
    "healthcare.com",
    "healthgrades.com",
    "vitals.com",
    "webmd.com",
    "doctolib.fr",
    "doctolib.com",
)

SEARCH_ENGINE_HOST_SUFFIXES = (
    "google.com",
    "google.fr",
    "bing.com",
    "yahoo.com",
    "duckduckgo.com",
    "baidu.com",
    "yandex.com",
    "yandex.ru",
)

SUPPORTING_HOST_SUFFIXES = (
    "wikipedia.org",
    "wikidata.org",
    "nih.gov",
    "who.int",
    "pubmed.ncbi.nlm.nih.gov",
)

GOVERNMENT_HOST_MARKERS = (
    ".gov",
    ".gouv.",
    ".gob.",
    ".go.jp",
    ".gov.uk",
    ".gov.au",
    ".govt.nz",
    ".gc.ca",
    ".mil",
)

REGISTRY_PATH_TOKENS = (
    "/registry",
    "/registre",
    "/register",
    "/licence",
    "/license",
    "/licensing",
    "/accreditation",
    "/annuaire",
)
DIRECTORY_PATH_TOKENS = (
    "/directory",
    "/directories",
    "/listing",
    "/listings",
    "/find-",
    "/search",
    "/annuaire",
    "/etablissements",
)
PROFILE_PATH_TOKENS = (
    "/profile/",
    "/profiles/",
    "/facility/",
    "/facilities/",
    "/etablissement/",
    "/fiche/",
    "/provider/",
    "/doctor/",
)
OFFICIAL_PATHS = frozenset(
    {
        "/",
        "/index.html",
        "/index.htm",
        "/about",
        "/about-us",
        "/about.html",
        "/accueil",
        "/home",
        "/contact",
        "/contact-us",
        "/contact.html",
    }
)

DiscoveryDnsResolver = Callable[[str], Sequence[str]]


@dataclass(frozen=True)
class DiscoveryUrlCanonicalization:
    """Immutable parse/canonicalize result. Public fields never carry raw exceptions."""

    original_url: str
    canonical_url: str | None
    scheme: str | None
    hostname: str | None
    normalized_domain: str | None
    port: int | None
    path: str | None
    query: str | None
    tracking_removed: bool
    removed_tracking_params: tuple[str, ...]
    is_valid: bool
    is_statically_safe: bool
    error_code: str | None


@dataclass(frozen=True)
class DiscoveryTargetSafety:
    """Static and optional DNS safety outcome for a discovery target."""

    is_safe: bool
    error_code: str | None
    hostname: str | None
    dns_checked: bool
    resolved_address_count: int = 0


@dataclass(frozen=True)
class DiscoverySourceClassification:
    classification: str
    reason_code: str
    evidence_flags: tuple[str, ...] = ()


def canonicalize_discovery_target(raw_url: str) -> DiscoveryUrlCanonicalization:
    """Parse and canonicalize a discovery URL without fetching or resolving DNS."""
    original = raw_url if isinstance(raw_url, str) else ""
    stripped = original.strip()
    if not stripped:
        return _invalid(original, "invalid_url")
    if len(stripped) > MAX_DISCOVERY_URL_LENGTH:
        return _invalid(original, "invalid_url")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in stripped):
        return _invalid(original, "invalid_url")
    if _has_internal_whitespace(stripped):
        return _invalid(original, "invalid_url")

    try:
        parsed = urlsplit(stripped)
    except ValueError:
        return _invalid(original, "invalid_url")

    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return _invalid(original, "unsupported_scheme")
    if parsed.username is not None or parsed.password is not None:
        return _invalid(original, "embedded_credentials")
    if "@" in (parsed.netloc or ""):
        # urlsplit sometimes leaves malformed authorities; never allow credentials.
        return _invalid(original, "embedded_credentials")

    raw_host = parsed.hostname
    if not raw_host:
        return _invalid(original, "missing_hostname")

    try:
        port = parsed.port
    except ValueError:
        return _invalid(original, "invalid_port")
    if port is not None and not (1 <= port <= 65535):
        return _invalid(original, "invalid_port")

    host_result = normalize_hostname(raw_host)
    if host_result.error_code:
        return _invalid(original, host_result.error_code)
    hostname = host_result.hostname
    assert hostname is not None

    path = remove_dot_segments(parsed.path or "/")
    query, removed, tracking_removed = strip_tracking_query(parsed.query)
    netloc = _format_netloc(hostname, scheme, port)
    canonical = urlunsplit((scheme, netloc, path, query, ""))
    if len(canonical) > MAX_DISCOVERY_URL_LENGTH:
        return _invalid(original, "invalid_url")

    static_safety = validate_discovery_hostname_static(hostname)
    normalized_domain = derive_normalized_domain(hostname)
    return DiscoveryUrlCanonicalization(
        original_url=stripped,
        canonical_url=canonical,
        scheme=scheme,
        hostname=hostname,
        normalized_domain=normalized_domain,
        port=port,
        path=path,
        query=query,
        tracking_removed=tracking_removed,
        removed_tracking_params=tuple(removed),
        is_valid=True,
        is_statically_safe=static_safety.is_safe,
        error_code=None if static_safety.is_safe else static_safety.error_code,
    )


@dataclass(frozen=True)
class HostnameNormalization:
    hostname: str | None
    error_code: str | None


def normalize_hostname(raw_hostname: str) -> HostnameNormalization:
    """Lowercase, strip one trailing dot, IDN→ASCII. Reject malformed hosts."""
    if raw_hostname is None:
        return HostnameNormalization(None, "missing_hostname")
    host = raw_hostname.strip()
    if not host:
        return HostnameNormalization(None, "missing_hostname")
    if _CONTROL_OR_WHITESPACE.search(host):
        return HostnameNormalization(None, "malformed_hostname")
    if host.endswith("."):
        host = host[:-1]
        if not host or host.endswith("."):
            return HostnameNormalization(None, "malformed_hostname")
    host = host.lower()

    # Bracketed IPv6 from some callers.
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return HostnameNormalization(host, None)

    try:
        ascii_host = host.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return HostnameNormalization(None, "invalid_idn")

    if not ascii_host or ascii_host.startswith(".") or ".." in ascii_host:
        return HostnameNormalization(None, "malformed_hostname")
    if any(ord(ch) < 32 or ord(ch) == 127 or ch.isspace() for ch in ascii_host):
        return HostnameNormalization(None, "malformed_hostname")
    # Basic DNS label checks (no homemade PSL).
    labels = ascii_host.split(".")
    if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in labels):
        return HostnameNormalization(None, "malformed_hostname")
    if len(ascii_host) > 253:
        return HostnameNormalization(None, "malformed_hostname")
    return HostnameNormalization(ascii_host, None)


def derive_normalized_domain(hostname: str | None) -> str | None:
    """Conservative domain identity without a public-suffix list.

    Returns the ASCII hostname for DNS names. Returns ``None`` for IP literals
    (not registrable domains) and empty/invalid input. Does **not** compute
    eTLD+1; multi-part public suffixes such as ``co.uk`` are not special-cased.
    """
    if not hostname:
        return None
    try:
        ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        return hostname.lower().rstrip(".")
    return None


def strip_tracking_query(query: str) -> tuple[str, list[str], bool]:
    """Remove tracking-only query params case-insensitively; preserve order/repeats."""
    if not query:
        return "", [], False
    pairs: list[tuple[str, str]] = []
    removed: list[str] = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in TRACKING_QUERY_KEYS or any(lowered.startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES):
            removed.append(key)
            continue
        pairs.append((key, value))
    return urlencode(pairs, doseq=True), removed, bool(removed)


def remove_dot_segments(path: str) -> str:
    """RFC 3986 remove_dot_segments for absolute URL paths."""
    if not path:
        return "/"
    input_buffer = path
    output: list[str] = []
    while input_buffer:
        if input_buffer.startswith("../"):
            input_buffer = input_buffer[3:]
        elif input_buffer.startswith("./"):
            input_buffer = input_buffer[2:]
        elif input_buffer.startswith("/./"):
            input_buffer = f"/{input_buffer[3:]}"
        elif input_buffer == "/.":
            input_buffer = "/"
        elif input_buffer.startswith("/../"):
            input_buffer = f"/{input_buffer[4:]}"
            if output:
                output.pop()
        elif input_buffer == "/..":
            input_buffer = "/"
            if output:
                output.pop()
        elif input_buffer in {".", ".."}:
            input_buffer = ""
        else:
            end = input_buffer.find("/", 1) if input_buffer.startswith("/") else input_buffer.find("/")
            if end == -1:
                output.append(input_buffer)
                input_buffer = ""
            else:
                output.append(input_buffer[:end])
                input_buffer = input_buffer[end:]
    result = "".join(output)
    if path.startswith("/") and not result.startswith("/"):
        result = f"/{result}"
    return result or "/"


def validate_discovery_hostname_static(hostname: str) -> DiscoveryTargetSafety:
    """Reject localhost/private/metadata/internal host forms without DNS."""
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return DiscoveryTargetSafety(False, "missing_hostname", None, False)

    if (
        host in LOCALHOST_HOSTS
        or host.endswith(".localhost")
        or host == "local"
        or host.endswith(".local")
        or host == "internal"
        or host.endswith(".internal")
        or host == "home.arpa"
        or host.endswith(".home.arpa")
        or host in METADATA_HOSTS
        or host in RESERVED_HOSTS
        or any(host.endswith(suffix) for suffix in RESERVED_SUFFIXES)
    ):
        return DiscoveryTargetSafety(False, "unsafe_hostname", host, False)

    try:
        ip = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return DiscoveryTargetSafety(True, None, host, False)

    if _is_unsafe_ip(ip):
        return DiscoveryTargetSafety(False, "unsafe_ip", host, False)
    return DiscoveryTargetSafety(True, None, host, False)


def validate_discovery_target_safety(
    hostname: str,
    *,
    resolver: DiscoveryDnsResolver | None = None,
    require_dns: bool = False,
) -> DiscoveryTargetSafety:
    """Validate discovery target safety.

    Static checks always run. DNS runs only when ``require_dns`` is True.
    When DNS is required, ``resolver`` must be provided by the caller — there is
    no production fake-resolver fallback and this function never performs real DNS
    by itself. Injected resolvers are for orchestration/tests.

    Later retrieval must revalidate every connection/redirect; results here are
    not cached forever and do not prevent DNS rebinding at fetch time.
    """
    static = validate_discovery_hostname_static(hostname)
    if not static.is_safe:
        return static

    host = static.hostname or hostname
    # IP literals need no DNS.
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        return DiscoveryTargetSafety(True, None, host, False, 0)

    if not require_dns:
        return DiscoveryTargetSafety(True, None, host, False, 0)

    if resolver is None:
        return DiscoveryTargetSafety(False, "dns_resolution_failed", host, True, 0)

    try:
        addresses = list(resolver(host))
    except Exception:
        return DiscoveryTargetSafety(False, "dns_resolution_failed", host, True, 0)

    if not addresses:
        return DiscoveryTargetSafety(False, "dns_resolution_failed", host, True, 0)

    safe_count = 0
    unsafe_count = 0
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return DiscoveryTargetSafety(False, "dns_resolution_failed", host, True, len(addresses))
        if _is_unsafe_ip(ip):
            unsafe_count += 1
        else:
            safe_count += 1

    if unsafe_count and safe_count:
        return DiscoveryTargetSafety(False, "mixed_public_private_dns", host, True, len(addresses))
    if unsafe_count:
        return DiscoveryTargetSafety(False, "unsafe_ip", host, True, len(addresses))
    return DiscoveryTargetSafety(True, None, host, True, len(addresses))


def classify_discovery_source(
    *,
    canonical_url: str | None = None,
    hostname: str | None = None,
    path: str | None = None,
    title: str | None = None,
    snippet: str | None = None,
    is_valid: bool = True,
    is_safe: bool = True,
    error_code: str | None = None,
) -> DiscoverySourceClassification:
    """Conservative initial source classification. Never qualifies facilities."""
    if not is_valid or not is_safe or error_code:
        return DiscoverySourceClassification(
            CrawlNodeSourceClassification.IRRELEVANT.value,
            "invalid_or_unsafe_target",
            ("unsafe_or_invalid",),
        )

    host = (hostname or "").lower().rstrip(".")
    url = canonical_url or ""
    path_value = path if path is not None else urlsplit(url).path or "/"
    path_lower = path_value.lower()
    title_l = (title or "").casefold()
    snippet_l = (snippet or "").casefold()
    combined = f"{title_l} {snippet_l} {path_lower}"

    if path_lower.endswith(".pdf") or ".pdf?" in f"{path_lower}?":
        return DiscoverySourceClassification(
            CrawlNodeSourceClassification.PDF.value,
            "pdf_extension",
            ("path_pdf",),
        )

    if _host_matches(host, SEARCH_ENGINE_HOST_SUFFIXES):
        return DiscoverySourceClassification(
            CrawlNodeSourceClassification.IRRELEVANT.value,
            "search_engine_result_url",
            ("search_engine_host",),
        )

    if _host_matches(host, SOCIAL_HOST_SUFFIXES):
        return DiscoverySourceClassification(
            CrawlNodeSourceClassification.SOCIAL_PROFILE.value,
            "social_host",
            ("social_domain",),
        )

    if _is_government_host(host) or any(token in combined for token in ("ministry of health", "ministère")):
        return DiscoverySourceClassification(
            CrawlNodeSourceClassification.GOVERNMENT_SOURCE.value,
            "government_domain",
            ("government_signal",),
        )

    if any(token in path_lower for token in REGISTRY_PATH_TOKENS) or any(
        token in combined for token in ("professional registry", "licence register", "license register")
    ):
        # Individual profile under a registry/directory beats bare registry when path is profile-like.
        if any(token in path_lower for token in PROFILE_PATH_TOKENS):
            return DiscoverySourceClassification(
                CrawlNodeSourceClassification.FACILITY_PROFILE.value,
                "directory_facility_profile",
                ("registry_host_or_path", "profile_path"),
            )
        return DiscoverySourceClassification(
            CrawlNodeSourceClassification.REGISTRY.value,
            "registry_signal",
            ("registry_path_or_text",),
        )

    if _host_matches(host, DIRECTORY_HOST_SUFFIXES) or any(token in path_lower for token in DIRECTORY_PATH_TOKENS):
        if any(token in path_lower for token in PROFILE_PATH_TOKENS):
            return DiscoverySourceClassification(
                CrawlNodeSourceClassification.FACILITY_PROFILE.value,
                "directory_facility_profile",
                ("directory_signal", "profile_path"),
            )
        return DiscoverySourceClassification(
            CrawlNodeSourceClassification.DIRECTORY.value,
            "directory_signal",
            ("directory_host_or_path",),
        )

    if _host_matches(host, COMMERCIAL_LISTING_HOST_SUFFIXES) or "commercial listing" in combined:
        return DiscoverySourceClassification(
            CrawlNodeSourceClassification.COMMERCIAL_LISTING.value,
            "commercial_listing_signal",
            ("commercial_listing_host_or_text",),
        )

    if (
        path_lower.rstrip("/") in {p.rstrip("/") for p in OFFICIAL_PATHS}
        or path_lower in OFFICIAL_PATHS
    ) and (
        "official site" in title_l
        or "official website" in title_l
        or "site officiel" in title_l
    ):
        if not (
            _host_matches(host, DIRECTORY_HOST_SUFFIXES)
            or _host_matches(host, COMMERCIAL_LISTING_HOST_SUFFIXES)
            or _host_matches(host, SOCIAL_HOST_SUFFIXES)
            or _host_matches(host, SEARCH_ENGINE_HOST_SUFFIXES)
        ):
            return DiscoverySourceClassification(
                CrawlNodeSourceClassification.OFFICIAL_FACILITY_SITE.value,
                "official_site_signal",
                ("official_title", "standalone_site_path"),
            )

    if _host_matches(host, SUPPORTING_HOST_SUFFIXES) or any(
        token in path_lower for token in ("/news/", "/article/", "/research/", "/study/")
    ):
        return DiscoverySourceClassification(
            CrawlNodeSourceClassification.SUPPORTING_SOURCE.value,
            "supporting_evidence_signal",
            ("supporting_host_or_path",),
        )

    return DiscoverySourceClassification(
        CrawlNodeSourceClassification.UNCLASSIFIED.value,
        "insufficient_evidence",
        (),
    )


def build_canonical_url_hash_payload(canonical_url: str) -> dict[str, str]:
    """Versioned identity payload for crawl-node ``canonical_url_hash``."""
    return {
        "schema": CANONICAL_URL_HASH_SCHEMA,
        "canonical_url": canonical_url,
    }


def compute_canonical_url_hash(canonical_url: str) -> str:
    """Hash via ``sha256_hex(payload_dict)`` — never pass pre-canonicalized bytes."""
    return sha256_hex(build_canonical_url_hash_payload(canonical_url))


def _invalid(original: str, error_code: str) -> DiscoveryUrlCanonicalization:
    return DiscoveryUrlCanonicalization(
        original_url=original if isinstance(original, str) else "",
        canonical_url=None,
        scheme=None,
        hostname=None,
        normalized_domain=None,
        port=None,
        path=None,
        query=None,
        tracking_removed=False,
        removed_tracking_params=(),
        is_valid=False,
        is_statically_safe=False,
        error_code=error_code,
    )


def _format_netloc(hostname: str, scheme: str, port: int | None) -> str:
    try:
        ip = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        host_out = hostname
    else:
        host_out = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed

    if port is None:
        return host_out
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return host_out
    return f"{host_out}:{port}"


def _is_unsafe_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and _is_unsafe_ip(mapped):
        return True
    return bool(
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip in METADATA_IPS
    )


def _host_matches(hostname: str, suffixes: Sequence[str]) -> bool:
    host = hostname.lower().rstrip(".")
    for suffix in suffixes:
        if host == suffix or host.endswith(f".{suffix}"):
            return True
    return False


def _is_government_host(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    if host.endswith(".gov") or ".gov." in host:
        return True
    if host.endswith(".mil") or ".mil." in host:
        return True
    for marker in GOVERNMENT_HOST_MARKERS:
        if marker.startswith(".") and not marker.endswith("."):
            if host.endswith(marker) or host == marker.lstrip("."):
                return True
        elif marker in host:
            return True
    return False


def _has_internal_whitespace(value: str) -> bool:
    # Allow a single leading/trailing strip already applied; reject embedded spaces.
    return any(ch.isspace() for ch in value)
