"""Phase 4 Slice 2: discovery URL canonicalization, safety, classification, hash."""

from __future__ import annotations

import hashlib
import json

import pytest

from app.db.models import CrawlNodeSourceClassification
from app.services.scraping.blueprint_execution_plan_service import (
    canonical_json_bytes,
    sha256_hex,
)
from app.services.scraping.discovery_url_service import (
    CANONICAL_URL_HASH_SCHEMA,
    TRACKING_QUERY_KEYS,
    build_canonical_url_hash_payload,
    canonicalize_discovery_target,
    classify_discovery_source,
    compute_canonical_url_hash,
    derive_normalized_domain,
    normalize_hostname,
    validate_discovery_target_safety,
)
from app.services.scraping.url_canonicalization import UrlRejected, canonicalize_discovery_url


# --- Canonicalization (1–21) -------------------------------------------------


def test_01_http_https_normalization():
    http = canonicalize_discovery_target("http://Docs.Python.Org/path")
    https = canonicalize_discovery_target("https://Docs.Python.Org/path")
    assert http.is_valid and https.is_valid
    assert http.scheme == "http"
    assert https.scheme == "https"
    assert http.canonical_url.startswith("http://")
    assert https.canonical_url.startswith("https://")


def test_02_uppercase_scheme_and_hostname():
    result = canonicalize_discovery_target("HTTPS://SUB.Domain.ORG/Path")
    assert result.canonical_url == "https://sub.domain.org/Path"
    assert result.hostname == "sub.domain.org"


def test_03_default_port_removal():
    http = canonicalize_discovery_target("http://docs.python.org:80/library")
    https = canonicalize_discovery_target("https://docs.python.org:443/library")
    assert http.canonical_url == "http://docs.python.org/library"
    assert https.canonical_url == "https://docs.python.org/library"
    assert http.port == 80
    assert https.port == 443


def test_04_non_default_port_preservation():
    result = canonicalize_discovery_target("https://docs.python.org:8443/x")
    assert result.canonical_url == "https://docs.python.org:8443/x"
    assert result.port == 8443


def test_05_empty_path_normalization():
    result = canonicalize_discovery_target("https://docs.python.org")
    assert result.path == "/"
    assert result.canonical_url == "https://docs.python.org/"


def test_06_fragment_removal():
    result = canonicalize_discovery_target("https://docs.python.org/page#section")
    assert result.canonical_url == "https://docs.python.org/page"
    assert "#" not in (result.canonical_url or "")


def test_07_dot_segment_normalization():
    result = canonicalize_discovery_target("https://docs.python.org/a/./b/../c")
    assert result.path == "/a/c"
    assert result.canonical_url == "https://docs.python.org/a/c"


def test_08_tracking_parameter_removal():
    result = canonicalize_discovery_target(
        "https://docs.python.org/x?utm_source=a&utm_medium=b&gclid=1&dclid=2"
        "&fbclid=3&msclkid=4&mc_cid=5&mc_eid=6&igshid=7&ref_src=8&ref_url=9&id=42"
    )
    assert result.tracking_removed is True
    assert result.query == "id=42"
    removed = {name.lower() for name in result.removed_tracking_params}
    for expected in (
        "utm_source",
        "utm_medium",
        "gclid",
        "dclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "igshid",
        "ref_src",
        "ref_url",
    ):
        assert expected in removed
    assert TRACKING_QUERY_KEYS  # existing tracking set remains available


def test_09_meaningful_query_preservation():
    result = canonicalize_discovery_target(
        "https://docs.python.org/x?id=1&page=2&lang=en&locale=fr&region=eu"
        "&country=fr&category=clinic&search=q&query=name&facility=ab&profile=cd&document=ef"
    )
    assert result.query is not None
    for key in (
        "id",
        "page",
        "lang",
        "locale",
        "region",
        "country",
        "category",
        "search",
        "query",
        "facility",
        "profile",
        "document",
    ):
        assert f"{key}=" in result.query


def test_10_repeated_meaningful_parameter_preservation():
    result = canonicalize_discovery_target("https://docs.python.org/x?id=1&id=2&tag=a&tag=b")
    assert result.query == "id=1&id=2&tag=a&tag=b"


def test_11_blank_meaningful_parameter_preservation():
    result = canonicalize_discovery_target("https://docs.python.org/x?id=&page=1")
    assert result.query == "id=&page=1"


def test_12_unicode_idn_hostname_handling():
    result = canonicalize_discovery_target("https://münchen.example-hospital.org/path")
    assert result.is_valid
    assert result.hostname is not None
    assert "xn--" in result.hostname
    assert result.canonical_url is not None
    assert result.canonical_url.startswith("https://xn--")


def test_13_ipv6_url_handling():
    result = canonicalize_discovery_target("http://[2001:db8::1]/path")
    assert result.hostname == "2001:db8::1"
    assert result.canonical_url == "http://[2001:db8::1]/path"


def test_14_embedded_credential_rejection():
    result = canonicalize_discovery_target("https://user:pass@docs.python.org/x")
    assert result.is_valid is False
    assert result.error_code == "embedded_credentials"


def test_15_unsupported_scheme_rejection():
    for url in (
        "file:///tmp/x",
        "ftp://docs.python.org/x",
        "data:text/plain,hi",
        "javascript:alert(1)",
        "blob:https://x",
        "chrome://settings",
        "about:blank",
        "gopher://x",
        "ssh://host",
        "mock://source/1",
    ):
        result = canonicalize_discovery_target(url)
        assert result.is_valid is False
        assert result.error_code == "unsupported_scheme"


def test_16_missing_host_rejection():
    result = canonicalize_discovery_target("https:///path")
    assert result.is_valid is False
    assert result.error_code == "missing_hostname"


def test_17_invalid_port_rejection():
    result = canonicalize_discovery_target("https://docs.python.org:99999/x")
    assert result.is_valid is False
    assert result.error_code == "invalid_port"


def test_18_malformed_url_rejection():
    result = canonicalize_discovery_target("https://docs.python.org/x with space")
    assert result.is_valid is False
    assert result.error_code == "invalid_url"


def test_19_deterministic_canonical_result():
    a = canonicalize_discovery_target("HTTPS://Docs.Python.Org:443/a?utm_source=x#y")
    b = canonicalize_discovery_target("HTTPS://Docs.Python.Org:443/a?utm_source=x#y")
    assert a.canonical_url == b.canonical_url == "https://docs.python.org/a"


def test_20_equivalent_tracked_urls_canonicalize_identically():
    a = canonicalize_discovery_target("https://docs.python.org/a?id=1&utm_source=x")
    b = canonicalize_discovery_target("https://docs.python.org/a?utm_campaign=y&id=1&fbclid=z")
    assert a.canonical_url == b.canonical_url


def test_21_distinct_meaningful_query_urls_remain_distinct():
    a = canonicalize_discovery_target("https://docs.python.org/a?id=1")
    b = canonicalize_discovery_target("https://docs.python.org/a?id=2")
    assert a.canonical_url != b.canonical_url


# --- Safety (22–38) ----------------------------------------------------------


def test_22_public_hostname_accepted_without_dns():
    safety = validate_discovery_target_safety("docs.python.org", require_dns=False)
    assert safety.is_safe is True
    assert safety.dns_checked is False
    assert safety.error_code is None


def test_23_localhost_rejected():
    assert validate_discovery_target_safety("localhost").is_safe is False
    assert validate_discovery_target_safety("localhost").error_code == "unsafe_hostname"


def test_24_localhost_subdomain_rejected():
    assert validate_discovery_target_safety("foo.localhost").error_code == "unsafe_hostname"


def test_25_local_internal_home_arpa_rejected():
    for host in ("printer.local", "local", "corp.internal", "internal", "home.arpa", "gw.home.arpa"):
        result = validate_discovery_target_safety(host)
        assert result.is_safe is False
        assert result.error_code == "unsafe_hostname"


def test_26_ipv4_loopback_rejected():
    assert validate_discovery_target_safety("127.0.0.1").error_code == "unsafe_ip"


def test_27_ipv4_private_rejected():
    assert validate_discovery_target_safety("10.0.0.2").error_code == "unsafe_ip"
    assert validate_discovery_target_safety("192.168.1.1").error_code == "unsafe_ip"


def test_28_ipv4_link_local_rejected():
    assert validate_discovery_target_safety("169.254.10.1").error_code == "unsafe_ip"


def test_29_ipv6_loopback_rejected():
    assert validate_discovery_target_safety("::1").error_code == "unsafe_ip"


def test_30_ipv6_unique_local_rejected():
    assert validate_discovery_target_safety("fd12::1").error_code == "unsafe_ip"


def test_31_ipv6_link_local_rejected():
    assert validate_discovery_target_safety("fe80::1").error_code == "unsafe_ip"


def test_32_ipv4_mapped_unsafe_ipv6_rejected():
    assert validate_discovery_target_safety("::ffff:127.0.0.1").error_code == "unsafe_ip"
    assert validate_discovery_target_safety("::ffff:10.0.0.1").error_code == "unsafe_ip"


def test_33_public_ip_accepted():
    # 8.8.8.8 is public; no DNS required for literals.
    safety = validate_discovery_target_safety("8.8.8.8")
    assert safety.is_safe is True
    assert safety.error_code is None


def test_34_injected_resolver_public_accepted():
    safety = validate_discovery_target_safety(
        "docs.python.org",
        require_dns=True,
        resolver=lambda _host: ("93.184.216.34",),
    )
    assert safety.is_safe is True
    assert safety.dns_checked is True
    assert safety.resolved_address_count == 1


def test_35_injected_resolver_private_rejected():
    safety = validate_discovery_target_safety(
        "docs.python.org",
        require_dns=True,
        resolver=lambda _host: ("10.0.0.5",),
    )
    assert safety.is_safe is False
    assert safety.error_code == "unsafe_ip"


def test_36_mixed_public_private_rejected():
    safety = validate_discovery_target_safety(
        "docs.python.org",
        require_dns=True,
        resolver=lambda _host: ("93.184.216.34", "10.0.0.5"),
    )
    assert safety.is_safe is False
    assert safety.error_code == "mixed_public_private_dns"


def test_37_resolver_failure_normalized():
    def boom(_host: str) -> list[str]:
        raise OSError("simulated dns failure")

    safety = validate_discovery_target_safety(
        "docs.python.org",
        require_dns=True,
        resolver=boom,
    )
    assert safety.is_safe is False
    assert safety.error_code == "dns_resolution_failed"
    assert "OSError" not in (safety.error_code or "")


def test_38_no_real_dns_in_tests():
    """require_dns without resolver must fail closed — never fall back to real DNS."""
    safety = validate_discovery_target_safety("docs.python.org", require_dns=True, resolver=None)
    assert safety.is_safe is False
    assert safety.error_code == "dns_resolution_failed"
    assert safety.dns_checked is True


# --- Classification (39–51) --------------------------------------------------


def test_39_pdf_classification():
    result = classify_discovery_source(
        canonical_url="https://docs.python.org/files/report.pdf",
        hostname="docs.python.org",
        path="/files/report.pdf",
    )
    assert result.classification == CrawlNodeSourceClassification.PDF.value
    assert result.reason_code == "pdf_extension"


def test_40_social_profile_classification():
    result = classify_discovery_source(
        canonical_url="https://www.linkedin.com/company/clinic",
        hostname="www.linkedin.com",
        path="/company/clinic",
    )
    assert result.classification == CrawlNodeSourceClassification.SOCIAL_PROFILE.value


def test_41_government_source_classification():
    result = classify_discovery_source(
        canonical_url="https://sante.gouv.fr/annuaire",
        hostname="sante.gouv.fr",
        path="/annuaire",
    )
    assert result.classification == CrawlNodeSourceClassification.GOVERNMENT_SOURCE.value


def test_42_registry_classification():
    result = classify_discovery_source(
        canonical_url="https://licenses.example-health.org/registry/list",
        hostname="licenses.example-health.org",
        path="/registry/list",
        title="Professional licence registry",
    )
    assert result.classification == CrawlNodeSourceClassification.REGISTRY.value


def test_43_directory_classification():
    result = classify_discovery_source(
        canonical_url="https://www.doctolib.fr/directory/clinics",
        hostname="www.doctolib.fr",
        path="/directory/clinics",
    )
    assert result.classification == CrawlNodeSourceClassification.DIRECTORY.value


def test_44_individual_directory_profile_classification():
    result = classify_discovery_source(
        canonical_url="https://www.doctolib.fr/facility/clinique-abc",
        hostname="www.doctolib.fr",
        path="/facility/clinique-abc",
    )
    assert result.classification == CrawlNodeSourceClassification.FACILITY_PROFILE.value
    assert result.reason_code == "directory_facility_profile"


def test_45_commercial_listing_classification():
    result = classify_discovery_source(
        canonical_url="https://www.yelp.com/biz/clinic",
        hostname="www.yelp.com",
        path="/biz/clinic",
    )
    assert result.classification == CrawlNodeSourceClassification.COMMERCIAL_LISTING.value


def test_46_strong_official_site_classification():
    result = classify_discovery_source(
        canonical_url="https://www.clinique-horizon.fr/",
        hostname="www.clinique-horizon.fr",
        path="/",
        title="Clinique Horizon — Official Website",
    )
    assert result.classification == CrawlNodeSourceClassification.OFFICIAL_FACILITY_SITE.value
    assert result.reason_code == "official_site_signal"


def test_47_supporting_source_classification():
    result = classify_discovery_source(
        canonical_url="https://en.wikipedia.org/wiki/Hospital",
        hostname="en.wikipedia.org",
        path="/wiki/Hospital",
    )
    assert result.classification == CrawlNodeSourceClassification.SUPPORTING_SOURCE.value


def test_48_unsafe_invalid_target_classification():
    result = classify_discovery_source(
        is_valid=False,
        is_safe=False,
        error_code="unsafe_ip",
    )
    assert result.classification == CrawlNodeSourceClassification.IRRELEVANT.value
    assert result.reason_code == "invalid_or_unsafe_target"


def test_49_ambiguous_url_remains_unclassified():
    result = classify_discovery_source(
        canonical_url="https://random-blog.example-health.org/posts/hello",
        hostname="random-blog.example-health.org",
        path="/posts/hello",
        title="Hello world",
    )
    assert result.classification == CrawlNodeSourceClassification.UNCLASSIFIED.value


def test_50_classification_priority_is_deterministic():
    # PDF wins over social host when path is a PDF on a social domain.
    result = classify_discovery_source(
        canonical_url="https://www.facebook.com/files/doc.pdf",
        hostname="www.facebook.com",
        path="/files/doc.pdf",
    )
    assert result.classification == CrawlNodeSourceClassification.PDF.value


def test_51_classification_does_not_accept_or_verify_facility():
    result = classify_discovery_source(
        canonical_url="https://www.clinique-horizon.fr/",
        hostname="www.clinique-horizon.fr",
        path="/",
        title="Clinique Horizon — Official Website",
    )
    assert result.classification == CrawlNodeSourceClassification.OFFICIAL_FACILITY_SITE.value
    # Classification result must not expose acceptance/verification semantics.
    assert not hasattr(result, "accepted")
    assert not hasattr(result, "verified")
    assert "accept" not in result.reason_code
    assert "verif" not in result.reason_code


# --- Hash (52–55) ------------------------------------------------------------


def test_52_same_canonical_url_same_hash():
    url = "https://docs.python.org/a?id=1"
    assert compute_canonical_url_hash(url) == compute_canonical_url_hash(url)


def test_53_tracking_only_variants_same_hash():
    a = canonicalize_discovery_target("https://docs.python.org/a?id=1&utm_source=x")
    b = canonicalize_discovery_target("https://docs.python.org/a?fbclid=y&id=1")
    assert a.canonical_url == b.canonical_url
    assert compute_canonical_url_hash(a.canonical_url or "") == compute_canonical_url_hash(b.canonical_url or "")


def test_54_meaningful_query_variants_different_hashes():
    a = compute_canonical_url_hash("https://docs.python.org/a?id=1")
    b = compute_canonical_url_hash("https://docs.python.org/a?id=2")
    assert a != b


def test_55_hash_helper_uses_payload_object_not_bytes():
    payload = build_canonical_url_hash_payload("https://docs.python.org/a")
    assert payload == {
        "schema": CANONICAL_URL_HASH_SCHEMA,
        "canonical_url": "https://docs.python.org/a",
    }
    # Correct: sha256_hex(payload_dict). Incorrect: sha256_hex(canonical_json_bytes(...)).
    assert compute_canonical_url_hash("https://docs.python.org/a") == sha256_hex(payload)
    with pytest.raises(TypeError):
        sha256_hex(canonical_json_bytes(payload))
    expected = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    ).hexdigest()
    assert compute_canonical_url_hash("https://docs.python.org/a") == expected


# --- Legacy compatibility + helpers ------------------------------------------


def test_legacy_canonicalize_discovery_url_still_works():
    canonical = canonicalize_discovery_url(
        "HTTPS://Sub.Domain.org:443/path?utm_source=x&id=42&fbclid=y#section"
    )
    assert canonical.canonical_url == "https://sub.domain.org/path?id=42"
    assert canonical.domain == "sub.domain.org"


def test_legacy_rejects_unsafe_urls():
    for url in [
        "mock://source/1",
        "data:text/plain,hello",
        "file:///tmp/x",
        "javascript:alert(1)",
        "http://localhost/a",
        "http://127.0.0.1/a",
        "http://10.0.0.2/a",
        "http://169.254.169.254/latest",
        "https://facility-001.example.invalid",
        "https://example.com/path",
        "https://user:pass@real.example/path",
    ]:
        with pytest.raises(UrlRejected):
            canonicalize_discovery_url(url)


def test_normalize_hostname_and_domain_helpers():
    host = normalize_hostname("Example.ORG.")
    assert host.hostname == "example.org"
    assert derive_normalized_domain("example.org") == "example.org"
    assert derive_normalized_domain("8.8.8.8") is None


def test_invalid_idn_rejected():
    # unpaired surrogate / invalid IDNA label
    result = normalize_hostname("xn--\uffff")
    assert result.error_code in {"invalid_idn", "malformed_hostname"}
