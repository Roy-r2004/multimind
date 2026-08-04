"""Watch Algeria small-batch enrichment after enqueue (no mutations)."""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone

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
    with urllib.request.urlopen(request, timeout=180) as response:
        raw = response.read().decode()
        return json.loads(raw) if raw else None


def snapshot(token, org_id):
    dash = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org_id)
    st = dash.get("processing_state") or {}
    status = Counter()
    life = Counter()
    elig = Counter()
    addictions = languages = 0
    export_ready = 0
    offset = 0
    while True:
        page = req(
            "GET",
            f"/maps/runs/{RUN}/places/paged?limit=500&offset={offset}",
            token=token,
            org_id=org_id,
        )
        items = page.get("items") or []
        if not items:
            break
        for p in items:
            status[p.get("enrichment_status") or "?"] += 1
            life[p.get("lifecycle_status") or "?"] += 1
            elig[p.get("client_eligibility") or "?"] += 1
            if [x for x in (p.get("addictions_treated") or []) if str(x).strip()]:
                addictions += 1
            if [x for x in (p.get("languages_spoken") or []) if str(x).strip()]:
                languages += 1
            if p.get("export_eligible"):
                export_ready += 1
        offset += len(items)
        if len(items) < 500:
            break
    qm = dash.get("quota_metrics") or {}
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "status": dash.get("status"),
        "stage": dash.get("current_stage"),
        "enrichment_status": dash.get("enrichment_status") or st.get("enrichment_status"),
        "phase": st.get("current_phase") or st.get("enrichment_phase"),
        "batch_id": st.get("batch_id"),
        "heartbeat": dash.get("enrichment_heartbeat_at") or st.get("enrichment_heartbeat_at"),
        "active_batch_lock": st.get("active_batch_lock"),
        "pending": status.get("pending", 0),
        "running": status.get("running", 0),
        "completed": status.get("completed", 0),
        "failed": status.get("failed", 0),
        "skipped": status.get("skipped", 0),
        "eligible": elig.get("eligible", 0),
        "review": elig.get("review", 0),
        "excluded": elig.get("excluded", 0),
        "life_eligible": life.get("probable_eligible", 0) + life.get("confirmed_eligible", 0),
        "life_review": life.get("needs_review", 0),
        "life_public": life.get("confirmed_public", 0),
        "life_individual": life.get("confirmed_individual_practitioner", 0),
        "life_unrelated": life.get("unrelated", 0),
        "export_ready": export_ready,
        "addictions": addictions,
        "languages": languages,
        "primary_extraction_calls": qm.get("primary_extraction_calls"),
        "sonar_fallback_calls": qm.get("sonar_fallback_calls"),
        "enrichment_calls": qm.get("enrichment_calls"),
        "classifier_calls": qm.get("classifier_calls"),
    }


def main() -> None:
    polls = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 45
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]
    prev = None
    seen_batches: list[str] = []
    for i in range(polls):
        snap = snapshot(token, org_id)
        bid = snap.get("batch_id")
        if bid and bid not in seen_batches:
            seen_batches.append(bid)
        delta = {}
        if prev:
            for k in ("pending", "running", "completed", "failed", "skipped", "review", "life_review"):
                delta[k] = snap[k] - prev[k]
        print(f"\n=== poll {i+1}/{polls} batches_seen={len(seen_batches)} ===")
        print(json.dumps({"snap": snap, "delta": delta, "seen_batches": seen_batches}, indent=2))
        if snap["pending"] == 0 and snap["running"] == 0 and snap["failed"] == 0:
            print("TERMINAL: no pending/running/failed")
            break
        if snap["pending"] == 0 and snap["running"] == 0 and snap.get("enrichment_status") == "completed":
            print("TERMINAL: enrichment completed")
            break
        prev = snap
        if i + 1 < polls:
            time.sleep(interval)


if __name__ == "__main__":
    main()
