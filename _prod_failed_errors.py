"""Fetch enrichment error messages from failed places."""

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


def main() -> None:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]

    failed = []
    offset = 0
    while len(failed) < 5:
        page = req("GET", f"/maps/runs/{RUN}/places/paged?limit=500&offset={offset}", token, org_id)
        for place in page.get("items") or []:
            if place.get("enrichment_status") == "failed":
                detail = req(
                    "GET",
                    f"/maps/runs/{RUN}/places/{place['id']}",
                    token,
                    org_id,
                )
                failed.append(
                    {
                        "name": place.get("canonical_name"),
                        "status": place.get("enrichment_status"),
                        "error": detail.get("enrichment_error_message"),
                    }
                )
                if len(failed) >= 5:
                    break
        offset += len(page.get("items") or [])
        if len(page.get("items") or []) < 500:
            break

    print(json.dumps(failed, indent=2))


if __name__ == "__main__":
    main()
