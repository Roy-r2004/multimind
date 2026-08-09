import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://multiverdict.tech/api/v1"


def req(method, path, token=None, org_id=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org_id:
        headers["X-Org-Id"] = org_id
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=90) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


s = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
token, org = s["access_token"], s["organization"]["id"]

runs = req("GET", "/maps/runs", token=token, org_id=org)
items = runs if isinstance(runs, list) else runs.get("items") or runs.get("runs") or []
albania = [
    r
    for r in items
    if (r.get("country_code") or "").upper() == "AL"
    or "albania" in (r.get("country_name") or "").lower()
]
print("ALBANIA RUNS")
for r in albania:
    print(
        json.dumps(
            {
                "id": r.get("id"),
                "country_name": r.get("country_name"),
                "status": r.get("status"),
                "error_message": r.get("error_message"),
                "cells_total": r.get("cells_total"),
                "cells_completed": r.get("cells_completed"),
                "places_found": r.get("places_found"),
                "places_enriched": r.get("places_enriched"),
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
                "completed_at": r.get("completed_at"),
            },
            indent=2,
            default=str,
        )
    )

if not albania:
    print("No Albania runs. Recent:")
    for r in items[:10]:
        print(r.get("country_name"), r.get("id"), r.get("status"), r.get("created_at"))
    raise SystemExit(0)

active = [r for r in albania if r.get("status") in {"running", "queued", "failed"}]
run = active[0] if active else albania[0]
run_id = run["id"]
print("CHECKING", run_id)


def snap():
    d = req("GET", f"/maps/runs/{run_id}/dashboard", token=token, org_id=org)
    return {
        "status": d.get("status"),
        "error_message": d.get("error_message"),
        "current_stage": d.get("current_stage"),
        "country_profile_status": d.get("country_profile_status"),
        "country_profile_error": d.get("country_profile_error"),
        "discovery_status": d.get("discovery_status"),
        "cells_total": d.get("cells_total"),
        "cells_completed": d.get("cells_completed"),
        "cells_pending": d.get("cells_pending"),
        "cells_failed": d.get("cells_failed"),
        "places_found": d.get("places_found"),
        "places_keep": d.get("places_keep"),
        "places_dropped": d.get("places_dropped"),
        "places_undecided": d.get("places_undecided"),
        "keep_drop_status": d.get("keep_drop_status"),
        "places_enriched": d.get("places_enriched"),
        "enrichment_status": d.get("enrichment_status"),
        "detail_enrichment_status": d.get("detail_enrichment_status"),
        "last_activity_at": d.get("last_activity_at"),
        "enrichment_heartbeat_at": d.get("enrichment_heartbeat_at"),
        "completed_at": d.get("completed_at"),
    }


a = snap()
print("\nT0", json.dumps(a, indent=2, default=str))
time.sleep(20)
b = snap()
print("\nT+20s", json.dumps(b, indent=2, default=str))
moved = {k: (a.get(k), b.get(k)) for k in a if a.get(k) != b.get(k)}
print("\nCHANGED", json.dumps(moved, indent=2, default=str) if moved else "none — likely stuck/idle")
