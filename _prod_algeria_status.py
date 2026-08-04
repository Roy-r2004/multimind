"""Fetch latest Algeria Maps Census run status from production."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "https://multiverdict.tech/api/v1"
PASSWORD = "password123"


def req(method, path, token=None, org_id=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org_id:
        headers["X-Org-Id"] = org_id
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read().decode()
        return json.loads(raw) if raw else None


def main() -> int:
    for email in ("admin@gmail.com", "chafic@gmail.com"):
        try:
            session = req("POST", "/auth/signin", body={"email": email, "password": PASSWORD})
            token = session["access_token"]
            org_id = session["organization"]["id"]
            role = session["organization"].get("role")
            runs = req("GET", "/maps/runs", token=token, org_id=org_id)
            dz = sorted(
                [run for run in runs if run.get("country_code") == "DZ"],
                key=lambda run: run.get("created_at") or "",
                reverse=True,
            )
            print(f"Signed in as {email} (role={role})")
            print("Algeria runs:")
            for run in dz[:5]:
                print(
                    f"  {run['id']} status={run['status']} "
                    f"cells={run.get('cells_completed')}/{run.get('cells_total')} "
                    f"found={run.get('places_found')} relevant={run.get('places_classified_relevant')} "
                    f"created={run.get('created_at')}"
                )
            if not dz:
                print("  (none)")
                continue

            run_id = dz[0]["id"]
            detail = req("GET", f"/maps/runs/{run_id}", token=token, org_id=org_id)
            print("\nLatest run:")
            for key in (
                "status",
                "error_message",
                "cells_completed",
                "cells_total",
                "places_found",
                "places_classified_relevant",
                "places_with_website",
                "created_at",
                "started_at",
                "completed_at",
            ):
                if key in detail:
                    print(f"  {key}: {detail.get(key)}")

            try:
                dash = req("GET", f"/maps/runs/{run_id}/dashboard", token=token, org_id=org_id)
                print("\nAdmin dashboard:")
                print(f"  status={dash.get('status')} stage={dash.get('current_stage')}")
                print(
                    f"  cells planned={dash.get('cells_planned')} completed={dash.get('cells_completed')} "
                    f"expanded={dash.get('cells_expanded')} capped={dash.get('cells_capped')}"
                )
                print(
                    f"  google_pages={dash.get('google_pages_fetched')} raw={dash.get('raw_results')} "
                    f"unique={dash.get('unique_results')} dup_rate={dash.get('duplicate_rate')}"
                )
                funnel = dash.get("funnel") or {}
                print(
                    f"  eligible={funnel.get('eligible')} review={funnel.get('needs_review')} "
                    f"public={funnel.get('public_facility')} individual={funnel.get('individual_practitioner')} "
                    f"unrelated={funnel.get('unrelated')}"
                )
                quota = dash.get("quota") or {}
                print(
                    f"  google_requests={quota.get('google_places_requests')} "
                    f"model_requests={quota.get('model_requests')} "
                    f"est_cost={quota.get('estimated_cost_usd')}"
                )
                print(
                    f"  crawl pending={dash.get('website_crawl_pending')} "
                    f"done={dash.get('website_crawl_completed')} failed={dash.get('website_crawl_failed')}"
                )
                print(
                    f"  enrich pending={dash.get('enrichment_pending')} "
                    f"done={dash.get('enrichment_completed')} failed={dash.get('enrichment_failed')}"
                )
                print(f"  OPEN admin: https://multiverdict.tech/admin/maps/{run_id}")
                print(f"  OPEN user:  https://multiverdict.tech/maps/{run_id}")
            except urllib.error.HTTPError as exc:
                body = exc.read().decode()[:300]
                print(f"\nAdmin dashboard unavailable: {exc.code} {body}")
            return 0
        except Exception as exc:
            print(f"{email} failed: {exc}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
