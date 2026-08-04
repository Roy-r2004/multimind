"""Re-enrich Algeria keep places for treatment_price, then poll."""

from __future__ import annotations

import json
import time
import urllib.error
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


def list_keeps(token, org_id):
    keeps = []
    offset = 0
    while True:
        page = req(
            "GET",
            f"/maps/runs/{RUN}/places/paged?limit=200&offset={offset}",
            token=token,
            org_id=org_id,
        )
        items = page.get("items") or []
        if not items:
            break
        for place in items:
            if place.get("client_eligibility") == "eligible" or place.get("keep_drop_decision") == "keep":
                keeps.append(place)
        offset += len(items)
        if len(items) < 200:
            break
    return keeps


def main() -> int:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]

    dash = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org_id)
    print(
        "BEFORE",
        {
            "places_keep": dash.get("places_keep"),
            "places_enriched": dash.get("places_enriched"),
            "detail_enrichment_status": dash.get("detail_enrichment_status"),
            "current_stage": dash.get("current_stage"),
            "overall_status": dash.get("overall_status"),
        },
    )

    try:
        out = req("POST", f"/maps/runs/{RUN}/re-enrich-keeps", token=token, org_id=org_id)
    except urllib.error.HTTPError as exc:
        print("HTTP", exc.code, exc.read().decode()[:800])
        return 1
    print("TRIGGER", json.dumps(out, indent=2, default=str))

    for i in range(40):
        time.sleep(15)
        dash = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org_id)
        keep = dash.get("places_keep")
        enriched = dash.get("places_enriched")
        detail = dash.get("detail_enrichment_status")
        stage = dash.get("current_stage")
        print(
            f"[{i + 1}] keep={keep} enriched={enriched} detail={detail} "
            f"stage={stage} overall={dash.get('overall_status')}"
        )
        if detail in {"completed", "completed_with_warnings"} and enriched == keep and stage != "enrichment":
            break

    keeps = list_keeps(token, org_id)
    priced = [p for p in keeps if (p.get("treatment_price") or "").strip()]
    print(f"\nKeeps={len(keeps)} with_price={len(priced)}")
    for place in keeps:
        print(
            "-",
            place.get("canonical_name"),
            "| price=",
            repr(place.get("treatment_price")),
            "| addictions=",
            place.get("addictions_treated"),
            "| enrichment=",
            place.get("enrichment_status"),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
