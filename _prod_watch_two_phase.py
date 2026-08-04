"""Watch Algeria two-phase enrichment health: verifies new code + Sonar failure rate."""

from __future__ import annotations

import json
import urllib.request
from collections import Counter

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


def main() -> None:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]

    dash = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org_id)
    state = dash.get("processing_state") or {}

    new_code_live = "sonar_classify_stats" in state or "detail_enrichment_budget" in state
    print("NEW_CODE_MARKERS_PRESENT:", new_code_live)
    print("pipeline:", state.get("enrichment_pipeline"))
    print("classification_stats:", json.dumps(state.get("classification_stats")))
    print("detail_enrichment_stats:", json.dumps(state.get("detail_enrichment_stats")))

    sonar = state.get("sonar_classify_stats") or state.get("sonar_fallback_stats") or {}
    calls = int(sonar.get("sonar_calls") or 0)
    fails = int(sonar.get("sonar_final_failures") or 0)
    print(f"sonar_calls={calls} sonar_final_failures={fails}")
    if calls:
        print(f"sonar_failure_rate={fails / calls:.0%}")
    print("limits:", json.dumps(state.get("limits_reached")))
    print("enrichment_refresh_completed_at:", dash.get("enrichment_refresh_completed_at"))

    counts: Counter[str] = Counter()
    pipe: Counter[str] = Counter()
    life: Counter[str] = Counter()
    elig: Counter[str] = Counter()
    addictions = 0
    languages = 0
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
            counts[p.get("enrichment_status") or "?"] += 1
            pipe[p.get("enrichment_pipeline_state") or "?"] += 1
            life[p.get("lifecycle_status") or "?"] += 1
            elig[p.get("client_eligibility") or "?"] += 1
            if [x for x in (p.get("addictions_treated") or []) if str(x).strip()]:
                addictions += 1
            if [x for x in (p.get("languages_spoken") or []) if str(x).strip()]:
                languages += 1
        offset += len(items)
        if len(items) < 500:
            break

    print("enrichment_status:", dict(counts))
    print("lifecycle:", dict(life))
    print("client_eligibility:", dict(elig))
    print(f"with_addictions={addictions} with_languages={languages}")

    export = req("GET", f"/maps/runs/{RUN}/export-summary", token=token, org_id=org_id)
    print("export_sheets:", json.dumps(export.get("sheets")))


if __name__ == "__main__":
    main()
