"""Call recover-enrichment and print final Algeria counts."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections import Counter

BASE = "https://multiverdict.tech/api/v1"
RUN = "df3918eb-60e5-40e5-8880-e69b411fd6e6"


def req(method, path, token=None, org_id=None, body=None, retries=6):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org_id:
        headers["X-Org-Id"] = org_id
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                raw = response.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            if exc.code in {502, 503, 504} and attempt + 1 < retries:
                time.sleep(20)
                continue
            raise


def counts(token, org_id):
    c: Counter[str] = Counter()
    offset = 0
    while True:
        page = req("GET", f"/maps/runs/{RUN}/places/paged?limit=500&offset={offset}", token, org_id)
        items = page.get("items") or []
        for p in items:
            c[p.get("enrichment_status") or "?"] += 1
        offset += len(items)
        if len(items) < 500:
            break
    return dict(c)


def main() -> None:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token, org_id = session["access_token"], session["organization"]["id"]
    print("Before:", counts(token, org_id))
    print("Recover:", req("POST", f"/maps/runs/{RUN}/recover-enrichment", token, org_id))
    print("After:", counts(token, org_id))
    export = req("GET", f"/maps/runs/{RUN}/export-summary", token, org_id)
    print("Export:", json.dumps(export.get("sheets"), indent=2))


if __name__ == "__main__":
    main()
