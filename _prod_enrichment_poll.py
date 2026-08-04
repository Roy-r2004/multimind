"""Poll Algeria enrichment progress with running/completed counts."""

from __future__ import annotations

import json
import urllib.request
from collections import Counter

BASE = "https://multiverdict.tech/api/v1"
RUN = "df3918eb-60e5-40e5-8880-e69b411fd6e6"


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

    detail = req("GET", f"/maps/runs/{RUN}", token=token, org_id=org_id)
    dash = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org_id)

    print("=== RUN ===")
    print(f"status: {detail.get('status')}")
    print(f"places_enriched: {detail.get('places_enriched')}")
    print(f"enrichment_refresh_completed_at: {detail.get('enrichment_refresh_completed_at')}")
    print(f"enrichment_refresh_attempts: {dash.get('enrichment_refresh_attempts')}")
    print(f"current_stage: {dash.get('current_stage')}")
    print(f"campaign_paused: {dash.get('campaign_paused')}")

    state = dash.get("processing_state") or {}
    print(f"processing_state keys: {list(state.keys())}")
    if state.get("enrichment_selection"):
        print("selection:", json.dumps(state["enrichment_selection"], indent=2)[:600])
    if state.get("sonar_fallback_stats"):
        print("sonar:", json.dumps(state["sonar_fallback_stats"], indent=2)[:400])

    quota = dash.get("quota_metrics") or {}
    print(
        "quota: primary_extraction_calls=",
        quota.get("primary_extraction_calls"),
        "sonar_fallback_calls=",
        quota.get("sonar_fallback_calls"),
        "enrichment_calls=",
        quota.get("enrichment_calls"),
        "crawl_requests=",
        quota.get("crawl_requests"),
    )

    counts: Counter[str] = Counter()
    pipeline: Counter[str] = Counter()
    offset = 0
    while True:
        page = req(
            "GET",
            f"/maps/runs/{RUN}/places/paged?limit=500&offset={offset}",
            token=token,
            org_id=org_id,
        )
        items = page.get("items") or []
        if not items:
            break
        for place in items:
            counts[place.get("enrichment_status") or "?"] += 1
        offset += len(items)
        if len(items) < 500:
            break

    print("enrichment_status:", dict(counts))


if __name__ == "__main__":
    main()
