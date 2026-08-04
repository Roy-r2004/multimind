"""Diagnose whether Algeria enrichment actually extracted data."""

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
        return json.loads(response.read().decode())


def main() -> None:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]

    pipeline = Counter()
    source = Counter()
    status = Counter()
    with_data = 0
    without_source = 0
    stale_msg = 0

    offset = 0
    while True:
        page = req("GET", f"/maps/runs/{RUN}/places/paged?limit=500&offset={offset}", token, org_id)
        items = page.get("items") or []
        for place in items:
            if not place.get("is_relevant"):
                continue
            status[place.get("enrichment_status") or "?"] += 1
            pipeline[place.get("enrichment_pipeline_state") or "null"] += 1
            source[place.get("enrichment_extraction_source") or "null"] += 1
            if place.get("enrichment_extraction_source"):
                with_data += 1
            if place.get("enrichment_status") == "completed" and not place.get("enrichment_extraction_source"):
                without_source += 1
            msg = place.get("enrichment_error_message") or ""
            if "stale running" in msg or "manual review" in msg:
                stale_msg += 1
            if place.get("addictions_treated") or place.get("facility_type"):
                with_data += 0  # already counting source
        offset += len(items)
        if len(items) < 500:
            break

    dash = req("GET", f"/maps/runs/{RUN}/dashboard", token, org_id)
    print("enrichment_status (relevant):", dict(status))
    print("pipeline_state:", dict(pipeline))
    print("extraction_source:", dict(source))
    print(f"completed_without_extraction_source={without_source}")
    print(f"stale_finalized={stale_msg}")
    print("quota:", dash.get("quota_metrics"))
    print("sonar:", (dash.get("processing_state") or {}).get("sonar_fallback_stats"))


if __name__ == "__main__":
    main()
