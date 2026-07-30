# Verified-Only Maps Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use Claude for Maps website selection and permanently exclude facilities lacking a verified official website.

**Architecture:** Keep search and deterministic validation unchanged, use a dedicated Claude model setting for fallback selection, then prune unverified relevant rows after all enrichment. Group frontend rows by verified website host so generic names do not merge unrelated organizations.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Pydantic settings, OpenRouter, React, TypeScript, Vitest.

## Global Constraints

- Claude selector batch size is 3.
- `places_found` remains a scan diagnostic.
- Only verified retained rows contribute to rehabilitation facility counts.
- Website sharing and UI grouping require a shared verified domain.

---

### Task 1: Claude website selector

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/services/scraping/maps_census_service.py`
- Test: `backend/tests/test_maps_census_service.py`

- [ ] Add a failing test asserting the dedicated website model setting is passed to `get_model`.
- [ ] Run the targeted test and confirm it fails because the service still uses `maps_census_model`.
- [ ] Add `maps_census_website_llm_model: str = "claude"` and set batch size to `3`.
- [ ] Use the dedicated setting in `_search_missing_websites`.
- [ ] Run the targeted test and confirm it passes.

### Task 2: Permanently prune unverified facilities

**Files:**
- Modify: `backend/app/services/scraping/maps_census_service.py`
- Test: `backend/tests/test_maps_census_service.py`

- [ ] Add failing tests proving verified rows remain, unverified relevant rows are deleted, and run counts reflect retained rows.
- [ ] Run the tests and confirm expected failures.
- [ ] Add `_delete_unverified_places` and call it after website propagation in initial census and refresh paths.
- [ ] Recompute `places_classified_relevant` and `places_with_website` from retained rows.
- [ ] Run targeted and related backend tests.

### Task 3: Verified-only UI and domain grouping

**Files:**
- Create: `src/lib/maps/groupPlaces.ts`
- Create: `src/lib/maps/groupPlaces.test.ts`
- Modify: `src/routes/maps.$runId.tsx`
- Modify: `src/components/maps/MapsRunCard.tsx`

- [ ] Add failing tests proving equal names with different domains remain separate and equal domains group as locations.
- [ ] Run tests and confirm expected failures.
- [ ] Implement normalized-host grouping.
- [ ] Request `withWebsiteOnly` results and display `places_with_website` as the verified rehab count.
- [ ] Update run-card copy to report verified facilities.
- [ ] Run frontend tests, ESLint, and build.

### Task 4: Full verification

- [ ] Run Maps/backend enrichment tests.
- [ ] Run Ruff on modified backend files.
- [ ] Run frontend tests and ESLint.
- [ ] Run production frontend build.
- [ ] Review `git diff` for unrelated files and report results without committing unless requested.
