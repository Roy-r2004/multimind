import json
import urllib.request

BASE = "https://multiverdict.tech/api/v1"
RUN = "df3918eb-60e5-40e5-8880-e69b411fd6e6"


def req(method, path, token=None, org_id=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if org_id:
        h["X-Org-Id"] = org_id
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(r, timeout=120) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


s = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
token = s["access_token"]
org_id = s["organization"]["id"]
d = req("GET", f"/maps/runs/{RUN}", token=token, org_id=org_id)
print("=== RUN DETAIL ===")
for k in [
    "status",
    "heartbeat_at",
    "enrichment_refresh_completed_at",
    "enrichment_refresh_attempts",
    "updated_at",
]:
    print(f"{k}: {d.get(k)}")
print("processing_state:", json.dumps(d.get("processing_state"), indent=2)[:800])

counts = {}
offset = 0
while True:
    page = req("GET", f"/maps/runs/{RUN}/places/paged?limit=500&offset={offset}", token=token, org_id=org_id)
    items = page.get("items") or []
    if not items:
        break
    for p in items:
        es = p.get("enrichment_status", "?")
        counts[es] = counts.get(es, 0) + 1
    offset += len(items)
    if len(items) < 500:
        break
print("enrichment_status:", counts)
