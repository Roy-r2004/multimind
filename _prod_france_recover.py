import json
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
    with urllib.request.urlopen(r, timeout=90) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


s = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
token, org = s["access_token"], s["organization"]["id"]
print("org", org, s["organization"].get("name"))

runs = req("GET", "/maps/runs", token=token, org_id=org)
items = runs if isinstance(runs, list) else runs.get("items") or runs.get("runs") or []
print("count", len(items))
for r in items:
    print(
        r.get("country_name"),
        "|",
        r.get("id"),
        "|",
        r.get("status"),
        "|",
        r.get("places_found"),
        "|",
        r.get("created_at"),
    )
