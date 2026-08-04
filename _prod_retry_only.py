"""Retry enrichment only."""

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
        return json.loads(response.read().decode())


session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
print("Retry:", req("POST", f"/maps/runs/{RUN}/retry-enrichment", session["access_token"], session["organization"]["id"]))
