import json
import time
import urllib.request

BASE = "https://multiverdict.tech/api/v1"
RUN = "512c266c-59cc-4a77-b283-8e8b6c21a371"


def req(method, path, token=None, org_id=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org_id:
        headers["X-Org-Id"] = org_id
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=90) as response:
        raw = response.read().decode()
        return json.loads(raw) if raw else None


session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
token = session["access_token"]
org = session["organization"]["id"]

for i in range(40):
    dash = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org)
    print(
        f"[{i + 1}] status={dash.get('status')} stage={dash.get('current_stage')} "
        f"keep_drop={dash.get('keep_drop_status')} undecided={dash.get('places_undecided')} "
        f"keep={dash.get('places_keep')} drop={dash.get('places_dropped')} "
        f"eligible={dash.get('places_eligible')} enriched={dash.get('places_enriched')} "
        f"detail={dash.get('detail_enrichment_status')}"
    )
    if (
        dash.get("places_undecided") == 0
        and dash.get("detail_enrichment_status") in {"completed", "completed_with_warnings"}
        and dash.get("current_stage") in {"completed"}
    ):
        break
    time.sleep(20)

print("\nFINAL", json.dumps({k: dash.get(k) for k in [
    "status", "overall_status", "current_stage", "places_found", "places_keep",
    "places_dropped", "places_undecided", "places_eligible", "places_enriched",
    "keep_drop_status", "detail_enrichment_status",
]}, indent=2, default=str))
