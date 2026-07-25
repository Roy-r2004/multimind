"""Phase B1: rank same-domain contact/location/about pages for follow-up retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

CONTACT_PATH_TERMS = (
    "contact",
    "contacts",
    "kontakt",
    "kontakti",
    "contacto",
    "contatto",
    "impressum",
    "imprint",
    "about",
    "about-us",
    "a-propos",
    "uber-uns",
    "ueber-uns",
    "locations",
    "location",
    "branches",
    "branch",
    "filialen",
    "standorte",
    "clinics",
    "centers",
    "centres",
    "admissions",
    "admission",
    "intake",
    "reach-us",
    "find-us",
    "address",
    "adresse",
    "our-locations",
)


@dataclass(frozen=True)
class RankedInternalLink:
    url: str
    anchor_text: str
    score: int
    reason: str


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = next((value for key, value in attrs if key.lower() == "href" and value), None)
        self._href = href
        self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = " ".join("".join(self._text).split())
        self.links.append((self._href, text))
        self._href = None
        self._text = []


def discover_contact_pages(
    *,
    base_url: str,
    html: str,
    max_links: int = 8,
) -> list[RankedInternalLink]:
    """Return same-domain internal links ranked for contact/location discovery."""
    base = urlsplit(base_url)
    if not base.scheme or not base.netloc:
        return []
    parser = _AnchorParser()
    try:
        parser.feed(html or "")
    except Exception:
        return []

    ranked: list[RankedInternalLink] = []
    seen: set[str] = set()
    for href, text in parser.links:
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.scheme not in {"http", "https"}:
            continue
        if parts.netloc.lower() != base.netloc.lower():
            continue
        normalized = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", "", ""))
        if normalized in seen or normalized.rstrip("/") == urlunsplit(
            (base.scheme, base.netloc.lower(), base.path or "/", "", "")
        ).rstrip("/"):
            continue
        seen.add(normalized)
        score, reason = _score_link(parts.path, text)
        if score <= 0:
            continue
        ranked.append(RankedInternalLink(url=normalized, anchor_text=text[:160], score=score, reason=reason))

    ranked.sort(key=lambda item: (-item.score, item.url))
    return ranked[:max_links]


def _score_link(path: str, text: str) -> tuple[int, str]:
    blob = f"{path} {text}".casefold()
    score = 0
    reasons: list[str] = []
    for term in CONTACT_PATH_TERMS:
        if re.search(rf"(^|[^a-z]){re.escape(term)}([^a-z]|$)", blob):
            boost = 30 if term in {"contact", "kontakt", "impressum", "locations", "standorte", "admissions"} else 15
            score += boost
            reasons.append(term)
    if score == 0:
        return 0, ""
    return score, ",".join(reasons[:6])
