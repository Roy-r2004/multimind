# Phase 2 Completion Gaps — Report

**Branch:** `feat/maps-census-recall-phase1`  
**Worktree:** `.worktrees/maps-census-recall-phase1`  
**Phase 1 rollback:** `0ac0a41` (unchanged)  
**Phase 2 implementation HEAD:** `eaf75b7`  
**Phase 2 status:** accepted (backend complete)

## Completion summary

- **Phase 2 backend implementation is complete.** All seven gap areas (pagination, subdivision, resumable cells, >250 batch processing, saturation semantics, quota metrics, France funnel validation + `list_cells` diagnostics) are implemented and committed on this branch.
- **218 Maps tests pass** (full maps suite; see Test results below).
- **France validation in this phase was deterministic and fully mocked.** `test_maps_france_funnel.py` exercises census → website refresh → enrichment with stubbed Places, classifier, and LLM providers; no live Google Places or external API calls.
- **Live France validation remains pending** and must be completed later with real provider credentials (Google Places API, classifier, website finder, enrichment) before treating France recall as production-validated.

## Migration 031 vs 032

Migration **031** did **not** contain pagination, resumable cell, subdivision, quota, or processing-state fields. **Migration 032** (`032_maps_census_pagination_resumable.py`) was required and is additive only.

## Commits (Phase 2 gaps, on top of `b77c119`)

| SHA | Message |
|---|---|
| `0c16304` | feat(maps): add migration 032 for pagination and resumable cells |
| `75fcff7` | feat(maps): paginate Places search with resume and cell helpers |
| `eaf75b7` | feat(maps): complete Phase 2 discovery gaps and France funnel |

Full Phase 2 history from rollback checkpoint:

```
eaf75b7 feat(maps): complete Phase 2 discovery gaps and France funnel
75fcff7 feat(maps): paginate Places search with resume and cell helpers
0c16304 feat(maps): add migration 032 for pagination and resumable cells
b77c119 feat(maps): adaptive saturation loop with region metrics
f1c7dd0 feat(maps): add saturation rules and 1500-cell campaign ceiling
6a7849d feat(maps): broaden grid planner with country profile terms
907d02e fix(maps): stub profile stage in census tests; align query_families with terms
37a6f8a feat(maps): add country discovery profile stage
0ac0a41 chore(maps): drop accidental SDD reports; add Phase 1 plan docs  ← Phase 1 rollback
```

## Files changed (gaps only)

**New:** `032_maps_census_pagination_resumable.py`, `maps_cell_runner.py`, `maps_cell_subdivision.py`, `maps_quota_tracker.py`, `test_maps_places_pagination.py`, `test_maps_cell_runner.py`, `test_maps_cell_subdivision.py`, `test_maps_census_pagination_resumable_migration.py`, `test_maps_france_funnel.py`

**Modified:** `config.py`, `models.py`, `maps_places_client.py`, `maps_census_service.py`, `maps_place_enrichment_service.py`, `schemas/api.py`, maps test files

## Test results

```
218 passed (maps suite including pagination, cell runner, subdivision, France funnel, >250 website/enrichment)
```

Command:
```bash
cd backend
python -m pytest tests/test_maps_places_pagination.py tests/test_maps_france_funnel.py \
  tests/test_maps_country_profile.py tests/test_maps_grid_planner.py tests/test_maps_saturation.py \
  tests/test_maps_eligibility.py tests/test_maps_census_service.py \
  tests/test_maps_census_recall_migration.py tests/test_maps_census_pagination_resumable_migration.py \
  tests/test_maps_place_enrichment.py tests/test_maps_export_service.py tests/test_maps_api.py \
  tests/test_maps_places_client.py tests/test_maps_cell_runner.py tests/test_maps_cell_subdivision.py -q
```

## Gap evidence

### 1. Google Places pagination
- **Before:** single request, max 20 results, no `nextPageToken`
- **After:** `search_text_paginated()` with `pageSize=20`, `pageToken`, per-page retry+jitter, dedupe, cancel_check, resume token
- **Persisted on cell:** `pages_fetched`, `raw_results_found`, `unique_results_found`, `duplicates_found`, `next_page_available`, `result_cap_reached`, `pagination_error`, `pagination_resume_token`
- **Tests:** `test_maps_places_pagination.py` proves page 2 and page 3 `pageToken` requests

### 2. Capped-cell subdivision
- `maps_cell_subdivision.subdivide_cell()` generates child cells (alternate query families, local terms, city qualifiers, viewport quadrants)
- Parent status `CAPPED`; persisted `parent_cell_id`, `expansion_reason`, `expansion_depth`
- Capped cells excluded from saturation window completion counts
- **Tests:** `test_maps_cell_subdivision.py`, France funnel asserts capped + subdivision cells

### 3. Resumable cell execution
- `maps_cell_runner.claim_cells()` with SKIP LOCKED (Postgres) / guarded UPDATE (SQLite)
- `attempt_count`, `started_at`, `heartbeat_at`, `next_retry_at`, `last_error`, `claimed_by`
- `recover_stale_running_cells()`, cancellation via `is_run_cancelled()`
- **Tests:** `test_maps_cell_runner.py` (concurrency, stale recovery, retry, cancellation)

### 4. Remove 250 bottleneck
- Website search and enrichment use resumable batch loops with `processing_state` cursor + pause flags
- `maps_census_website_search_batch_size` / `maps_census_enrichment_processing_batch_size` (25)
- Call budgets `*_max_calls_per_run` (5000) instead of silent 250 truncation
- **Tests:** `test_run_website_refresh_processes_more_than_250_places_without_truncation`, `test_enrich_run_processes_more_than_250_places_without_truncation`

### 5. Saturation semantics
- Classify new places per cell before region metrics update
- `new_plausible_places` uses classifier outcomes, not raw uniques
- Region counters: `eligible_candidates_found`, `review_candidates_found`, `confirmed_public_found`, `individuals_found`, `unrelated_found`
- High-confidence unrelated reasons no longer inflate plausible counts
- **Tests:** `test_run_census_saturates_region_with_many_unrelated_uniques_but_no_plausible`

### 6. Cost and quota metrics
- `MapsQuotaTracker` → persisted `run.quota_metrics` and included in `funnel_metrics`
- Tracks: google_places_requests/pages, profile/planner/classifier/website/enrichment calls, runtime_seconds, estimated_tokens

### 7. Completion validation
- `test_maps_france_funnel.py` mocks France end-to-end (census → website refresh → enrichment)
- Asserts regions/cities, cells, pages, capped/subdivision, raw/unique, eligible/review/public/individuals/unrelated, duplicate rate, saturation by region, quota counts, processing limits
- `list_cells` exposes `query_family`, `query_language`, pagination and subdivision diagnostics

## France funnel (mocked) snapshot

From `test_france_census_run_produces_full_funnel_report`:
- Regions: Ile-de-France, Auvergne-Rhone-Alpes
- Cities: Paris, Lyon
- Paris cell hits pagination cap → 1 capped parent + ≥1 subdivision child
- Mixed classification → eligible + unrelated both > 0
- Quota: google_places_requests ≥ 2, google_places_pages ≥ 3, classifier_calls ≥ 1
- Website + enrichment complete without hitting processing limits

## Phase 3 gate

Phase 2 is **accepted**. Backend discovery core is complete. **Do not start Phase 3** (website crawling) until explicitly requested. No PR opened. No frontend changes. Live France validation is a separate follow-up, not part of this checkpoint.
