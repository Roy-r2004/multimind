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
**Prevent runaway jobs (8-hour default)**

**Defaults:**
- Campaign runs max 8 hours (`maps_census_campaign_timeout_seconds`)
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

# Campaign timeout (recommended: 8h for full run, 2h for test)
MAPS_CENSUS_CAMPAIGN_TIMEOUT_SECONDS=28800

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

