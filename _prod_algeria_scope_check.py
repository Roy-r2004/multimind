import json
import urllib.request

BASE = "https://multiverdict.tech/api/v1"
PASSWORD = "password123"
RUN = "df3918eb-60e5-40e5-8880-e69b411fd6e6"


def req(method, path, token=None, org_id=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org_id:
        headers["X-Org-Id"] = org_id
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=60) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


s = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": PASSWORD})
token, org = s["access_token"], s["organization"]["id"]
d = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org)
print(
    "dashboard:",
    json.dumps(
        {
            "places_found": d.get("places_found"),
            "places_classified_relevant": d.get("places_classified_relevant"),
            "places_keep": d.get("places_keep"),
            "places_dropped": d.get("places_dropped"),
            "places_undecided": d.get("places_undecided"),
            "places_eligible": d.get("places_eligible"),
            "places_review": d.get("places_review"),
            "places_excluded": d.get("places_excluded"),
            "keep_drop_status": d.get("keep_drop_status"),
        },
        indent=2,
    ),
)

queries = [
    "limit=1&offset=0",
    "limit=1&offset=0&is_relevant=true",
    "limit=1&offset=0&is_relevant=false",
    "limit=1&offset=0&client_eligibility=review",
    "limit=1&offset=0&client_eligibility=eligible",
    "limit=1&offset=0&client_eligibility=excluded",
]
for params in queries:
    p = req("GET", f"/maps/runs/{RUN}/places/paged?{params}", token=token, org_id=org)
    print(f"{params} -> total={p.get('meta', {}).get('total')}")
