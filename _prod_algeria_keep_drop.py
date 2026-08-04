"""Run the strict keep/drop gate over the existing Algeria campaign.

Safe by design: no rediscovery, no deletion, no reset. The pass only judges
places that do not yet have a persisted keep/drop decision (resumable), then
detail enrichment runs automatically for the keeps.

Usage:
    python _prod_algeria_keep_drop.py            # inspect only
    python _prod_algeria_keep_drop.py --start    # enqueue the pass
    python _prod_algeria_keep_drop.py --watch    # poll progress
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://multiverdict.tech/api/v1"
PASSWORD = "password123"
ALGERIA_RUN_ID = "df3918eb-60e5-40e5-8880-e69b411fd6e6"


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
        "overall_status",
        "current_stage",
        "keep_drop_status",
        "places_keep",
        "places_dropped",
        "places_undecided",
        "places_eligible",
        "places_review",
        "places_excluded",
        "places_enriched",
        "detail_enrichment_status",
        "last_activity_at",
    ]
    line = {key: dash.get(key) for key in keys if key in dash}
    print(json.dumps(line, indent=2, default=str))


def main() -> int:
    do_start = "--start" in sys.argv
    do_watch = "--watch" in sys.argv
    token, org_id = signin()

    dash = req("GET", f"/maps/runs/{ALGERIA_RUN_ID}/dashboard", token=token, org_id=org_id)
    print("\n=== BEFORE ===")
    show(dash)

    if do_start:
        print("\nEnqueueing keep/drop pass (no rediscovery, resumable)...")
        result = req(
            "POST",
            f"/maps/runs/{ALGERIA_RUN_ID}/keep-drop",
            token=token,
            org_id=org_id,
        )
        print(json.dumps(result, indent=2, default=str))

    if do_watch or do_start:
        print("\nWatching (Ctrl+C to stop)...")
        try:
            while True:
                time.sleep(60)
                dash = req(
                    "GET",
                    f"/maps/runs/{ALGERIA_RUN_ID}/dashboard",
                    token=token,
                    org_id=org_id,
                )
                undecided = dash.get("places_undecided")
                kept = dash.get("places_keep")
                dropped = dash.get("places_dropped")
                status = dash.get("keep_drop_status")
                print(
                    f"  keep={kept} drop={dropped} undecided={undecided} "
                    f"keep_drop_status={status} enriched={dash.get('places_enriched')}"
                )
                if status == "completed" and undecided == 0:
                    print("\n=== DONE ===")
                    show(dash)
                    break
        except KeyboardInterrupt:
            print("stopped watching")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
