"""Quality assessment of Algeria enrichment output."""

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

    relevant = 0
    elig = Counter()
    lifecycle = Counter()
    facility = Counter()
    has_website = 0
    addictions_n = 0
    languages_n = 0
    price_n = 0
    export_eligible = 0
    confidence_buckets = Counter()
    addiction_focus = Counter()

    offset = 0
    addiction_samples = []
    while True:
        page = req("GET", f"/maps/runs/{RUN}/places/paged?limit=500&offset={offset}", token, org_id)
        items = page.get("items") or []
        for place in items:
            if not place.get("is_relevant"):
                continue
            relevant += 1
            elig[place.get("client_eligibility") or "?"] += 1
            lifecycle[place.get("lifecycle_status") or "?"] += 1
            if place.get("facility_type"):
                facility[place.get("facility_type")] += 1
            if (place.get("official_website") or place.get("raw_website") or "").strip():
                has_website += 1
            if place.get("addictions_treated"):
                addictions_n += 1
                if len(addiction_samples) < 8:
                    addiction_samples.append(
                        f"{place.get('canonical_name')}: {', '.join(place.get('addictions_treated') or [])}"
                    )
            if place.get("languages_spoken"):
                languages_n += 1
            if place.get("treatment_price"):
                price_n += 1
            if place.get("export_eligible"):
                export_eligible += 1
            conf = float(place.get("confidence_score") or 0)
            if conf >= 0.7:
                confidence_buckets[">=0.70"] += 1
            elif conf >= 0.55:
                confidence_buckets["0.55-0.69"] += 1
            else:
                confidence_buckets["<0.55"] += 1
            if place.get("addiction_focus_confirmed") is True:
                addiction_focus["confirmed_yes"] += 1
            elif place.get("addiction_focus_confirmed") is False:
                addiction_focus["confirmed_no"] += 1
            else:
                addiction_focus["unknown"] += 1
        offset += len(items)
        if len(items) < 500:
            break

    export = req("GET", f"/maps/runs/{RUN}/export-summary", token, org_id)

    print(f"relevant_places={relevant}")
    print(f"with_website={has_website} ({100*has_website/max(relevant,1):.0f}%)")
    print(f"with_addictions={addictions_n} ({100*addictions_n/max(relevant,1):.1f}%)")
    print(f"with_languages={languages_n} ({100*languages_n/max(relevant,1):.1f}%)")
    print(f"with_price={price_n}")
    print(f"export_eligible={export_eligible}")
    print("client_eligibility:", dict(elig))
    print("lifecycle:", dict(lifecycle))
    print("facility_type top:", dict(facility.most_common(8)))
    print("discovery_confidence:", dict(confidence_buckets))
    print("addiction_focus:", dict(addiction_focus))
    print("export_sheets:", export.get("sheets"))
    print("addiction_samples:")
    for s in addiction_samples:
        print(f"  - {s}")


if __name__ == "__main__":
    main()
