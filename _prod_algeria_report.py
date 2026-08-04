"""Detailed production Algeria run report."""

from __future__ import annotations

import json
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


def main() -> None:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]

    dash = req("GET", f"/maps/runs/{RUN_ID}/dashboard", token=token, org_id=org_id)
    detail = req("GET", f"/maps/runs/{RUN_ID}", token=token, org_id=org_id)

    print("=== RUN SUMMARY ===")
    for key in [
        "status",
        "current_stage",
        "campaign_paused",
        "error_message",
        "country_profile_status",
        "country_profile_error",
        "cells_total",
        "cells_completed",
        "cells_pending",
        "cells_failed",
        "cells_capped",
        "regions_total",
        "places_found",
        "places_eligible",
        "places_review",
        "places_excluded",
        "places_enriched",
        "places_with_website",
        "places_classified_relevant",
        "started_at",
        "completed_at",
        "updated_at",
    ]:
        print(f"{key}: {dash.get(key) if key in dash else detail.get(key)}")

    print("\n=== FUNNEL / QUOTA / STATE ===")
    for block in ("funnel_metrics", "saturation_summary", "quota_metrics", "processing_state"):
        val = dash.get(block)
        print(f"{block}: {json.dumps(val, ensure_ascii=False)[:1200] if val else None}")

    try:
        export = req("GET", f"/maps/runs/{RUN_ID}/export-summary", token=token, org_id=org_id)
        print("\n=== EXPORT SUMMARY ===")
        print(json.dumps(export, indent=2, ensure_ascii=False)[:3000])
    except Exception as exc:
        print(f"\nexport-summary error: {exc}")

    try:
        regions = req("GET", f"/maps/runs/{RUN_ID}/regions?limit=100", token=token, org_id=org_id)
        items = regions.get("items") if isinstance(regions, dict) else regions
        print(f"\n=== REGIONS ({len(items) if items else 0}) ===")
        if items:
            for region in sorted(items, key=lambda r: r.get("cells_completed") or 0, reverse=True)[:12]:
                print(
                    f"  {region.get('region_name')}: "
                    f"cells={region.get('cells_completed')}/{region.get('cells_total')} "
                    f"expanded={region.get('cells_expanded')} "
                    f"status={region.get('saturation_status')}"
                )
    except Exception as exc:
        print(f"\nregions error: {exc}")


if __name__ == "__main__":
    main()
