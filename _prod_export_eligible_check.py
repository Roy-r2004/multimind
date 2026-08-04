"""Check why places with addictions aren't export-eligible."""

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
token = session["access_token"]
org_id = session["organization"]["id"]

samples = []
offset = 0
while True:
    page = req("GET", f"/maps/runs/{RUN}/places/paged?limit=500&offset={offset}", token, org_id)
    for place in page.get("items") or []:
        if not place.get("addictions_treated"):
            continue
        samples.append(
            {
                "name": place.get("canonical_name"),
                "export_eligible": place.get("export_eligible"),
                "client_eligibility": place.get("client_eligibility"),
                "confidence_score": place.get("confidence_score"),
                "addictions": place.get("addictions_treated"),
                "city": place.get("city_name"),
            }
        )
    offset += len(page.get("items") or [])
    if len(page.get("items") or []) < 500:
        break

print(json.dumps(samples, indent=2))
