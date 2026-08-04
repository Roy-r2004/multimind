"""Check if primary extraction fields appear on completed places."""

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

    with_conf = 0
    samples = []
    offset = 0
    while True:
        page = req("GET", f"/maps/runs/{RUN}/places/paged?limit=500&offset={offset}", token, org_id)
        for place in page.get("items") or []:
            if place.get("enrichment_status") != "completed" or not place.get("is_relevant"):
                continue
            detail = req("GET", f"/maps/runs/{RUN}/places/{place['id']}", token, org_id)
            conf = detail.get("classification_confidence")
            err = detail.get("enrichment_error_message")
            if conf is not None or err:
                with_conf += 1
            if len(samples) < 5:
                samples.append(
                    {
                        "name": place.get("canonical_name"),
                        "confidence": conf,
                        "error": err,
                        "addictions": place.get("addictions_treated"),
                        "facility_type": place.get("facility_type"),
                    }
                )
        offset += len(page.get("items") or [])
        if len(page.get("items") or []) < 500:
            break

    print(f"completed_with_confidence_or_error={with_conf}")
    print(json.dumps(samples, indent=2))


if __name__ == "__main__":
    main()
