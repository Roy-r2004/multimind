"""Diagnose Algeria Sonar classify + Phase 2 routing without spending new Sonar calls."""

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
    print("=== PROCESSING STATE ===")
    print("pipeline:", state.get("enrichment_pipeline"))
    print("classification_stats:", json.dumps(state.get("classification_stats")))
    print("detail_enrichment_stats:", json.dumps(state.get("detail_enrichment_stats")))
    print("sonar:", json.dumps(state.get("sonar_fallback_stats")))
    print("limits:", json.dumps(state.get("limits_reached")))

    # Collect all relevant places
    places = []
    offset = 0
    while True:
        page = req(
            "GET",
            f"/maps/runs/{RUN}/places/paged?limit=200&offset={offset}&is_relevant=true",
            token=token,
            org_id=org_id,
        )
        items = page.get("items") or []
        if not items:
            break
        places.extend(items)
        offset += len(items)
        if len(items) < 200:
            break

    print(f"\nrelevant_places_fetched={len(places)}")

    c_status = Counter()
    c_pipe = Counter()
    c_life = Counter()
    c_elig = Counter()
    c_source = Counter()
    c_facility = Counter()
    c_own = Counter()
    c_addict = Counter()
    errors = []
    needs_review_excluded = 0
    would_be_phase2 = 0

    for p in places:
        c_status[p.get("enrichment_status") or "?"] += 1
        c_pipe[p.get("enrichment_pipeline_state") or "?"] += 1
        c_life[p.get("lifecycle_status") or "?"] += 1
        c_elig[p.get("client_eligibility") or "?"] += 1
        c_source[p.get("enrichment_extraction_source") or "none"] += 1
        c_facility[p.get("facility_type") or "null"] += 1
        c_own[p.get("ownership_status") or "null"] += 1
        focus = p.get("addiction_focus_confirmed")
        c_addict[str(focus)] += 1
        err = (p.get("enrichment_error_message") or "").strip()
        if err:
            errors.append((p.get("id"), p.get("canonical_name") or p.get("raw_name"), err[:300]))

        life = p.get("lifecycle_status")
        elig = p.get("client_eligibility")
        if life == "needs_review" and elig == "excluded":
            needs_review_excluded += 1
        # Mimic is_detail_enrichment_candidate
        confident = {
            "unrelated",
            "confirmed_public",
            "confirmed_individual_practitioner",
            "confirmed_cessation_only",
            "contradicted",
            "duplicate",
            "permanently_closed",
        }
        if (
            p.get("is_relevant")
            and life not in confident
            and elig in {"eligible", "review"}
            and life in {"needs_review", "plausible", "probable_eligible", "confirmed_eligible"}
        ):
            would_be_phase2 += 1

    print("\nenrichment_status:", dict(c_status))
    print("pipeline_state:", dict(c_pipe))
    print("lifecycle:", dict(c_life))
    print("client_eligibility:", dict(c_elig))
    print("extraction_source:", dict(c_source))
    print("facility_type top:", c_facility.most_common(8))
    print("ownership:", dict(c_own))
    print("addiction_focus:", dict(c_addict))
    print("needs_review+excluded:", needs_review_excluded)
    print("would_pass_is_detail_enrichment_candidate:", would_be_phase2)
    print(f"\nerror_messages_count={len(errors)}")
    for pid, name, err in errors[:15]:
        print(f"  ERR {pid} | {name}: {err}")

    # Sample needs_review places for Sonar-failure candidates
    print("\n=== SAMPLE needs_review (3) ===")
    samples = [p for p in places if p.get("lifecycle_status") == "needs_review"][:3]
    for p in samples:
        print(
            json.dumps(
                {
                    "id": p.get("id"),
                    "name": p.get("canonical_name") or p.get("raw_name"),
                    "lifecycle": p.get("lifecycle_status"),
                    "eligibility": p.get("client_eligibility"),
                    "facility_type": p.get("facility_type"),
                    "ownership": p.get("ownership_status"),
                    "operator": p.get("operator_type"),
                    "addiction_focus": p.get("addiction_focus_confirmed"),
                    "confidence": p.get("classification_confidence"),
                    "source": p.get("enrichment_extraction_source"),
                    "pipeline": p.get("enrichment_pipeline_state"),
                    "status": p.get("enrichment_status"),
                    "error": (p.get("enrichment_error_message") or "")[:200],
                    "website": p.get("official_website") or p.get("raw_website"),
                },
                indent=2,
            )
        )

    # Detail place endpoints if available
    for p in samples[:3]:
        try:
            detail = req("GET", f"/maps/runs/{RUN}/places/{p['id']}", token=token, org_id=org_id)
            print(f"\n=== DETAIL {p['id']} ===")
            for key in (
                "canonical_name",
                "enrichment_error_message",
                "enrichment_extraction_source",
                "classification_evidence",
                "classification_confidence",
                "facility_type",
                "ownership_status",
                "client_eligibility",
                "lifecycle_status",
            ):
                val = detail.get(key)
                if isinstance(val, (dict, list)):
                    print(f"  {key}: {json.dumps(val)[:400]}")
                else:
                    print(f"  {key}: {val}")
        except Exception as exc:
            print(f"detail failed {p['id']}: {exc}")


if __name__ == "__main__":
    main()
