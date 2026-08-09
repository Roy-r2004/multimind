import json
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://multiverdict.tech/api/v1"
RUN = "4160d740-7973-4b2c-a8fd-c7ac08fd8c0e"


def req(method, path, token=None, org_id=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org_id:
        headers["X-Org-Id"] = org_id
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        try:
            parsed = json.loads(body_text) if body_text else None
        except json.JSONDecodeError:
            parsed = body_text
        return e.code, parsed


_, s = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
token, org = s["access_token"], s["organization"]["id"]

code, resp = req("POST", f"/maps/runs/{RUN}/keep-drop", token=token, org_id=org)
print("KEEP-DROP TRIGGER", code, json.dumps(resp, indent=2, default=str))

prev = None
stagnant = 0
for i in range(40):
    time.sleep(20)
    code, d = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org)
    if code != 200:
        print(f"[{i + 1}] dashboard {code}")
        continue
    cur = (d.get("places_keep"), d.get("places_dropped"), d.get("places_undecided"))
    print(
        f"[{i + 1}] status={d.get('status')} keep_drop={d.get('keep_drop_status')} "
        f"keep={cur[0]} drop={cur[1]} undecided={cur[2]} "
        f"enriched={d.get('places_enriched')} stage={d.get('current_stage')} "
        f"activity={d.get('last_activity_at')}"
    )
    stagnant = stagnant + 1 if cur == prev else 0
    prev = cur
    if d.get("places_undecided") == 0:
        print("KEEP/DROP DRAINED")
        break
    if stagnant >= 6:
        print("STALLED AGAIN (2 min no movement)")
        break

print("\nFINAL", json.dumps({
    k: d.get(k)
    for k in [
        "status", "error_message", "places_found", "places_keep", "places_dropped",
        "places_undecided", "places_enriched", "keep_drop_status",
        "detail_enrichment_status", "current_stage", "completed_at",
    ]
}, indent=2, default=str))
