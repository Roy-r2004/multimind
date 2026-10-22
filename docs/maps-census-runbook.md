# Maps Census Operational Runbook

**Status:** Phases 1-4 live on main branch  
**Active campaigns:** Algeria (primary), France (secondary), Finland (watch)  
**Last incident:** Mid-run crashes + stale status (Aug 3, fixed)

---

## Quick Reference

### Start a Census Run

**Via API:**
```bash
curl -X POST https://multiverdict.tech/api/v1/maps/runs \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Org-Id: $ORG_ID" \
  -H "Content-Type: application/json" \
  -d '{"country_code": "FR", "country_name": "France"}'
```

**Via Admin UI:**
- Go to `/admin/maps`
- Click "New Campaign"
- Select country, confirm budget estimate
- Monitor dashboard in real-time

### Monitor Active Runs

**Dashboard:**
- Admin: `https://multiverdict.tech/admin/maps`
- User: `https://multiverdict.tech/maps`

**Key Metrics:**
- `cells_completed / cells_total` — grid coverage %
- `places_found` — total discoveries
- `places_classified_relevant` — after soft filter
- `eligible_candidates_found` — final export count
- `quota_metrics.estimated_cost_usd` — live cost estimate

**Logs:**
```bash
# Show run progress
tail -f logs/maps-census.log | grep "run_id=$RUN_ID"

# Show errors/warnings
grep "ERROR\|WARN" logs/maps-census.log | grep "run_id=$RUN_ID"

# Show circuit breaker events
grep "circuit_breaker" logs/maps-census.log | grep "run_id=$RUN_ID"

# Show LLM cost
grep "llm_call" logs/maps-census.log | grep "run_id=$RUN_ID" | jq '.cost_usd' | awk '{sum+=$1} END {print sum}'
```

---

## Safety Features (Phase 0)

### 1. Circuit Breaker
**Prevents cascading failures from external APIs**

| Provider | Opens After | Recovery Time | Action |
|---|---|---|---|
| OpenRouter | 5 consecutive failures | 60s | Fail cell + backoff |
| Google Places | 5 timeouts | 60s | Skip cell + retry later |
| Sonar | 5 rate limits | 60s | Use fallback rules |

**Monitor:**
```bash
grep "circuit_breaker" logs/maps-census.log | jq '.state'
# Output: "closed" (normal), "open" (failing), "half_open" (recovering)
```

**If stuck open:**
- Check provider status page
- Wait 60s for automatic recovery
- Or manually trigger via admin API: `POST /maps/runs/{runId}/recover`

### 2. LLM Call Budget
**Per-cell budget prevents cost spirals**

**Defaults:**
- Per-cell: 10 LLM calls max
- Per-run: 5000 LLM calls max
- Classification: 1 call/cell
- Enrichment: 2 calls/place

**Monitor:**
```bash
# Check per-cell budget
grep "budget_exceeded" logs/maps-census.log | grep "run_id=$RUN_ID"

# Check run-level budget
curl https://multiverdict.tech/api/v1/maps/runs/$RUN_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Org-Id: $ORG_ID" | jq '.funnel_metrics.quota_metrics'
```

**If budget exhausted early:**
- Review `cell_llm_budget_classification_cost` (might be too high)
- Increase `maps_run_llm_budget_max_calls` if expected (high-complexity country)
- Pause run: `POST /maps/runs/{runId}/pause`
- Adjust budget in config, restart

### 3. Campaign Timeout
**Prevent runaway jobs (10-hour default)**

**Defaults:**
- Campaign runs max 10 hours (`maps_census_campaign_timeout_seconds`)
- Checked every 10 cells (`maps_census_timeout_check_interval_cells`)

**If timeout approaching:**
- Logs warn 30 min before timeout
- Run gracefully finishes on timeout (exports what it has)
- No data loss (all discoveries persisted)

**Monitor:**
```bash
# Check elapsed time
curl https://multiverdict.tech/api/v1/maps/runs/$RUN_ID \
  -H "Authorization: Bearer $TOKEN" | jq '.started_at, .completed_at'
```

---

## Common Scenarios

### Scenario 1: Cost Exploding

**Symptoms:**
- Estimated cost jumps 10x unexpectedly
- Budget warnings in logs

**Diagnosis:**
```bash
# Check which stage is burning cost
grep "llm_call" logs/maps-census.log | grep "run_id=$RUN_ID" | \
  jq -s 'group_by(.purpose) | map({purpose: .[0].purpose, count: length, total_cost: map(.cost_usd) | add})'
```

**Actions:**
1. Pause run: `POST /maps/runs/{runId}/pause`
2. If classification phase: increase `CONFIDENCE_VERIFIED_MIN` (currently 0.90)
3. If enrichment phase: reduce `maps_census_enrichment_processing_batch_size` (currently 25)
4. Resume: `POST /maps/runs/{runId}/resume`

### Scenario 2: Website Crawl Hanging

**Symptoms:**
- Crawl stage stuck for >5 minutes
- No new places being processed
- CPU usage low

**Diagnosis:**
```bash
# Check for skipped domains
curl https://multiverdict.tech/api/v1/maps/runs/$RUN_ID \
  -H "Authorization: Bearer $TOKEN" | jq '.processing_state.crawl_skip_domains'
```

**Actions:**
1. Check if domain is known problematic (slow/large)
2. If new domain hanging: add to skip list manually
3. Pause run, force crawl skip, resume

### Scenario 3: Cells Failing Repeatedly

**Symptoms:**
- Same cell retries 3+ times
- Error: `max attempts exceeded`
- Cells_completed stalled

**Diagnosis:**
```bash
# Get failing cell details
curl https://multiverdict.tech/api/v1/maps/runs/$RUN_ID/cells \
  -H "Authorization: Bearer $TOKEN" | jq '.[] | select(.status == "failed")'
```

**Actions:**
1. Check error_message (rate limit? validation? network?)
2. If rate limit: wait 5 min, manual resume
3. If validation: fix in `maps_grid_planner` or skip region
4. If network: check provider status

---

## Feature Flags (Phase 0)

### New Safety Flags

```bash
# In .env file:

# Circuit breaker (recommended: ON)
MAPS_CIRCUIT_BREAKER_ENABLED=true
MAPS_CIRCUIT_BREAKER_FAILURE_THRESHOLD=5
MAPS_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS=60

# LLM budget (recommended: ON for production)
MAPS_CELL_LLM_BUDGET_MAX_CALLS=10
MAPS_RUN_LLM_BUDGET_MAX_CALLS=5000
MAPS_RUN_LLM_BUDGET_CHECK_ENABLED=true

# Campaign timeout (recommended: 10h for full run, 2h for test)
MAPS_CENSUS_CAMPAIGN_TIMEOUT_SECONDS=36000

# Observability (recommended: ON)
MAPS_OBSERVABILITY_ENABLED=true
MAPS_OBSERVABILITY_LOG_LEVEL=INFO
```

### Existing Flags (Not New)

| Flag | Default | Purpose |
|---|---|---|
| `MAPS_KEEP_DROP_SONAR_FALLBACK_ENABLED` | True | Use Sonar when classify confidence low |
| `MAPS_CENSUS_WEBSITE_SEARCH_ENABLED` | True | Search for missing websites |
| `MAPS_CENSUS_ENRICHMENT_ENABLED` | True | Run Phase 2 enrichment (addictions/languages) |
| `MAPS_CENSUS_AUTO_ENRICHMENT_ENABLED` | True | Auto-start enrichment after discovery |
| `MAPS_CENSUS_WEBSITE_CRAWL_ENABLED` | True | Crawl websites for contact info |
| `MAPS_CENSUS_EXTERNAL_DISCOVERY_ENABLED` | False | Use external sources (experimental) |
| `MAPS_CENSUS_ADMIN_UI_ENABLED` | True | Enable /admin/maps dashboard |

---

## Geographic Targeting (Boundary Dataset + Optional AI Planner)

A run's geographic cells (the lat/lon/zoom bias Gosom uses) come from a
trusted bounding box, resolved in this priority order:

1. **Dataset-backed boundary** (`app/services/scraping/maps_geo_boundaries.py`)
   — a real ADM0/ADM1 polygon cached locally from
   [geoBoundaries gbOpen](https://www.geoboundaries.org), bbox computed from
   the actual geometry.
2. **Static reference table** (`app/services/scraping/maps_geo_regions.py`)
   — a small hand-curated table of standard state bounding boxes (today:
   US AL/MI/CO/FL), used only when no cached dataset boundary covers the
   requested country/region.
3. **Text-only fallback** — no geographic bias at all. This is always the
   outcome for any country/state combination not covered by (1) or (2);
   nothing here ever invents or guesses a boundary.

Every run records which of the three applied, plus source/license/
boundary-id when a dataset boundary was used, in
`run.processing_state["geo_anchor"]` (surfaced on the run summary API as
`geo_anchor`). A run that fell back to text-only always says so explicitly
— never silently.

### Dataset provenance and license

- **Source:** geoBoundaries gbOpen (`gbOpen`, never `gbAuthoritative`).
- **License:** varies by country/dataset vintage; each cached file stores
  the license string returned by the API verbatim (do not assume Public
  Domain for every country — check the cached file's `license` field).
- **Coverage today:** country-code resolution covers every ISO 3166-1
  country (via `pycountry`, not a hand-picked list), and state/province
  matching cross-checks ISO 3166-2 whenever both a code and a name are
  given (an actual conflict, e.g. code "AL" with name "Alaska", is
  rejected rather than silently guessed). What's still narrow is the
  **cache**: a country/level only resolves a dataset-backed boundary once
  an operator has actually fetched it — see below. Everything else stays
  on the static table or text-only.

### Cache management

Cached boundaries live under `settings.maps_geo_boundary_cache_dir`
(default `backend/var/geo_boundaries/`, gitignored — never `backend/data/`
and never committed). The cache is **never populated automatically**: a
census run only *reads* it and falls back safely if it's empty.

To populate it, run the acquisition script once per country/level you
want dataset-backed targeting for:

```bash
cd backend
python -m scripts.fetch_geo_boundaries US --level ADM0 ADM1
python -m scripts.fetch_geo_boundaries CA FR --level ADM1
```

This is the *only* code path that talks to geoBoundaries (or GitHub, where
the actual geometry files are hosted) — the download host is allowlisted,
the request is timeout-bounded
(`MAPS_GEO_BOUNDARY_FETCH_TIMEOUT_SECONDS`), and the response size is
capped while streaming (`MAPS_GEO_BOUNDARY_MAX_DOWNLOAD_BYTES`, default
25MB — enforced as bytes arrive, not after a full download), and redirects
(geoBoundaries' GitHub-hosted downloads 302 in practice) are followed only
between allowlisted hosts, re-validated on every hop. Re-running the
script for an already-cached country/level is a no-op unless `--force` is
passed — it never overwrites existing cached data otherwise. Downloaded
geometry is validated (parseable GeoJSON, a usable `Feature`/
`FeatureCollection`, non-empty shapely geometry) before it's ever written
to the cache.

### Optional AI search planner

`MAPS_GEO_AI_PLANNER_ENABLED` (default **false**) turns on a small,
opt-in planning step that runs at most once per run (never per cell) via
the existing OpenRouter/model-catalog infrastructure
(`MAPS_GEO_AI_PLANNER_MODEL`, default `gpt-4.1-mini` — a cheap chat
model, not a research/web-search model; change it deliberately if you
want different cost/quality trade-offs). It may only suggest search
priorities, local-language terms, and additional query terms **within**
the region the deterministic resolver already verified. Its response
schema has no coordinate field at all, so it cannot supply or override a
boundary even if it tried. On any failure, timeout, or invalid output it
is skipped and the run continues with deterministic planning — this never
fails a run. Results are cached on disk per (country, state, model), so a
second run over the same geography never re-pays for a planning call.
Diagnostics (whether it ran, was cached, which model, or why it was
skipped) are recorded in `run.processing_state["geo_ai_plan"]` (surfaced
as `geo_ai_plan` on the run summary API). Only `additional_search_terms`
actually influences what gets searched (reused as extra query text on a
region the deterministic grid planner already chose, never a new
region, never `city_name`, never coordinates) — `priority_areas` (place
names) is recorded for visibility but deliberately never turned into a
search target, because there is no gazetteer in this codebase to check
an AI-suggested place name against, and doing so would be exactly the
"hallucinated/out-of-scope area" risk this feature must avoid.

### Geographic result validation

Coordinate-based targeting is a discovery *strategy*, not proof of
membership. Every discovered place's coordinates (when the provider
supplied any) are checked against the run's verified polygon and recorded
on `MapsPlace.geo_boundary_match`: `inside_polygon`, `outside_polygon`,
`no_polygon_available` (no cached dataset boundary for this run), or
`no_coordinates` (the provider gave none). A result is never silently
treated as "confirmed inside the region" just because its search cell was
seeded with that region's box — only an actual point-in-polygon check
against the real geometry earns `inside_polygon`. This does not change
Phase 2 classification/eligibility, which remains untouched.

### Known limitations

- **Cache coverage is operator-driven:** country-code and state/province
  *resolution* now covers any ISO 3166-1/3166-2 region, but a boundary
  only becomes usable once an operator has fetched it into the cache —
  see Cache management above. Until then, a country/state silently stays
  on the static table or text-only, exactly as documented.
- **ADM1 name matching has no fuzzy/alias layer:** a state/province name
  must match the cached GeoJSON's `shapeName` exactly (case-insensitive)
  or its ISO 3166-2 code — a differently-spelled or translated name will
  not resolve and falls back safely rather than guessing.
- This does **not** change the existing legal/compliance posture on
  browser-based Google Maps scraping, which remains a separate decision
  for the dev team.

---

## Cost Estimation

**Per-country estimates (before Phase 0 guards):**

| Country | Google Places | Classification | Enrichment | Total |
|---|---|---|---|---|
| Algeria | $50 | $150 | $200 | **$400–600** |
| France | $40 | $100 | $150 | **$300–400** |
| Finland | $20 | $40 | $60 | **$120–150** |

**With Phase 0 guardrails:**
- Expect 20–30% cost reduction (fewer wasted cells)
- Classification fallback to rules when high-confidence
- Per-cell budget prevents runaway

**Budget formula:**
```
Cost = (cells_planned × $0.015 Google)
     + (places_found × $0.015 classification)
     + (eligible_candidates × $0.05 enrichment)
```

---

## Troubleshooting Checklist

- [ ] **Run stuck?** Check `cells_completed` vs `cells_total` in dashboard
- [ ] **Logs unhelpful?** Set `MAPS_OBSERVABILITY_LOG_LEVEL=DEBUG`
- [ ] **Cost too high?** Pause → review circuit breaker + budget settings → resume
- [ ] **Circuit breaker open?** Wait 60s, check provider status, manually recover
- [ ] **Website crawl slow?** Check `crawl_skip_domains` and consider raising timeout
- [ ] **Cells failing?** Get details from `/maps/runs/{runId}/cells?status=failed`

---

## Escalation Path

1. **Panel alert (Datadog):** Check circuit breaker dashboard
2. **Page on-call:** Likely provider outage; wait for recovery
3. **Cost spike:** Pause run, investigate budget + stage breakdown
4. **Data loss:** Check backups + run reconciliation (`POST /maps/runs/{runId}/reconcile`)
5. **Can't recover:** Contact engineering, provide `run_id` + logs

---

## Operations Checklist (Daily)

- [ ] Any runs in **FAILED** state? Investigate + retry
- [ ] Any costs > $1000 USD? Review cell budget + saturation
- [ ] Circuit breaker open > 5 min? Check provider status
- [ ] Website crawl domains blacklisted? Review + purge old entries

