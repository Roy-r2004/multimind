import json
import sys
import time
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://multiverdict.tech/api/v1"
RUN = "4160d740-7973-4b2c-a8fd-c7ac08fd8c0e"


def req(method, path, token=None, org_id=None, body=None, timeout=30):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org_id:
        headers["X-Org-Id"] = org_id
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


s = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
token, org = s["access_token"], s["organization"]["id"]

for i in range(5):
    d = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org)
    print(
        f"[{i}] keep={d.get('places_keep')} drop={d.get('places_dropped')} "
        f"undecided={d.get('places_undecided')} keep_drop={d.get('keep_drop_status')} "
        f"status={d.get('status')} activity={d.get('last_activity_at')}"
    )
    if i < 4:
        time.sleep(25)
