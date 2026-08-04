"""Algeria enrichment monitor — prints health snapshot for a live run."""

from __future__ import annotations

import json
import sys
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


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "poll"
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]
    dash = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org_id)
    st = dash.get("processing_state") or {}
    now = datetime.now(timezone.utc)

    counts: Counter[str] = Counter()
    pipe: Counter[str] = Counter()
    life: Counter[str] = Counter()
    elig: Counter[str] = Counter()
    running: list[dict] = []
    addictions = languages = 0
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
            status = p.get("enrichment_status") or "?"
            counts[status] += 1
            pipe[p.get("enrichment_pipeline_state") or "?"] += 1
            life[p.get("lifecycle_status") or "?"] += 1
            elig[p.get("client_eligibility") or "?"] += 1
            if [x for x in (p.get("addictions_treated") or []) if str(x).strip()]:
                addictions += 1
            if [x for x in (p.get("languages_spoken") or []) if str(x).strip()]:
                languages += 1
            if status == "running":
                running.append(p)
        offset += len(items)
        if len(items) < 500:
            break

    heartbeat = parse_dt(dash.get("heartbeat_at"))
    updated = parse_dt(dash.get("updated_at"))
    print(f"=== {label} @ {now.isoformat()} ===")
    print(
        "pending={p} running={r} completed={c} failed={f} skipped={s}".format(
            p=counts.get("pending", 0),
            r=counts.get("running", 0),
            c=counts.get("completed", 0),
            f=counts.get("failed", 0),
            s=counts.get("skipped", 0),
        )
    )
    print(
        "pipeline_not_required={nr} class_completed={cc} class_failed={cf} detail_completed={dc} detail_failed={df} detail_pending={dp}".format(
            nr=pipe.get("detail_not_required", 0) + pipe.get("DETAIL_NOT_REQUIRED", 0),
            cc=pipe.get("classification_completed", 0),
            cf=pipe.get("classification_failed_retryable", 0),
            dc=pipe.get("detail_enrichment_completed", 0),
            df=pipe.get("detail_enrichment_failed", 0),
            dp=pipe.get("detail_enrichment_pending", 0),
        )
    )
    print(f"heartbeat_at={dash.get('heartbeat_at')} updated_at={dash.get('updated_at')}")
    if heartbeat:
        print(f"heartbeat_age_s={int((now - heartbeat).total_seconds())}")
    elif updated:
        print(f"updated_age_s={int((now - updated).total_seconds())}")

    if running:
        # oldest by attempts / no started field; use first and list names
        oldest = running[0]
        for p in running[1:]:
            # prefer place with website (more likely crawl stall)
            if (p.get("official_website") or p.get("website")) and not (
                oldest.get("official_website") or oldest.get("website")
            ):
                oldest = p
        print(
            "oldest_running:",
            json.dumps(
                {
                    "id": oldest.get("id"),
                    "name": oldest.get("canonical_name") or oldest.get("raw_name"),
                    "website": oldest.get("official_website") or oldest.get("website"),
                    "pipeline": oldest.get("enrichment_pipeline_state"),
                    "lifecycle": oldest.get("lifecycle_status"),
                    "error": (oldest.get("enrichment_error_message") or "")[:160],
                },
                ensure_ascii=False,
            ),
        )
        names = []
        for p in running[:6]:
            name = (p.get("canonical_name") or p.get("raw_name") or "")[:80]
            names.append(name.encode("ascii", "replace").decode("ascii"))
        print(f"running_names={names}")
    else:
        print("oldest_running: none")

    sonar = st.get("sonar_classify_stats") or st.get("sonar_fallback_stats") or {}
    print(
        f"sonar_calls={sonar.get('sonar_calls', 0)} successes={int(sonar.get('sonar_calls') or 0) - int(sonar.get('sonar_final_failures') or 0)} failures={sonar.get('sonar_final_failures', 0)}"
    )
    detail = st.get("detail_enrichment_stats") or {}
    print(f"phase2_stats={json.dumps(detail)}")
    print(f"classification_stats={json.dumps(st.get('classification_stats'))}")
    print(f"crawl_skip_domains={st.get('crawl_skip_domains')}")
    print(f"limits={json.dumps(st.get('limits_reached'))}")
    print(f"lifecycle={dict(life)}")
    print(f"eligibility={dict(elig)}")
    print(f"with_addictions={addictions} with_languages={languages}")
    print(f"pipeline_top={pipe.most_common(12)}")


if __name__ == "__main__":
    main()
