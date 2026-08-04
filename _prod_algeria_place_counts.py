"""Detailed Algeria place counts from production API."""

from __future__ import annotations

import json
import urllib.request
from collections import Counter

BASE = "https://multiverdict.tech/api/v1"
RUN_ID = "df3918eb-60e5-40e5-8880-e69b411fd6e6"


def req(method, path, token=None, org_id=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org_id:
        headers["X-Org-Id"] = org_id
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read().decode()
        return json.loads(raw) if raw else None


def main() -> None:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]

    counts: Counter[str] = Counter()
    offset = 0
    api_total = 0
    while True:
        page = req(
            "GET",
            f"/maps/runs/{RUN_ID}/places/paged?limit=500&offset={offset}",
            token=token,
            org_id=org_id,
        )
        items = page.get("items") or []
        api_total = int(page.get("total") or api_total or 0)
        if not items:
            break
        for place in items:
            counts["total"] += 1
            counts[f"client_eligibility:{place.get('client_eligibility')}"] += 1
            counts[f"lifecycle_status:{place.get('lifecycle_status')}"] += 1
            counts[f"enrichment_status:{place.get('enrichment_status')}"] += 1
            counts[f"is_relevant:{place.get('is_relevant')}"] += 1
            has_website = bool((place.get("official_website") or place.get("raw_website") or "").strip())
            counts[f"has_website:{has_website}"] += 1
        offset += len(items)
        if len(items) < 500:
            break

    print("=== ALGERIA PLACE COUNTS ===")
    print(f"api_total: {api_total}")
    print(f"fetched: {counts['total']}")
    for key in sorted(counts):
        if key == "total":
            continue
        print(f"{key}: {counts[key]}")


if __name__ == "__main__":
    main()
