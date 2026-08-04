"""Inspect Algeria Maps campaign terminal conditions, then reconcile finalization.

Safe by design: POST /reconcile-finalization never requeues discovery or enrichment.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "https://multiverdict.tech/api/v1"
PASSWORD = "password123"
ALGERIA_RUN_ID = "df3918eb-60e5-40e5-8880-e69b411fd6e6"


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


def _print_dash(label: str, dash: dict) -> None:
    keys = [
        "status",
        "overall_status",
        "current_stage",
        "discovery_status",
        "website_discovery_status",
        "crawl_status",
        "classification_status",
        "detail_enrichment_status",
        "cells_completed",
        "cells_total",
        "initial_cells",
        "expansion_cells",
        "cells_pending",
        "cells_failed",
        "places_found",
        "places_classified_relevant",
        "places_with_website",
        "places_enriched",
        "website_refresh_completed_at",
        "enrichment_refresh_completed_at",
        "completed_at",
        "last_activity_at",
        "enrichment_heartbeat_at",
        "enrichment_status",
    ]
    print(f"\n=== {label} ===")
    for key in keys:
        if key in dash:
            print(f"  {key}: {dash.get(key)}")


def main() -> int:
    do_reconcile = "--reconcile" in sys.argv
    for email in ("admin@gmail.com", "chafic@gmail.com"):
        try:
            session = req("POST", "/auth/signin", body={"email": email, "password": PASSWORD})
        except urllib.error.HTTPError:
            continue
        token = session["access_token"]
        org_id = session["organization"]["id"]
        print(f"Signed in as {email}")

        run_id = ALGERIA_RUN_ID
        dash = req("GET", f"/maps/runs/{run_id}/dashboard", token=token, org_id=org_id)
        _print_dash("BEFORE", dash)

        cells = req(
            "GET",
            f"/maps/runs/{run_id}/cells/paged?limit=1&offset=0",
            token=token,
            org_id=org_id,
        )
        print(f"\n  cells meta.total (actual persisted): {cells.get('meta', {}).get('total')}")

        places = req(
            "GET",
            f"/maps/runs/{run_id}/places/paged?limit=1&offset=0",
            token=token,
            org_id=org_id,
        )
        print(f"  places meta.total: {places.get('meta', {}).get('total')}")

        if not do_reconcile:
            print("\nDry inspect only. Re-run with --reconcile after deploy.")
            return 0

        print("\nCalling reconcile-finalization (no reprocess)...")
        result = req(
            "POST",
            f"/maps/runs/{run_id}/reconcile-finalization",
            token=token,
            org_id=org_id,
        )
        print(json.dumps(result, indent=2, default=str)[:4000])

        dash2 = req("GET", f"/maps/runs/{run_id}/dashboard", token=token, org_id=org_id)
        _print_dash("AFTER", dash2)
        places2 = req(
            "GET",
            f"/maps/runs/{run_id}/places/paged?limit=1&offset=0",
            token=token,
            org_id=org_id,
        )
        print(f"\n  places meta.total unchanged check: {places2.get('meta', {}).get('total')}")
        return 0

    print("Could not sign in", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
