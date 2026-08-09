import json
import sys
import urllib.request
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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

buckets = Counter()
decisions = Counter()
conf_zero = 0
samples = {"error": [], "unavailable": [], "uncertain": [], "genuine": [], "keep": []}
total = 0
offset = 0

while True:
    page = req(
        "GET", f"/maps/runs/{RUN}/places/paged?limit=100&offset={offset}", token=token, org_id=org
    )
    items = page.get("items") or page.get("data") or []
    if not items:
        break
    meta_total = (page.get("meta") or {}).get("total") or 0
    for p in items:
        total += 1
        dec = p.get("keep_drop_decision")
        decisions[dec or "undecided"] += 1
        reason = (p.get("relevance_reason") or "").strip()
        conf = p.get("confidence_score")
        if conf in (0, 0.0):
            conf_zero += 1
        name = p.get("canonical_name") or p.get("raw_name")
        row = {"name": name, "conf": conf, "reason": reason[:110]}
        low = reason.lower()
        if dec == "keep":
            buckets["KEEP"] += 1
            if len(samples["keep"]) < 5:
                samples["keep"].append(row)
        elif low.startswith("keep_drop_error"):
            buckets["DROP: call error"] += 1
            if len(samples["error"]) < 5:
                samples["error"].append(row)
        elif "classifier_unavailable" in low:
            buckets["DROP: classifier unavailable"] += 1
            if len(samples["unavailable"]) < 5:
                samples["unavailable"].append(row)
        elif low.startswith("uncertain"):
            buckets["DROP: uncertain/low-confidence"] += 1
            if len(samples["uncertain"]) < 5:
                samples["uncertain"].append(row)
        elif low.startswith("preexisting_exclusion"):
            buckets["DROP: prefilter (no AI call)"] += 1
        elif dec == "drop":
            buckets["DROP: genuine model reason"] += 1
            if len(samples["genuine"]) < 5:
                samples["genuine"].append(row)
        else:
            buckets["UNDECIDED"] += 1
    offset += len(items)
    if offset >= meta_total or len(items) < 100:
        break

print("total places scanned:", total)
print("\ndecision field counts:", dict(decisions))
print("confidence_score == 0 count:", conf_zero)
print("\nREASON BUCKETS")
for k, v in buckets.most_common():
    print(f"  {v:5d}  {k}")

for label, rows in samples.items():
    if rows:
        print(f"\n--- {label} samples ---")
        for r in rows:
            print(f"  conf={r['conf']} | {r['name'][:45]!r} | {r['reason']!r}")
