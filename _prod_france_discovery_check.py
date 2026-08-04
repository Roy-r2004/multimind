import json
import urllib.request
from collections import Counter

BASE = "https://multiverdict.tech/api/v1"
RUN = "09d468cf-341b-46ca-b228-e680f0ebe94f"


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

# Try paged cells with status filters if supported; also dump all statuses.
statuses = Counter()
pending = []
offset = 0
while True:
    page = req(
        "GET",
        f"/maps/runs/{RUN}/cells/paged?limit=100&offset={offset}",
        token=token,
        org_id=org,
    )
    items = page.get("items") or page.get("data") or []
    if not items:
        break
    for c in items:
        st = c.get("status")
        statuses[st] += 1
        if st in {"pending", "in_progress", "failed"}:
            pending.append(
                {
                    "id": c.get("id"),
                    "status": st,
                    "region_name": c.get("region_name"),
                    "city_name": c.get("city_name"),
                    "query_text": (c.get("query_text") or "")[:80],
                    "places_found": c.get("places_found"),
                    "error_message": c.get("error_message"),
                    "updated_at": c.get("updated_at"),
                }
            )
    total = (page.get("meta") or {}).get("total") or 0
    offset += len(items)
    if offset >= total or len(items) < 100:
        break

print("CELL STATUS COUNTS", dict(statuses))
print("NON_TERMINAL", json.dumps(pending, indent=2, default=str))

# Relevant place counts via paging samples that work
for params in [
    "limit=1&offset=0",
    "limit=1&offset=0&lifecycle_status=needs_review",
    "limit=1&offset=0&lifecycle_status=plausible",
    "limit=1&offset=0&lifecycle_status=unrelated",
    "limit=1&offset=0&lifecycle_status=discovered",
    "limit=1&offset=0&lifecycle_status=confirmed_eligible",
]:
    p = req("GET", f"/maps/runs/{RUN}/places/paged?{params}", token=token, org_id=org)
    print(f"places {params} -> {(p or {}).get('meta', {}).get('total')}")
