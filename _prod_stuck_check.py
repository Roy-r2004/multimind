"""Check if Algeria run is actively progressing or truly stuck."""

from __future__ import annotations

import json
import time
import urllib.request

BASE = "https://multiverdict.tech/api/v1"
RUN_ID = "df3918eb-60e5-40e5-8880-e69b411fd6e6"


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


def count_websites(token, org_id):
    total_with = 0
    relevant_with = 0
    for offset in range(0, 1500, 500):
        page = req(
            "GET",
            f"/maps/runs/{RUN_ID}/places/paged?limit=500&offset={offset}",
            token=token,
            org_id=org_id,
        )
        for place in page.get("items") or []:
            if (place.get("official_website") or place.get("raw_website") or "").strip():
                total_with += 1
                if place.get("is_relevant"):
                    relevant_with += 1
    return total_with, relevant_with


def main() -> None:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]

    d1 = req("GET", f"/maps/runs/{RUN_ID}/dashboard", token=token, org_id=org_id)
    w1 = count_websites(token, org_id)
    time.sleep(30)
    d2 = req("GET", f"/maps/runs/{RUN_ID}/dashboard", token=token, org_id=org_id)
    w2 = count_websites(token, org_id)

    print("30s progress check:")
    for key in ["status", "current_stage", "updated_at", "places_found", "places_with_website", "places_enriched", "cells_pending"]:
        print(f"  {key}: {d1.get(key)} -> {d2.get(key)}")
    print(f"  places_with_any_website: {w1} -> {w2}")


if __name__ == "__main__":
    main()
