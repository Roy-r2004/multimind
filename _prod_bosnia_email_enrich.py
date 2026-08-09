"""Bosnia eligible keep: recover enrichment + poll for contact email."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://multiverdict.tech/api/v1"
RUN = "4160d740-7973-4b2c-a8fd-c7ac08fd8c0e"


def req(method, path, token=None, org_id=None, body=None, timeout=120):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if org_id:
        headers["X-Org-Id"] = org_id
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return exc.code, raw


def fetch_keeps(token, org):
    _, page = req(
        "GET",
        f"/maps/runs/{RUN}/places?keep_drop_decision=keep&limit=50",
        token=token,
        org_id=org,
    )
    return (page or {}).get("items") or []


def print_place(place):
    print(
        f"\n{place.get('canonical_name')}\n"
        f"  id={place.get('id')}\n"
        f"  email={place.get('contact_email') or '(none)'}\n"
        f"  web={place.get('official_website') or place.get('raw_website') or '(none)'}\n"
        f"  phone={place.get('international_phone_number') or '(none)'}\n"
        f"  enrichment={place.get('enrichment_status')} pipeline={place.get('enrichment_pipeline_state')}\n"
        f"  source={place.get('enrichment_extraction_source')} err={place.get('enrichment_error_message')}"
    )


def main() -> int:
    _, session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token, org = session["access_token"], session["organization"]["id"]

    keeps = fetch_keeps(token, org)
    print(f"Bosnia keep places: {len(keeps)}")
    for p in keeps:
        print_place(p)

    if not keeps:
        return 0

    missing = [p for p in keeps if not (p.get("contact_email") or "").strip()]
    if not missing:
        print("\nEmail already present.")
        return 0

    for endpoint in ("/recover-enrichment", "/re-enrich-keeps", "/retry-enrichment"):
        code, action = req("POST", f"/maps/runs/{RUN}{endpoint}", token=token, org_id=org)
        print(f"\nPOST {endpoint} => {code} {json.dumps(action, default=str)}")
        if code in (200, 201):
            break

    for i in range(45):
        time.sleep(20)
        _, dash = req("GET", f"/maps/runs/{RUN}/dashboard", token=token, org_id=org)
        keeps = fetch_keeps(token, org)
        emails = [p for p in keeps if (p.get("contact_email") or "").strip()]
        print(
            f"[{i + 1}] status={dash.get('status')} "
            f"enr={dash.get('enrichment_status')} detail={dash.get('detail_enrichment_status')} "
            f"emails={len(emails)}/{len(keeps)}"
        )
        if emails:
            break
        if (
            dash.get("enrichment_status") == "completed"
            and dash.get("detail_enrichment_status") == "completed"
            and i >= 2
        ):
            # Give a few polls after completion in case sonar is still running
            pass

    print("\n=== Final ===")
    for p in fetch_keeps(token, org):
        print_place(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
