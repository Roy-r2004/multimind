"""Inspect currently running / stalled Algeria places and recent movement."""

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
    with urllib.request.urlopen(request, timeout=180) as response:
        raw = response.read().decode()
        return json.loads(raw) if raw else None


def main() -> None:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]

    dash = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org_id)
    print("heartbeat_at:", dash.get("heartbeat_at"))
    print("updated_at:", dash.get("updated_at"))
    print("current_stage:", dash.get("current_stage"))
    quota = dash.get("quota") or dash.get("quota_metrics") or {}
    print("quota:", json.dumps(quota)[:500])

    running = []
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
        for p in items:
            if p.get("enrichment_status") == "running":
                running.append(p)
        offset += len(items)
        if len(items) < 500:
            break

    print(f"running_count={len(running)}")
    for p in running[:10]:
        print(
            json.dumps(
                {
                    "id": p.get("id"),
                    "name": (p.get("canonical_name") or "").encode("ascii", "replace").decode(),
                    "website": p.get("official_website") or p.get("raw_website"),
                    "lifecycle": p.get("lifecycle_status"),
                    "eligibility": p.get("client_eligibility"),
                }
            )
        )


if __name__ == "__main__":
    main()
