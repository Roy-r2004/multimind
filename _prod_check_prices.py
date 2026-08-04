import json
import urllib.request

BASE = "https://multiverdict.tech/api/v1"
RUN = "df3918eb-60e5-40e5-8880-e69b411fd6e6"


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

page = req("GET", f"/maps/runs/{RUN}/places?limit=2000", token=token, org_id=org)
items = page if isinstance(page, list) else page.get("items") or []
el = [p for p in items if p.get("client_eligibility") == "eligible"]
print("eligible", len(el))
print("has treatment_price key?", "treatment_price" in (el[0] if el else {}))
print(
    "price/enrich/keep keys",
    [k for k in (el[0] if el else {}) if "price" in k.lower() or "enrich" in k.lower() or "keep" in k.lower()],
)

for place in el:
    name = place.get("canonical_name") or ""
    if "CGSA" in name or "بوشاوي" in name or "Abidat" in name:
        detail = req("GET", f"/maps/runs/{RUN}/places/{place['id']}", token=token, org_id=org)
        keys = [
            "canonical_name",
            "treatment_price",
            "addictions_treated",
            "languages_spoken",
            "enrichment_status",
            "enrichment_pipeline_state",
            "enrichment_extraction_source",
            "enrichment_completed_at",
            "official_website",
            "raw_website",
            "keep_drop_decision",
            "keep_drop_reason",
        ]
        print("\nDETAIL", name)
        print(json.dumps({k: detail.get(k) for k in keys}, ensure_ascii=False, indent=2))
