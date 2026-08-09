"""Re-judge Bosnia places that were auto-dropped by classifier failures.

The original keep/drop pass recorded drop/classifier_unavailable (conf 0.0)
for places where every classifier attempt errored — they were never actually
judged, and the resumable pass never revisits a persisted decision. This
clears exactly those failure-decisions via the retry-failed-keep-drop
endpoint (genuine model drops stay decided) and re-runs the pass; enrichment
then picks up all keeps thanks to the all-keeps enqueue gating (6ddde8d).

Usage:
    python _prod_bosnia_retry_failed_drops.py            # inspect only
    python _prod_bosnia_retry_failed_drops.py --start    # clear + re-judge
    python _prod_bosnia_retry_failed_drops.py --watch    # poll progress
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://multiverdict.tech/api/v1"
PASSWORD = "password123"
BOSNIA_RUN_ID = "4160d740-7973-4b2c-a8fd-c7ac08fd8c0e"

# Names that smell like NGO/religious therapeutic communities — the tier a
# too-strict gate is most likely to misjudge in the Balkans.
SUSPICIOUS_NAME = re.compile(
    r"zajednic|komun|udru[gž]|terapijsk|emanuel|remar|cenacolo|caritas|monaste|samostan",
    re.IGNORECASE,
)
FAILURE_REASON = re.compile(r"^(classifier_unavailable|keep_drop_error)")


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


def fetch_all_places(token, org_id):
    places, offset = [], 0
    while True:
        page = req(
            "GET",
            f"/maps/runs/{BOSNIA_RUN_ID}/places/paged?limit=500&offset={offset}",
            token=token,
            org_id=org_id,
        )
        items = page["items"]
        places.extend(items)
        offset += len(items)
        if offset >= page["meta"]["total"] or not items:
            return places


def show(dash: dict) -> None:
    keys = [
        "status",
        "current_stage",
        "keep_drop_status",
        "places_keep",
        "places_dropped",
        "places_undecided",
        "places_enriched",
        "last_activity_at",
    ]
    print(json.dumps({k: dash.get(k) for k in keys if k in dash}, indent=2, default=str))


def main() -> int:
    do_start = "--start" in sys.argv
    do_watch = "--watch" in sys.argv
    token, org_id = signin()

    run = req("GET", f"/maps/runs/{BOSNIA_RUN_ID}", token=token, org_id=org_id)
    if "bosnia" not in run["country_name"].lower():
        raise SystemExit(f"Refusing: run {BOSNIA_RUN_ID} is {run['country_name']}, not Bosnia")

    dash = req("GET", f"/maps/runs/{BOSNIA_RUN_ID}/dashboard", token=token, org_id=org_id)
    print("\n=== BEFORE ===")
    show(dash)

    places = fetch_all_places(token, org_id)
    drops = [p for p in places if p.get("keep_drop_decision") == "drop"]
    failed = [p for p in drops if FAILURE_REASON.match(p.get("relevance_reason") or "")]
    suspicious = [
        p
        for p in drops
        if p not in failed and SUSPICIOUS_NAME.search(p.get("canonical_name") or "")
    ]

    print(f"\nFailure-dropped (never judged) — {len(failed)}:")
    for p in failed:
        print(f"  - {p['canonical_name']}  [{p.get('relevance_reason')}]")
    print(f"\nGenuine drops with community-like names (manual review candidates) — {len(suspicious)}:")
    for p in suspicious:
        print(f"  - {p['canonical_name']}  conf={p.get('confidence_score')}  [{(p.get('relevance_reason') or '')[:100]}]")

    if do_start:
        print("\nClearing failure-decisions + re-enqueueing keep/drop...")
        try:
            result = req(
                "POST",
                f"/maps/runs/{BOSNIA_RUN_ID}/retry-failed-keep-drop",
                token=token,
                org_id=org_id,
            )
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 405):
                raise SystemExit(
                    "retry-failed-keep-drop endpoint not found — deploy 1a1259e first"
                ) from exc
            raise
        print(json.dumps(result, indent=2, default=str))

    if do_watch or do_start:
        print("\nWatching (Ctrl+C to stop)...")
        try:
            while True:
                time.sleep(20)
                dash = req(
                    "GET",
                    f"/maps/runs/{BOSNIA_RUN_ID}/dashboard",
                    token=token,
                    org_id=org_id,
                )
                print(
                    f"  keep={dash.get('places_keep')} drop={dash.get('places_dropped')} "
                    f"undecided={dash.get('places_undecided')} "
                    f"keep_drop_status={dash.get('keep_drop_status')} "
                    f"enriched={dash.get('places_enriched')} status={dash.get('status')}"
                )
                if (
                    dash.get("keep_drop_status") == "completed"
                    and dash.get("places_undecided") == 0
                ):
                    print("\n=== KEEP/DROP DONE ===")
                    show(dash)
                    break
        except KeyboardInterrupt:
            print("stopped watching")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
