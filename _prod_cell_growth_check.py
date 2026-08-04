"""Check whether Algeria cell count is still expanding and why."""

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


def main() -> None:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]

    dash = lambda: req("GET", f"/maps/runs/{RUN_ID}/dashboard", token=token, org_id=org_id)
    d1 = dash()
    time.sleep(20)
    d2 = dash()

    print("Snapshot A -> B (20 seconds apart):")
    for key in (
        "cells_total",
        "cells_completed",
        "cells_pending",
        "cells_failed",
        "cells_capped",
        "places_found",
        "places_enriched",
        "current_stage",
        "status",
    ):
        print(f"  {key}: {d1.get(key)} -> {d2.get(key)}")

    delta_completed = (d2.get("cells_completed") or 0) - (d1.get("cells_completed") or 0)
    delta_pending = (d2.get("cells_pending") or 0) - (d1.get("cells_pending") or 0)
    print(f"\n  cells_completed delta: +{delta_completed} in 20s")
    print(f"  cells_pending delta: {delta_pending} in 20s")

    regions = req("GET", f"/maps/runs/{RUN_ID}/regions", token=token, org_id=org_id)
    print(f"\nRegions: {len(regions)}")
    from collections import Counter

    status_counts = Counter(
        region.get("saturation_status") or region.get("status") or "unknown" for region in regions
    )
    print("Region status counts:", dict(status_counts))

    print("\nTop regions by activity:")
    for region in sorted(
        regions,
        key=lambda item: (item.get("cells_total") or 0, item.get("cells_completed") or 0),
        reverse=True,
    )[:10]:
        print(
            f"  {region.get('region_name')}: "
            f"cells={region.get('cells_completed')}/{region.get('cells_total')} "
            f"expanded={region.get('cells_expanded')} "
            f"status={region.get('saturation_status') or region.get('status')}"
        )


if __name__ == "__main__":
    main()
