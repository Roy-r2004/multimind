"""Phase B2: deterministic tel/mailto/JSON-LD/address extraction from HTML."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urlsplit

PHONE_RE = re.compile(
    r"(?<![\w@])(?:\+|00)?[\d][\d\s()./-]{6,}\d",
)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


@dataclass
class ExtractedContact:
    contact_type: str  # phone | email | website
    value: str
    source: str  # tel_href | mailto_href | regex | json_ld
    evidence_quote: str


@dataclass
class ExtractedAddress:
    value: str
    source: str
    evidence_quote: str
    structured: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContactLocationExtraction:
    phones: list[ExtractedContact] = field(default_factory=list)
    emails: list[ExtractedContact] = field(default_factory=list)
    websites: list[ExtractedContact] = field(default_factory=list)
    addresses: list[ExtractedAddress] = field(default_factory=list)


class _HrefCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for key, value in attrs:
            if key.lower() == "href" and value:
                self.hrefs.append(value)


def extract_contacts_and_addresses(html: str, *, page_url: str | None = None) -> ContactLocationExtraction:
    result = ContactLocationExtraction()
    raw = html or ""

    collector = _HrefCollector()
    try:
        collector.feed(raw)
    except Exception:
        collector.hrefs = []

    for href in collector.hrefs:
        lower = href.strip().casefold()
        if lower.startswith("tel:"):
            value = unquote(href.split(":", 1)[1]).strip()
            if value:
                result.phones.append(
                    ExtractedContact("phone", value, "tel_href", href[:240])
                )
        elif lower.startswith("mailto:"):
            value = unquote(href.split(":", 1)[1]).split("?")[0].strip()
            if value:
                result.emails.append(
                    ExtractedContact("email", value, "mailto_href", href[:240])
                )

    # Strip scripts/styles for regex text scan but keep header/footer text.
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    for match in PHONE_RE.finditer(text):
        value = match.group(0).strip()
        if _looks_like_phone(value):
            result.phones.append(ExtractedContact("phone", value, "regex", value[:240]))
    for match in EMAIL_RE.finditer(text):
        value = match.group(0).strip()
        result.emails.append(ExtractedContact("email", value, "regex", value[:240]))

    for block in JSON_LD_RE.findall(raw):
        _consume_json_ld(block, result)

    if page_url:
        host = urlsplit(page_url).hostname
        if host:
            result.websites.append(
                ExtractedContact("website", page_url, "page_url", page_url[:240])
            )

    result.phones = _dedupe_contacts(result.phones)
    result.emails = _dedupe_contacts(result.emails)
    result.websites = _dedupe_contacts(result.websites)
    result.addresses = _dedupe_addresses(result.addresses)
    return result


def _consume_json_ld(block: str, result: ContactLocationExtraction) -> None:
    try:
        data = json.loads(block)
    except Exception:
        return
    nodes = data if isinstance(data, list) else [data]
    for node in nodes:
        if not isinstance(node, dict):
            continue
        graph = node.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                if isinstance(item, dict):
                    _from_schema_node(item, result)
        else:
            _from_schema_node(node, result)


def _from_schema_node(node: dict[str, Any], result: ContactLocationExtraction) -> None:
    telephone = node.get("telephone") or node.get("phone")
    if isinstance(telephone, str) and telephone.strip():
        result.phones.append(
            ExtractedContact("phone", telephone.strip(), "json_ld", telephone.strip()[:240])
        )
    email = node.get("email")
    if isinstance(email, str) and email.strip():
        result.emails.append(
            ExtractedContact("email", email.strip(), "json_ld", email.strip()[:240])
        )
    url = node.get("url")
    if isinstance(url, str) and url.startswith("http"):
        result.websites.append(ExtractedContact("website", url, "json_ld", url[:240]))

    address = node.get("address")
    if isinstance(address, dict):
        parts = [
            address.get("streetAddress"),
            address.get("postalCode"),
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        value = ", ".join(str(part).strip() for part in parts if part)
        if value:
            result.addresses.append(
                ExtractedAddress(
                    value=value,
                    source="json_ld",
                    evidence_quote=value[:240],
                    structured={k: v for k, v in address.items() if isinstance(v, (str, int, float))},
                )
            )
    elif isinstance(address, str) and address.strip():
        result.addresses.append(
            ExtractedAddress(value=address.strip(), source="json_ld", evidence_quote=address.strip()[:240])
        )


def _looks_like_phone(value: str) -> bool:
    digits = sum(ch.isdigit() for ch in value)
    return 7 <= digits <= 15


def _dedupe_contacts(items: list[ExtractedContact]) -> list[ExtractedContact]:
    seen: set[str] = set()
    out: list[ExtractedContact] = []
    for item in items:
        key = f"{item.contact_type}:{item.value.casefold()}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _dedupe_addresses(items: list[ExtractedAddress]) -> list[ExtractedAddress]:
    seen: set[str] = set()
    out: list[ExtractedAddress] = []
    for item in items:
        key = item.value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
