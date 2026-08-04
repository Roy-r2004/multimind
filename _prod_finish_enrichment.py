"""Recover, retry, and verify Algeria enrichment completion."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import Counter

BASE = "https://multiverdict.tech/api/v1"
RUN = "df3918eb-60e5-40e5-8880-e69b411fd6e6"


def req(method, path, token=None, org_id=None, body=None, retries=5):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org_id:
        headers["X-Org-Id"] = org_id
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    last_error = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code in {502, 503, 504} and attempt + 1 < retries:
                time.sleep(15 * (attempt + 1))
                continue
            raise
    raise last_error  # type: ignore[misc]


def enrichment_counts(token, org_id):
    counts: Counter[str] = Counter()
    offset = 0
    while True:
        page = req("GET", f"/maps/runs/{RUN}/places/paged?limit=500&offset={offset}", token, org_id)
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

    print("Before:", enrichment_counts(token, org_id))
    print("Recover:", req("POST", f"/maps/runs/{RUN}/recover-enrichment", token, org_id))
    time.sleep(3)
    print("Retry:", req("POST", f"/maps/runs/{RUN}/retry-enrichment", token, org_id))

    for i in range(15):
        time.sleep(30)
        counts = enrichment_counts(token, org_id)
        pending = counts.get("pending", 0)
        running = counts.get("running", 0)
        completed = counts.get("completed", 0)
        print(f"[{i + 1}/15] completed={completed} pending={pending} running={running}")
        if pending == 0 and running == 0:
            break

    export = req("GET", f"/maps/runs/{RUN}/export-summary", token, org_id)
    dash = req("GET", f"/maps/runs/{RUN}/dashboard", token, org_id)
    print("Final counts:", enrichment_counts(token, org_id))
    print("Export:", json.dumps(export.get("sheets"), indent=2))
    print(
        "Run:",
        dash.get("status"),
        dash.get("current_stage"),
        "places_enriched=",
        dash.get("places_enriched"),
    )


if __name__ == "__main__":
    main()
