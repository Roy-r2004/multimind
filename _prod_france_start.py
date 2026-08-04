import json
import time
import urllib.error
import urllib.request

BASE = "https://multiverdict.tech/api/v1"


def req(method, path, token=None, org_id=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org_id:
        headers["X-Org-Id"] = org_id
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        try:
            parsed = json.loads(body_text) if body_text else None
        except json.JSONDecodeError:
            parsed = body_text
        return e.code, parsed


s_code, s = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
assert s_code == 200, s
token, org = s["access_token"], s["organization"]["id"]

code, created = req("POST", "/maps/runs", token=token, org_id=org, body={"country_code": "FR"})
print("CREATE", code, json.dumps(created, indent=2, default=str))
if code not in {200, 201}:
    raise SystemExit(1)

run_id = created["id"]
print("RUN_ID", run_id)

for i in range(24):
    time.sleep(15)
    code, d = req("GET", f"/maps/runs/{run_id}/dashboard", token=token, org_id=org)
    if code != 200:
        print(f"[{i + 1}] dashboard {code} {d}")
        continue
    print(
        f"[{i + 1}] status={d.get('status')} err={(d.get('error_message') or '')[:100]!r} "
        f"profile={d.get('country_profile_status')} "
        f"cells={d.get('cells_completed')}/{d.get('cells_total')} pending={d.get('cells_pending')} "
        f"places={d.get('places_found')} keep={d.get('places_keep')} drop={d.get('places_dropped')} "
        f"undecided={d.get('places_undecided')} enriched={d.get('places_enriched')} "
        f"keep_drop={d.get('keep_drop_status')} stage={d.get('current_stage')}"
    )
    if d.get("status") in {"completed", "completed_with_warnings", "failed", "cancelled"}:
        print("\nFINAL", json.dumps({
            k: d.get(k)
            for k in [
                "status", "error_message", "country_profile_status", "country_profile_error",
                "cells_total", "cells_completed", "cells_pending", "places_found",
                "places_keep", "places_dropped", "places_undecided", "places_enriched",
                "keep_drop_status", "detail_enrichment_status", "current_stage", "completed_at",
            ]
        }, indent=2, default=str))
        break
