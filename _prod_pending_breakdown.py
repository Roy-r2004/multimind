"""Break down pending/failed by relevance for Algeria resume."""

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
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode() or "null")


session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
token, org_id = session["access_token"], session["organization"]["id"]

keys = list(
    req("GET", f"/maps/runs/{RUN}/places/paged?limit=1&offset=0", token, org_id)["items"][0].keys()
)
print("place_fields_has_pipeline=", "enrichment_pipeline_state" in keys)
print("sample_keys=", sorted(keys)[:40], "...")

pending_rel = Counter()
failed_rel = Counter()
pending_life = Counter()
failed_life = Counter()
samples = {"pending_relevant": [], "failed_relevant": [], "pending_irrelevant": []}
offset = 0
while True:
    page = req("GET", f"/maps/runs/{RUN}/places/paged?limit=500&offset={offset}", token, org_id)
    items = page.get("items") or []
    if not items:
        break
    for p in items:
        st = p.get("enrichment_status")
        rel = bool(p.get("is_relevant"))
        life = p.get("lifecycle_status") or "?"
        if st == "pending":
            pending_rel[rel] += 1
            pending_life[life] += 1
            bucket = "pending_relevant" if rel else "pending_irrelevant"
            if len(samples[bucket]) < 3:
                samples[bucket].append(
                    {
                        "id": p.get("id"),
                        "name": p.get("canonical_name") or p.get("raw_name"),
                        "life": life,
                        "elig": p.get("client_eligibility"),
                        "pipe": p.get("enrichment_pipeline_state"),
                        "attempts": p.get("enrichment_attempts"),
                    }
                )
        if st == "failed":
            failed_rel[rel] += 1
            failed_life[life] += 1
            if rel and len(samples["failed_relevant"]) < 5:
                samples["failed_relevant"].append(
                    {
                        "id": p.get("id"),
                        "name": p.get("canonical_name") or p.get("raw_name"),
                        "life": life,
                        "elig": p.get("client_eligibility"),
                        "pipe": p.get("enrichment_pipeline_state"),
                        "attempts": p.get("enrichment_attempts"),
                        "err": (p.get("enrichment_error_message") or "")[:120],
                    }
                )
    offset += len(items)
    if len(items) < 500:
        break

print("pending_by_relevant=", dict(pending_rel))
print("failed_by_relevant=", dict(failed_rel))
print("pending_lifecycle=", dict(pending_life))
print("failed_lifecycle=", dict(failed_life))
print(json.dumps(samples, indent=2))
