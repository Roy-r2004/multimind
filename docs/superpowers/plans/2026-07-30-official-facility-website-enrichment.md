# Official Facility Website Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover and persist official facility homepages after publication, and link facility titles to them.

**Architecture:** Add a focused enrichment service that uses the existing search-provider abstraction, applies deterministic candidate rejection/ranking, and updates only facilities with missing or suspicious websites. Invoke it as a bounded, non-fatal post-publication step and expose the confirmed URL through the existing facility detail API.

**Tech Stack:** Python 3.12, SQLAlchemy async, existing Serper/Brave search adapters, pytest, React/TypeScript.

## Global Constraints

- Never replace a strong existing official website.
- Reject directories, social networks, documents, and search/list pages.
- Search/provider failure must not fail the census.
- Limit searches and candidates per execution.
- No new runtime dependency.

---

### Task 1: Test deterministic website selection

**Files:**
- Create: `backend/tests/test_facility_website_enrichment.py`

- [ ] Add failing tests for exact-name/geography query construction.
- [ ] Add failing tests rejecting directories, social media, PDFs, and list URLs.
- [ ] Add failing tests selecting a strong name-matching official domain.
- [ ] Add failing test returning no candidate for ambiguous low-confidence results.

### Task 2: Implement ranking and enrichment service

**Files:**
- Create: `backend/app/services/scraping/facility_website_enrichment_service.py`
- Modify: `backend/app/core/config.py`

- [ ] Implement normalized token matching and suspicious-host/path rejection.
- [ ] Build bounded exact-name search requests through `create_search_provider`.
- [ ] Accept only candidates above a conservative score threshold and margin.
- [ ] Preserve existing non-suspicious websites.
- [ ] Persist accepted URL as `primary_website` and a website contact.
- [ ] Return counters for considered, searched, enriched, preserved, ambiguous, and failed.

### Task 3: Invoke enrichment after publication

**Files:**
- Modify: `backend/app/services/scraping/execution_orchestrator.py`
- Modify/add focused orchestration tests.

- [ ] Run the enrichment pass after facility publication and before AI cleanup.
- [ ] Emit started/completed events with bounded counters.
- [ ] Make provider/configuration failures non-fatal and observable.
- [ ] Make phase resume/idempotency safe.

### Task 4: Link dossier title

**Files:**
- Modify: `src/components/scraping/FacilityDossier.tsx`

- [ ] Wrap canonical title in an external link when a website exists.
- [ ] Keep plain text when no website exists.
- [ ] Preserve the separate Open website action.

### Task 5: Verify and ship

- [ ] Run focused backend unit tests.
- [ ] Run targeted backend lint/compile checks.
- [ ] Run targeted frontend lint and production build.
- [ ] Commit only relevant files and push to `main`.
