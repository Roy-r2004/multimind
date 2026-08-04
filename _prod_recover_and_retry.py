"""Recover and retry Algeria enrichment on production."""

from __future__ import annotations

import json
import time
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


def enrichment_counts(token, org_id):
    counts: Counter[str] = Counter()
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
    return dict(counts)


def main() -> None:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]

    before = enrichment_counts(token, org_id)
    print("Before:", before)

    recover = req("POST", f"/maps/runs/{RUN}/recover-enrichment", token=token, org_id=org_id)
    print("Recover:", json.dumps(recover, indent=2))

    time.sleep(2)

    retry = req("POST", f"/maps/runs/{RUN}/retry-enrichment", token=token, org_id=org_id)
    print("Retry:", json.dumps(retry, indent=2))

    print("\nPolling every 60s for 10 minutes...")
    for i in range(10):
        time.sleep(60)
        counts = enrichment_counts(token, org_id)
        dash = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org_id)
        print(
            f"[{i + 1}/10] enrichment_status={counts} "
            f"stage={dash.get('current_stage')} updated_at={dash.get('updated_at')}"
        )
        if counts.get("pending", 0) == 0 and counts.get("running", 0) == 0:
            print("Enrichment queue drained.")
            break

    after = enrichment_counts(token, org_id)
    export = req("GET", f"/maps/runs/{RUN}/export-summary", token=token, org_id=org_id)
    print("\nFinal enrichment_status:", after)
    print("Export sheets:", json.dumps(export.get("sheets"), indent=2))


if __name__ == "__main__":
    main()
