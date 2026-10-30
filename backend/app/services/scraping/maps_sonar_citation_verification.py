"""Verifies a Sonar web-search citation's source_url actually, reachably,
supports the claim it is cited for — before any evidence from it is ever
trusted to confirm an eligibility-critical field (inpatient_treatment,
residential_accommodation, medical_detox).

Perplexity Sonar has live web search, but its own citations are never
trusted blindly here: a model can hallucinate a URL, cite a real page that
doesn't actually mention this facility, or confuse a similarly-named
organization / a different branch. This module fetches the cited URL
through the SAME bounded, safety-guarded crawler already used everywhere
else in this pipeline (robots.txt, timeouts, blocked-IP checks — see
``maps_website_crawl_service``) and confirms the facility's own identity
(name — including non-Latin scripts, via the same transliteration/
diacritic-folding already used for official-website matching — or phone
number) actually appears in the fetched content.

A failed verification (unreachable, timed out, or simply doesn't mention
this facility) returns ``False`` — never raises, and never means "this
facility is ineligible". The caller (``maps_sonar_classify.py``) treats a
``False`` result as "no usable evidence", which is what normally happens
whenever evidence is merely missing: the field stays unresolved and the
facility stays in review, exactly as it would with no citation at all.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services.scraping.maps_place_website_resolution import _fold
from app.services.scraping.maps_website_crawl_service import (
    MapsWebsiteCrawlError,
    maps_website_crawl_service,
)

logger = logging.getLogger(__name__)

_MIN_NAME_TOKEN_LENGTH = 4
_VERIFICATION_EXCERPT_MAX_CHARS = 20_000


def _identity_name_tokens(place: Any) -> set[str]:
    name = _fold(
        getattr(place, "canonical_name", None) or getattr(place, "raw_name", None) or ""
    )
    return {tok for tok in re.findall(r"[a-z0-9]+", name) if len(tok) >= _MIN_NAME_TOKEN_LENGTH}


def _phone_last_digits(phone: str | None, *, digits: int = 7) -> str:
    return re.sub(r"\D", "", phone or "")[-digits:]


async def verify_citation_refers_to_place(
    db: Any,
    *,
    place: Any,
    source_url: str,
) -> bool:
    """Fetches ``source_url`` through the existing bounded crawler and
    confirms the facility's own name (folded/transliterated, so this works
    for Cyrillic, accented Latin, etc.) or phone number actually appears in
    the fetched content.

    Returns ``False`` on any fetch failure or when nothing ties the page
    back to this specific facility — never raises.
    """
    url = (source_url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False

    try:
        outcome = await maps_website_crawl_service.crawl_website(db, website_url=url)
    except MapsWebsiteCrawlError as exc:
        logger.info("maps_sonar_citation_unreachable url=%s error=%s", url, exc)
        return False
    except Exception as exc:  # noqa: BLE001 — a verification failure is never fatal
        logger.warning("maps_sonar_citation_verify_error url=%s error=%s", url, exc)
        return False

    if outcome.error or not outcome.pages:
        return False

    excerpt = outcome.combined_excerpt(max_chars=_VERIFICATION_EXCERPT_MAX_CHARS)
    if not excerpt:
        return False
    folded_excerpt = _fold(excerpt)

    name_tokens = _identity_name_tokens(place)
    if name_tokens and any(token in folded_excerpt for token in name_tokens):
        return True

    phone_digits = _phone_last_digits(getattr(place, "international_phone_number", None))
    if phone_digits and phone_digits in re.sub(r"\D", "", excerpt):
        return True

    return False


__all__ = ["verify_citation_refers_to_place"]
