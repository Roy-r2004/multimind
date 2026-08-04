"""Sample enriched place fields from Algeria production run."""

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

    elig = Counter()
    source = Counter()
    has_addictions = 0
    has_languages = 0
    has_price = 0
    completed_relevant = 0
    samples = []

    offset = 0
    while True:
        page = req("GET", f"/maps/runs/{RUN}/places/paged?limit=500&offset={offset}", token, org_id)
        items = page.get("items") or []
        for place in items:
            if place.get("enrichment_status") != "completed" or not place.get("is_relevant"):
                continue
            completed_relevant += 1
            elig[place.get("client_eligibility") or "?"] += 1
            source[place.get("enrichment_extraction_source") or "?"] += 1
            if place.get("addictions_treated"):
                has_addictions += 1
            if place.get("languages_spoken"):
                has_languages += 1
            if place.get("price_range"):
                has_price += 1
            if len(samples) < 5:
                samples.append(
                    {
                        k: place.get(k)
                        for k in [
                            "canonical_name",
                            "client_eligibility",
                            "lifecycle_status",
                            "addictions_treated",
                            "languages_spoken",
                            "price_range",
                            "facility_type",
                            "enrichment_extraction_source",
                            "enrichment_error_message",
                        ]
                    }
                )
        offset += len(items)
        if len(items) < 500:
            break

    export = req("GET", f"/maps/runs/{RUN}/export-summary", token, org_id)
    print(f"completed_relevant={completed_relevant}")
    print("client_eligibility:", dict(elig))
    print("extraction_source:", dict(source))
    print(f"has_addictions={has_addictions} has_languages={has_languages} has_price={has_price}")
    print("samples:", json.dumps(samples, indent=2))
    print("export:", json.dumps(export, indent=2))


if __name__ == "__main__":
    main()
