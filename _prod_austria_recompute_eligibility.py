"""Re-apply the current eligibility rules (outpatient/telehealth/sober-living/
mental-health-clinic exclusions) to the already-completed Austria campaign.

Pure recompute over already-stored fields — no LLM calls, no rediscovery.
Requires the /maps/runs/{run_id}/recompute-eligibility endpoint to be deployed.

Usage:
    python _prod_austria_recompute_eligibility.py
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "https://multiverdict.tech/api/v1"
PASSWORD = "password123"
AUSTRIA_RUN_ID = "cdfe7852-2bc4-49ab-8209-b11f407e1408"


def req(method, path, token=None, org_id=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org_id:
        headers["X-Org-Id"] = org_id
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=180) as response:
        raw = response.read().decode()
        return json.loads(raw) if raw else None


def signin():
    for email in ("admin@gmail.com", "chafic@gmail.com"):
        try:
            session = req("POST", "/auth/signin", body={"email": email, "password": PASSWORD})
        except urllib.error.HTTPError:
            continue
        print(f"Signed in as {email}")
        return session["access_token"], session["organization"]["id"]
    raise SystemExit("Could not sign in")


def main() -> int:
    token, org_id = signin()

    dash = req("GET", f"/maps/runs/{AUSTRIA_RUN_ID}/dashboard", token=token, org_id=org_id)
    print("\n=== BEFORE ===")
    print(json.dumps({k: dash.get(k) for k in ("status", "places_classified_relevant", "places_enriched")}, indent=2))

    print("\nRecomputing eligibility (no LLM calls, no rediscovery)...")
    result = req(
        "POST",
        f"/maps/runs/{AUSTRIA_RUN_ID}/recompute-eligibility",
        token=token,
        org_id=org_id,
    )
    print(json.dumps(result, indent=2, default=str))

    dash = req("GET", f"/maps/runs/{AUSTRIA_RUN_ID}/dashboard", token=token, org_id=org_id)
    print("\n=== AFTER ===")
    print(json.dumps({k: dash.get(k) for k in ("status", "places_classified_relevant", "places_enriched")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
