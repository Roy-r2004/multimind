"""Re-judge Austria's already-'keep' places under the tightened keep/drop prompt.

Resets keep_drop_decision on current keeps only (drops stay decided), then
re-enqueues the keep/drop pass. Uses only already-stored Maps fields and
already-crawled website content — no rediscovery.

Usage:
    python _prod_austria_revalidate_keeps.py            # inspect only
    python _prod_austria_revalidate_keeps.py --start    # enqueue the pass
    python _prod_austria_revalidate_keeps.py --watch    # poll progress
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
        "keep_drop_status",
        "places_keep",
        "places_dropped",
        "places_undecided",
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
        print("\nResetting decisions on current keeps + enqueueing re-judgment...")
        result = req(
            "POST",
            f"/maps/runs/{AUSTRIA_RUN_ID}/revalidate-keeps",
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
