"""Re-run detail enrichment on Austria's 28 kept places to backfill the
website-extracted phone number (fix deployed in commit 6f95d8a).

Safe by design: no rediscovery, no new keep/drop judgment — only resets
enrichment_status to pending for already-kept places and re-runs Phase 2
structured extraction, which now also captures/prefers the phone number
found on each facility's own official website over Google's.

Usage:
    python _prod_austria_reenrich_phones.py            # inspect only
    python _prod_austria_reenrich_phones.py --start    # enqueue the pass
    python _prod_austria_reenrich_phones.py --watch    # poll progress
"""

from __future__ import annotations

import json
import sys
import time
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


def show(dash: dict) -> None:
    keys = [
        "status",
        "current_stage",
        "places_keep",
        "places_dropped",
        "places_classified_relevant",
        "places_enriched",
        "last_activity_at",
    ]
    line = {key: dash.get(key) for key in keys if key in dash}
    print(json.dumps(line, indent=2, default=str))


def main() -> int:
    do_start = "--start" in sys.argv
    do_watch = "--watch" in sys.argv
    token, org_id = signin()

    dash = req("GET", f"/maps/runs/{AUSTRIA_RUN_ID}/dashboard", token=token, org_id=org_id)
    print("\n=== BEFORE ===")
    show(dash)

    if do_start:
        print("\nResetting the 28 keeps to pending Phase 2 + enqueueing re-enrichment...")
        result = req(
            "POST",
            f"/maps/runs/{AUSTRIA_RUN_ID}/re-enrich-keeps",
            token=token,
            org_id=org_id,
        )
        print(json.dumps(result, indent=2, default=str))

    if do_watch or do_start:
        print("\nWatching (Ctrl+C to stop)...")
        try:
            while True:
                time.sleep(20)
                dash = req(
                    "GET",
                    f"/maps/runs/{AUSTRIA_RUN_ID}/dashboard",
                    token=token,
                    org_id=org_id,
                )
                enriched = dash.get("places_enriched")
                stage = dash.get("current_stage")
                print(f"  enriched={enriched}/28 current_stage={stage} status={dash.get('status')}")
                if stage == "completed" and dash.get("status") in {
                    "completed",
                    "completed_with_warnings",
                }:
                    print("\n=== DONE ===")
                    show(dash)
                    break
        except KeyboardInterrupt:
            print("stopped watching")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
