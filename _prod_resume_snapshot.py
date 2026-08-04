"""Snapshot Algeria enrichment buckets before resume (no mutations)."""

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

    status = Counter()
    pipe = Counter()
    life = Counter()
    elig = Counter()
    selectable_class = 0
    selectable_detail = 0
    stale_running = 0
    completed_preserve = 0
    failed_retryable = 0
    pending = 0
    total = 0
    relevant = 0
    CLASSIFY_PIPELINES = {
        None,
        "prefilter_pending",
        "prefilter_completed",
        "website_resolution_pending",
        "website_resolved",
        "website_not_found",
        "crawl_pending",
        "crawl_completed",
        "crawl_failed",
        "primary_extraction_pending",
        "primary_extraction_failed",
        "classification_failed_retryable",
    }
    DETAIL_PIPELINES = {"classification_completed", "detail_enrichment_failed"}

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
            total += 1
            is_rel = bool(p.get("is_relevant"))
            if is_rel:
                relevant += 1
            st = p.get("enrichment_status") or "?"
            pl = p.get("enrichment_pipeline_state")
            status[st] += 1
            pipe[pl or "?"] += 1
            life[p.get("lifecycle_status") or "?"] += 1
            elig[p.get("client_eligibility") or "?"] += 1
            if st == "completed":
                completed_preserve += 1
            if st == "pending":
                pending += 1
            if st == "running":
                stale_running += 1
            if st == "failed":
                failed_retryable += 1
            if is_rel and st in {"pending", "failed"} and (pl in CLASSIFY_PIPELINES or pl is None):
                selectable_class += 1
            if is_rel and st in {"pending", "failed"} and pl in DETAIL_PIPELINES:
                selectable_detail += 1
        offset += len(items)
        if len(items) < 500:
            break

    st_state = dash.get("processing_state") or {}
    print("=== RUN ===")
    print(
        json.dumps(
            {
                "run_id": RUN,
                "status": dash.get("status"),
                "stage": dash.get("current_stage"),
                "places_found": dash.get("places_found"),
                "places_classified_relevant": dash.get("places_classified_relevant"),
                "cells_completed": dash.get("cells_completed"),
                "cells_total": dash.get("cells_total"),
                "enrichment_status": dash.get("enrichment_status") or st_state.get("enrichment_status"),
                "enrichment_heartbeat_at": dash.get("enrichment_heartbeat_at")
                or st_state.get("enrichment_heartbeat_at"),
                "batch_id": st_state.get("batch_id"),
                "current_phase": st_state.get("current_phase") or st_state.get("enrichment_phase"),
                "active_batch_lock": st_state.get("active_batch_lock"),
                "quota_metrics": dash.get("quota_metrics"),
            },
            indent=2,
        )
    )
    print("=== COUNTS ===")
    print(f"total_places={total} relevant={relevant}")
    print(f"enrichment_status={dict(status)}")
    print(f"pipeline_state={dict(pipe)}")
    print(f"lifecycle={dict(life)}")
    print(f"eligibility={dict(elig)}")
    print(
        f"pending={pending} failed={failed_retryable} running={stale_running} completed={completed_preserve}"
    )
    print(f"selectable_classification={selectable_class} selectable_detail={selectable_detail}")
    print(
        "selection_sql_equivalent: "
        "is_relevant AND enrichment_status IN (pending,failed) "
        "AND (pipeline_state IS NULL OR pipeline_state IN early_classify_states "
        "OR pipeline_state='classification_failed_retryable' for failed retries; "
        "detail uses classification_completed|detail_enrichment_failed)"
    )


if __name__ == "__main__":
    main()
