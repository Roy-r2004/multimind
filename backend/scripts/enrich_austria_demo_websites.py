"""Enrich Austria census demo fixture with website URLs.

Strategy:
1. Derive https://domain from non-generic facility emails (high confidence).
2. For remaining gaps, Serper-search \"{name} Austria official website\".

Usage (from repo root or backend/)::

    python -m scripts.enrich_austria_demo_websites
    python -m scripts.enrich_austria_demo_websites --serper-limit 80
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "austria_census_demo.json"
GENERIC_EMAIL_DOMAINS = {
    "gmail.com",
    "yahoo.com",
    "hotmail.com",
    "outlook.com",
    "gmx.at",
    "gmx.net",
    "aon.at",
    "chello.at",
    "googlemail.com",
    "proton.me",
    "icloud.com",
    "live.com",
    "msn.com",
}
DIRECTORY_HOST_MARKERS = (
    "docfinder",
    "herold",
    "netdoktor",
    "facebook.",
    "instagram.",
    "linkedin.",
    "wikipedia.",
    "yelp.",
    "google.",
    "bing.",
    "youtube.",
    "gesundheit.gv.at",
    "sozialministerium",
    "willhaben.",
    "firmenabc.",
    "bmeia.gv.at",
    "reiseinformation",
)


def _load_env_files() -> None:
    roots = [
        Path(__file__).resolve().parents[2] / ".env",
        Path(__file__).resolve().parents[1] / ".env",
    ]
    for path in roots:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            os.environ.setdefault(key, value)


def _domain_from_email(email: str) -> str | None:
    match = re.search(r"@([^>\s]+)$", email.strip().lower())
    if not match:
        return None
    domain = match.group(1).strip(".")
    if not domain or domain in GENERIC_EMAIL_DOMAINS:
        return None
    if domain.endswith(".gv.at") and "soziale" not in domain:
        # government mailboxes are rarely a facility homepage
        return None
    return domain


def _normalize_website(url: str) -> str | None:
    text = (url or "").strip()
    if not text:
        return None
    if not text.startswith(("http://", "https://")):
        text = "https://" + text.lstrip("/")
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        return None
    if any(marker in host for marker in DIRECTORY_HOST_MARKERS):
        return None
    path = parsed.path or "/"
    return f"https://{host}{path}".rstrip("/") + ("/" if path in {"", "/"} else "")


def _pick_serper_url(organic: list[dict[str, Any]], facility_name: str) -> str | None:
    name_tokens = {
        token
        for token in re.split(r"[^a-z0-9äöüß]+", facility_name.casefold())
        if len(token) >= 4
    }
    ranked: list[tuple[int, str]] = []
    for row in organic:
        url = _normalize_website(str(row.get("link") or ""))
        if not url:
            continue
        host = (urlparse(url).hostname or "").lower()
        title = str(row.get("title") or "").casefold()
        snippet = str(row.get("snippet") or "").casefold()
        score = 0
        if host.endswith(".at"):
            score += 3
        if any(token in host for token in name_tokens):
            score += 4
        if any(token in title or token in snippet for token in name_tokens):
            score += 2
        if "reha" in host or "sucht" in host or "therapie" in host or "klinik" in host:
            score += 1
        ranked.append((score, url))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, best_url = ranked[0]
    return best_url if best_score >= 3 else None


async def _serper_lookup(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    base_url: str,
    facility: dict[str, Any],
) -> str | None:
    place_bits = [
        facility.get("primary_city"),
        facility.get("primary_region"),
        (facility.get("primary_address") or "").split(",")[-1].strip()
        if facility.get("primary_address")
        else None,
    ]
    place = " ".join(bit for bit in place_bits if bit)[:80]
    query = f'{facility["canonical_name"]} {place} Österreich offizielle Website'.strip()
    response = await client.post(
        base_url,
        headers={"Content-Type": "application/json", "X-API-KEY": api_key},
        json={"q": query, "num": 5, "gl": "at", "hl": "de"},
    )
    if response.status_code in {401, 403}:
        raise RuntimeError("Serper auth failed")
    if response.status_code == 429:
        await asyncio.sleep(2.0)
        return None
    response.raise_for_status()
    payload = response.json()
    return _pick_serper_url(list(payload.get("organic") or []), facility["canonical_name"])


def _ensure_website_contact(
    contacts: list[dict[str, Any]],
    *,
    facility_id: str,
    website: str,
    confidence: float,
) -> None:
    for contact in contacts:
        if contact.get("facility_id") != facility_id:
            continue
        if contact.get("contact_type") == "website" and (
            contact.get("value") or ""
        ).rstrip("/") == website.rstrip("/"):
            return
    now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    contacts.append(
        {
            "facility_id": facility_id,
            "contact_type": "website",
            "label": None,
            "value": website,
            "normalized_value": website,
            "is_primary": 1,
            "available_24_7": 0,
            "verification_status": "enriched_for_demo",
            "confidence_score": confidence,
            "is_mock": 0,
            "created_at": now,
            "id": str(uuid4()),
        }
    )


async def enrich(*, serper_limit: int, dry_run: bool) -> dict[str, int]:
    _load_env_files()
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    facilities: list[dict[str, Any]] = data["facilities"]
    contacts: list[dict[str, Any]] = data.setdefault("contacts", [])
    contacts_by_facility: dict[str, list[dict[str, Any]]] = {}
    for contact in contacts:
        contacts_by_facility.setdefault(contact["facility_id"], []).append(contact)

    stats = {
        "already_had": 0,
        "from_email": 0,
        "from_serper": 0,
        "still_missing": 0,
        "serper_attempts": 0,
    }

    for facility in facilities:
        if facility.get("primary_website"):
            stats["already_had"] += 1
            continue
        email_domain = None
        for contact in contacts_by_facility.get(facility["id"], []):
            if contact.get("contact_type") != "email":
                continue
            email_domain = _domain_from_email(str(contact.get("value") or ""))
            if email_domain:
                break
        if email_domain:
            website = _normalize_website(f"https://{email_domain}/")
            if website:
                facility["primary_website"] = website
                _ensure_website_contact(
                    contacts,
                    facility_id=facility["id"],
                    website=website,
                    confidence=0.72,
                )
                stats["from_email"] += 1

    api_key = os.environ.get("SERPER_API_KEY", "").strip()
    base_url = os.environ.get(
        "SERPER_SEARCH_BASE_URL", "https://google.serper.dev/search"
    ).strip()
    missing = [facility for facility in facilities if not facility.get("primary_website")]
    if api_key and serper_limit > 0 and missing:
        async with httpx.AsyncClient(timeout=20.0) as client:
            for facility in missing[:serper_limit]:
                stats["serper_attempts"] += 1
                try:
                    website = await _serper_lookup(
                        client, api_key=api_key, base_url=base_url, facility=facility
                    )
                except Exception as exc:  # noqa: BLE001 - enrich script should continue
                    print(f"serper_failed name={facility['canonical_name']!r} err={exc}")
                    website = None
                if website:
                    facility["primary_website"] = website
                    _ensure_website_contact(
                        contacts,
                        facility_id=facility["id"],
                        website=website,
                        confidence=0.6,
                    )
                    stats["from_serper"] += 1
                await asyncio.sleep(0.25)

    stats["still_missing"] = sum(
        1 for facility in facilities if not facility.get("primary_website")
    )
    data["website_enrichment"] = {
        "enriched_at": datetime.now(UTC).isoformat(),
        "stats": stats,
    }
    if not dry_run:
        FIXTURE_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serper-limit",
        type=int,
        default=80,
        help="Max Serper lookups after email-domain enrichment (0 disables).",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    started = time.time()
    stats = asyncio.run(enrich(serper_limit=args.serper_limit, dry_run=args.dry_run))
    print(json.dumps({"elapsed_s": round(time.time() - started, 1), **stats}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
