import json
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://multiverdict.tech/api/v1"
RUN = "4160d740-7973-4b2c-a8fd-c7ac08fd8c0e"


def req(method, path, token=None, org_id=None, body=None, timeout=90):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org_id:
        headers["X-Org-Id"] = org_id
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        t = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(t) if t else None
        except json.JSONDecodeError:
            return e.code, t


_, s = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
token, org = s["access_token"], s["organization"]["id"]

_, before = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org)
print(
    "BEFORE",
    json.dumps(
        {
            "status": before.get("status"),
            "places_keep": before.get("places_keep"),
            "places_enriched": before.get("places_enriched"),
            "cells_completed": before.get("cells_completed"),
            "cells_total": before.get("cells_total"),
            "keep_drop_status": before.get("keep_drop_status"),
        },
        indent=2,
        default=str,
    ),
    flush=True,
)

code, enrich = req("POST", f"/maps/runs/{RUN}/retry-enrichment", token=token, org_id=org)
print("ENRICH", code, json.dumps(enrich, default=str)[:400], flush=True)

if code != 200:
    # Fallback path if retry-enrichment still blocked for some reason.
    code2, kd = req("POST", f"/maps/runs/{RUN}/keep-drop", token=token, org_id=org)
    print("KEEP-DROP FALLBACK", code2, json.dumps(kd, default=str)[:300], flush=True)

prev = None
stagnant = 0
d = before
for i in range(30):
    time.sleep(15)
    code, d = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org)
    if code != 200:
        print(f"[{i + 1}] dashboard {code}", flush=True)
        continue
    cur = (
        d.get("status"),
        d.get("places_enriched"),
        d.get("enrichment_status"),
        d.get("detail_enrichment_status"),
        d.get("completed_at"),
    )
    print(
        f"[{i + 1}] status={d.get('status')} enriched={d.get('places_enriched')} "
        f"enr={d.get('enrichment_status')} detail={d.get('detail_enrichment_status')} "
        f"completed_at={d.get('completed_at')} activity={d.get('last_activity_at')}",
        flush=True,
    )
    if (d.get("places_enriched") or 0) >= 1 and d.get("status") in {
        "completed",
        "completed_with_warnings",
    }:
        print("DONE", flush=True)
        break
    if (d.get("places_enriched") or 0) >= 1 and d.get("status") == "running":
        # Enrichment happened but status still running — force reconcile.
        code3, r = req(
            "POST", f"/maps/runs/{RUN}/reconcile-finalization", token=token, org_id=org
        )
        print("RECONCILE", code3, json.dumps({
            "reconciled": (r or {}).get("reconciled") if isinstance(r, dict) else None,
            "run_status": (r or {}).get("run_status") if isinstance(r, dict) else None,
            "blockers": (r or {}).get("blockers") if isinstance(r, dict) else None,
        }, default=str), flush=True)
    stagnant = stagnant + 1 if cur == prev else 0
    prev = cur
    if stagnant >= 8:
        print("NO MOVEMENT", flush=True)
        break

print(
    "\nFINAL",
    json.dumps(
        {
            k: d.get(k)
            for k in [
                "status",
                "error_message",
                "places_found",
                "places_keep",
                "places_dropped",
                "places_undecided",
                "places_enriched",
                "keep_drop_status",
                "detail_enrichment_status",
                "completed_at",
            ]
        },
        indent=2,
        default=str,
    ),
    flush=True,
)
