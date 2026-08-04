"""Scan all completed places for addictions and facility_type."""

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
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode())


def main() -> None:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]

    status = Counter()
    addictions = 0
    languages = 0
    facility_types = Counter()
    export_eligible = 0
    with_addictions_samples = []

    offset = 0
    while True:
        page = req("GET", f"/maps/runs/{RUN}/places/paged?limit=500&offset={offset}", token, org_id)
        items = page.get("items") or []
        for place in items:
            if not place.get("is_relevant"):
                continue
            status[place.get("enrichment_status") or "?"] += 1
            if place.get("addictions_treated"):
                addictions += 1
                if len(with_addictions_samples) < 3:
                    with_addictions_samples.append(place.get("canonical_name"))
            if place.get("languages_spoken"):
                languages += 1
            if place.get("facility_type"):
                facility_types[place.get("facility_type")] += 1
            if place.get("export_eligible"):
                export_eligible += 1
        offset += len(items)
        if len(items) < 500:
            break

    print("status:", dict(status))
    print(f"with_addictions={addictions} with_languages={languages} export_eligible={export_eligible}")
    print("facility_types:", dict(facility_types.most_common(10)))
    print("samples_with_addictions:", with_addictions_samples)


if __name__ == "__main__":
    main()
