"""Live smoke test: mission → blueprint → plan → execution → facilities."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000/api/v1"
EMAIL = "chafic@gmail.com"
PASSWORD = "password123"


def req(method: str, path: str, token: str | None = None, org: str | None = None, body: dict | None = None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org:
        headers["X-Org-Id"] = org
    request = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            raw = response.read().decode()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from exc


def main() -> int:
    print("1) login")
    _, session = req("POST", "/auth/signin", body={"email": EMAIL, "password": PASSWORD})
    token = session["access_token"]
    org = session["organization"]["id"]
    print("   org", org)

    print("2) create mission (MC / Monaco — small scope)")
    _, mission = req(
        "POST",
        "/scraping/missions",
        token,
        org,
        {
            "title": "Live smoke Monaco rehab",
            "original_prompt": (
                "Find rehabilitation and addiction treatment facilities in Monaco. "
                "Prefer official directories and clinic websites."
            ),
            "country_code": "MC",
            "model_set_id": "research",
        },
    )
    mission_id = mission["id"]
    print("   mission", mission_id)

    print("3) generate blueprint (OpenRouter)...")
    _, blueprint = req("POST", f"/scraping/missions/{mission_id}/blueprints", token, org, {})
    print("   blueprint", blueprint["id"], blueprint["status"])

    print("4) approve blueprint")
    _, blueprint = req("POST", f"/scraping/blueprints/{blueprint['id']}/approve", token, org, {})
    print("   status", blueprint["status"])

    print("5) plan AI team")
    _, run = req("POST", f"/scraping/missions/{mission_id}/runs/plan", token, org, {})
    run_id = run["id"]
    print("   run", run_id, "agents", len(run.get("agents") or []))

    print("6) start source discovery execution")
    _, execution = req(
        "POST",
        f"/scraping/runs/{run_id}/executions",
        token,
        org,
        {"execution_type": "initial_full_country", "mode": "real"},
    )
    execution_id = execution["id"]
    print("   execution", execution_id, execution["status"])

    print("7) poll until terminal...")
    terminal = {"completed", "failed", "cancelled"}
    detail = None
    for attempt in range(90):
        _, detail = req("GET", f"/scraping/executions/{execution_id}", token, org)
        status = detail["execution"]["status"]
        print(
            f"   [{attempt:02d}] status={status} "
            f"sources={detail['execution']['sources_discovered']} "
            f"docs={detail['execution']['documents_found']} "
            f"extracted={detail['execution']['records_extracted']} "
            f"verified={detail['execution']['records_verified']}"
        )
        if status in terminal:
            break
        time.sleep(5)
    else:
        print("TIMEOUT waiting for execution")
        return 1

    _, facilities = req("GET", f"/scraping/executions/{execution_id}/facilities", token, org)
    _, candidates = req(
        "GET", f"/scraping/executions/{execution_id}/source-candidates", token, org
    )
    _, documents = req(
        "GET", f"/scraping/executions/{execution_id}/source-documents", token, org
    )

    print("\n=== RESULT ===")
    print("status:", detail["execution"]["status"])
    print("source candidates:", len(candidates))
    print("source documents:", len(documents))
    print("published facilities:", len(facilities))
    for facility in facilities[:10]:
        print(
            f" - {facility['canonical_name']} "
            f"({facility['country_code']}) conf={facility['confidence_score']}"
        )
    if detail["execution"]["status"] != "completed":
        return 1
    if len(candidates) == 0 and len(documents) == 0:
        print("WARNING: no discovery/retrieval output")
        return 2
    print("SMOKE OK")
    print(f"UI: /scraping/{mission_id}/executions/{execution_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
