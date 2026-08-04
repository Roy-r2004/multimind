"""Find places stuck in enrichment_status=running."""

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


def main() -> None:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]

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
        for place in items:
            if place.get("enrichment_status") == "running":
                running.append(
                    {
                        k: place.get(k)
                        for k in [
                            "id",
                            "name",
                            "enrichment_status",
                            "enrichment_pipeline_state",
                            "official_website",
                            "raw_website",
                            "is_relevant",
                            "lifecycle_status",
                            "updated_at",
                        ]
                    }
                )
        offset += len(items)
        if len(items) < 500:
            break

    print(json.dumps(running, indent=2))


if __name__ == "__main__":
    main()
