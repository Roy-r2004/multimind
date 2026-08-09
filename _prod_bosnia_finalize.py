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

code, r1 = req("POST", f"/maps/runs/{RUN}/reconcile-finalization", token=token, org_id=org)
print("RECONCILE (soft)", code)
if isinstance(r1, dict):
    print("  reconciled:", r1.get("reconciled"))
    print("  blockers:", r1.get("blockers"))
    print("  message:", r1.get("message"))
    print("  overall:", r1.get("overall_status"))

if isinstance(r1, dict) and not r1.get("reconciled"):
    code, r2 = req(
        "POST", f"/maps/runs/{RUN}/reconcile-finalization?force=true", token=token, org_id=org
    )
    print("\nRECONCILE (force)", code)
    if isinstance(r2, dict):
        print("  reconciled:", r2.get("reconciled"))
        print("  run_status:", r2.get("run_status"))
        print("  overall:", r2.get("overall_status"))
        print("  completed_at:", r2.get("completed_at"))

code, d = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org)
print("\nafter reconcile: status =", d.get("status"), "| enriched =", d.get("places_enriched"))

code, r3 = req("POST", f"/maps/runs/{RUN}/retry-enrichment", token=token, org_id=org)
print("ENRICHMENT TRIGGER", code, json.dumps(r3, default=str)[:250])

prev = None
stagnant = 0
for i in range(20):
    time.sleep(20)
    code, d = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org)
    if code != 200:
        continue
    cur = (d.get("places_enriched"), d.get("enrichment_status"))
    print(
        f"[{i + 1}] status={d.get('status')} enriched={d.get('places_enriched')} "
        f"enr={d.get('enrichment_status')} detail={d.get('detail_enrichment_status')} "
        f"activity={d.get('last_activity_at')}"
    )
    if (d.get("places_enriched") or 0) >= 1:
        print("ENRICHED")
        break
    stagnant = stagnant + 1 if cur == prev else 0
    prev = cur
    if stagnant >= 5:
        print("no movement")
        break

print("\nFINAL", json.dumps({
    k: d.get(k)
    for k in [
        "status", "places_found", "places_keep", "places_dropped", "places_undecided",
        "places_enriched", "keep_drop_status", "detail_enrichment_status", "completed_at",
    ]
}, indent=2, default=str))
