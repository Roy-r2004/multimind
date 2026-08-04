"""Enqueue next small enrichment batch for Algeria — NO Recover, no reset."""

from __future__ import annotations

import json
import urllib.request

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


session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
token = session["access_token"]
org_id = session["organization"]["id"]
print("Enqueue retry-enrichment (small-batch job, no Recover):")
print(json.dumps(req("POST", f"/maps/runs/{RUN}/retry-enrichment", token, org_id), indent=2))
