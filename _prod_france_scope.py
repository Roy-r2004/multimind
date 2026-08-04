import json
import urllib.request

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

p = req("GET", f"/maps/runs/{RUN}/places/paged?limit=8&offset=0", token=token, org_id=org)
items = p.get("items") or p.get("data") or []
print("returned", len(items), "of total", p.get("meta", {}).get("total"))
for it in items:
    print(
        json.dumps(
            {
                "name": it.get("canonical_name") or it.get("raw_name"),
                "is_relevant": it.get("is_relevant"),
                "lifecycle_status": it.get("lifecycle_status"),
                "client_eligibility": it.get("client_eligibility"),
                "relevance_reason": (it.get("relevance_reason") or "")[:60],
                "keep_drop_decision": it.get("keep_drop_decision"),
            },
            default=str,
        )
    )
