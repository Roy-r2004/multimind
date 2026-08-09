import json
import time
import urllib.request
from datetime import UTC, datetime

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
    with urllib.request.urlopen(r, timeout=90) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


s = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
token, org = s["access_token"], s["organization"]["id"]

detail = req("GET", f"/maps/runs/{RUN}", token=token, org_id=org)
state = detail.get("processing_state") or {}
print("run.status =", detail.get("status"))
print("run.heartbeat_at =", detail.get("heartbeat_at"))
print("run.error_message =", detail.get("error_message"))
print("processing_state keys =", sorted(state.keys()) if isinstance(state, dict) else state)
for key in [
    "keep_drop_status",
    "keep_drop_heartbeat_at",
    "enrichment_status",
    "enrichment_heartbeat_at",
    "current_phase",
    "enrichment_paused",
    "enrichment_pipeline_paused",
    "campaign_paused",
    "batch_lock",
    "batch_lock_at",
]:
    if isinstance(state, dict) and key in state:
        print(f"  {key} = {json.dumps(state[key], default=str)[:200]}")

now = datetime.now(UTC)
print("\nnow(UTC) =", now.isoformat())


def stale(ts_str):
    if not ts_str:
        return None
    try:
        ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (now - ts).total_seconds() / 60.0


d0 = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org)
print("last_activity_at stale_min =", stale(d0.get("last_activity_at")))
print("enrichment_heartbeat stale_min =", stale(d0.get("enrichment_heartbeat_at")))

print("\nWatching 3 min for any movement...")
base = (d0.get("places_keep"), d0.get("places_dropped"), d0.get("places_undecided"), d0.get("last_activity_at"))
for i in range(6):
    time.sleep(30)
    d = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org)
    cur = (d.get("places_keep"), d.get("places_dropped"), d.get("places_undecided"), d.get("last_activity_at"))
    print(
        f"[{i + 1}] keep={cur[0]} drop={cur[1]} undecided={cur[2]} "
        f"activity={cur[3]} moved={cur != base}"
    )
    if cur != base:
        print("MOVEMENT DETECTED — still progressing")
        break
else:
    print("NO MOVEMENT in 3 minutes — stalled")
