import json
import time
import urllib.request

BASE = "https://multiverdict.tech/api/v1"
RUN = "cdfe7852-2bc4-49ab-8209-b11f407e1408"


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

for i in range(8):
    d = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org)
    print(
        f"[{i}] status={d.get('status')} stage={d.get('current_stage')} "
        f"disc={d.get('discovery_status')} keep_drop={d.get('keep_drop_status')} "
        f"keep={d.get('places_keep')} drop={d.get('places_dropped')} "
        f"undec={d.get('places_undecided')} enriched={d.get('places_enriched')} "
        f"err={(d.get('error_message') or '')[:100]!r} "
        f"activity={d.get('last_activity_at')}"
    )
    if d.get("status") in {"failed", "completed", "completed_with_warnings"}:
        break
    if d.get("keep_drop_status") in {"running", "completed"} or (d.get("places_keep") or 0) > 0:
        print("keep/drop active")
        break
    time.sleep(15)
