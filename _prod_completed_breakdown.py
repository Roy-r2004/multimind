"""Break down completed vs pending enrichment places for Algeria run."""

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
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read().decode()
        return json.loads(raw) if raw else None


def main() -> None:
    session = req("POST", "/auth/signin", body={"email": "admin@gmail.com", "password": "password123"})
    token = session["access_token"]
    org_id = session["organization"]["id"]

    all_status = Counter()
    completed_life = Counter()
    completed_elig = Counter()
    completed_relevant = Counter()
    completed_combo = Counter()

    pending_life = Counter()
    pending_elig = Counter()
    pending_relevant = Counter()
    pending_status = Counter()

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
        for place in items:
            status = place.get("enrichment_status") or "?"
            all_status[status] += 1
            relevant_key = "relevant" if place.get("is_relevant") else "not_relevant"
            life = place.get("lifecycle_status") or "?"
            elig = place.get("client_eligibility") or "?"

            if status == "completed":
                completed_life[life] += 1
                completed_elig[elig] += 1
                completed_relevant[relevant_key] += 1
                completed_combo[f"{relevant_key} | {life} | {elig}"] += 1
            else:
                pending_status[status] += 1
                pending_life[life] += 1
                pending_elig[elig] += 1
                pending_relevant[relevant_key] += 1
        offset += len(items)
        if len(items) < 500:
            break

    export = req("GET", f"/maps/runs/{RUN}/export-summary", token=token, org_id=org_id)

    print("=== ENRICHMENT STATUS (all 1,471) ===")
    for key, value in sorted(all_status.items()):
        print(f"  {key}: {value}")

    completed_total = all_status.get("completed", 0)
    print(f"\n=== {completed_total} COMPLETED — what happened to them ===")
    print("\nBy relevance (discovery classifier):")
    for key, value in completed_relevant.most_common():
        print(f"  {key}: {value}")

    print("\nBy lifecycle (outcome bucket):")
    for key, value in completed_life.most_common():
        print(f"  {key}: {value}")

    print("\nBy client eligibility (export column):")
    for key, value in completed_elig.most_common():
        print(f"  {key}: {value}")

    print("\nTop outcome combos (completed only):")
    for key, value in completed_combo.most_common(10):
        print(f"  {value:4d}  {key}")

    print("\n=== NOT YET PROCESSED (pending + running) ===")
    for key, value in pending_status.most_common():
        print(f"  enrichment_status={key}: {value}")
    print("\nBy relevance:")
    for key, value in pending_relevant.most_common():
        print(f"  {key}: {value}")
    print("\nBy lifecycle:")
    for key, value in pending_life.most_common():
        print(f"  {key}: {value}")

    print("\n=== EXPORT SHEETS (current) ===")
    print(json.dumps(export.get("sheets"), indent=2))

    print("\n=== PLAIN ENGLISH ===")
    rel_done = completed_relevant.get("relevant", 0)
    unrelated_done = completed_relevant.get("not_relevant", 0)
    rel_left = pending_relevant.get("relevant", 0)
    unrelated_left = pending_relevant.get("not_relevant", 0)
    needs_review_done = completed_life.get("needs_review", 0)
    unrelated_life_done = completed_life.get("unrelated", 0)
    plausible_done = completed_life.get("plausible", 0)

    print(f"- {unrelated_done} completed places were NOT RELEVANT — skipped expensive AI, kept as unrelated/excluded.")
    print(f"- {rel_done} completed places WERE RELEVANT — went through primary extraction (+ Sonar if needed).")
    print(f"  • {needs_review_done} still needs_review after processing")
    print(f"  • {plausible_done} marked plausible")
    print(f"  • {unrelated_life_done} reclassified unrelated during enrichment")
    print(f"- Still waiting: {rel_left} relevant + {unrelated_left} unrelated = {rel_left + unrelated_left}")
    print("- Nothing is deleted. 'Removed' means excluded from Eligible export, not erased from database.")


if __name__ == "__main__":
    main()
