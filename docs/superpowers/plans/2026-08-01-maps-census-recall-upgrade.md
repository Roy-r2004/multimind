# Maps Census Recall Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade Maps Census so discovery is broad and country-adaptive, classification is structured (operator ≠ funding), and client export is strict — maximizing recall without hardcoding any country or target count.

**Architecture:** Keep Google Places + ARQ as the universal base. Insert a country-profile stage before the grid. Replace binary `is_relevant` gating with a lifecycle + `client_eligibility` model. Enrichment becomes structured classification with field-level evidence; website crawl and optional directory sources are later boosters. Old runs remain readable via derived backward-compatible fields.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Jinja2 prompts, OpenRouter/Sonar Pro + GPT-4.1, Google Places API, openpyxl, React/TanStack Router frontend, pytest.

## Global Constraints

- Do **not** hardcode France, French keywords, “500 centers,” or any country-specific count.
- Do **not** classify public funding as government ownership; operator is primary.
- Do **not** auto-delete `unknown` / insufficient-evidence candidates (`needs_review` instead).
- Do **not** count individual practitioners as “centers” in eligible export.
- Do **not** rewrite unrelated product areas (Scraping Council, chat, etc.).
- Preserve existing run rows; migrations are additive.
- France is validation only, not logic.

---

## Current bottleneck (why France = 14–29)

| Stage | Current behavior | Spec target |
|---|---|---|
| Grid | Private inpatient-only terms; max 120 cells | Broad local terms from country profile; adaptive saturation up to high ceiling |
| Classifier | Drops outpatient, associations-as-public, unknown ownership | Noise-only filter; keep candidates |
| Contact guard | Drops no-phone + no-website | Tag `contact_status=missing`, keep |
| Enrichment | `unknown`/`contradicted` → `is_relevant=False` | Structured fields; `unknown` → `needs_review` |
| Export | Single sheet of `is_relevant=True` | Multi-sheet by eligibility category |

---

## File map

### Create

| Path | Responsibility |
|---|---|
| `backend/app/services/scraping/maps_country_profile_service.py` | Live-web country discovery profile |
| `backend/app/prompts/scraping/maps_country_profile.j2` | Country profile prompt |
| `backend/app/services/scraping/maps_eligibility.py` | Pure functions: lifecycle → client_eligibility |
| `backend/app/services/scraping/maps_saturation.py` | Region saturation metrics + stop/expand rules |
| `backend/app/services/scraping/maps_website_crawl_service.py` | Limited official-site crawl (Phase 3) |
| `backend/app/services/scraping/maps_external_discovery.py` | `MapsExternalDiscoverySource` interface + stubs (Phase 3) |
| `backend/alembic/versions/031_maps_census_recall_upgrade.py` | Additive schema |
| `backend/tests/test_maps_country_profile.py` | Profile service tests |
| `backend/tests/test_maps_eligibility.py` | Eligibility rule tests |
| `backend/tests/test_maps_saturation.py` | Saturation rule tests |
| `docs/superpowers/specs/2026-08-01-maps-census-recall-upgrade-design.md` | Locked design decisions |
| Update `docs/maps-census-workflow.md` | Reflect new stages (Phase 4) |

### Modify

| Path | Change |
|---|---|
| `backend/app/db/models.py` | New enums/columns/tables |
| `backend/app/core/config.py` + `.env.example` | Saturation / crawl / profile settings |
| `backend/app/prompts/scraping/maps_grid_planner.j2` | Consume profile; broad discovery |
| `backend/app/services/scraping/maps_grid_planner.py` | Accept profile; higher cell ceiling |
| `backend/app/prompts/scraping/maps_relevance_classifier.j2` | Noise-only first pass |
| `backend/app/prompts/scraping/maps_place_enricher.j2` | Structured classification + evidence |
| `backend/app/services/scraping/maps_census_service.py` | Profile → plan → adaptive search; new guards; metrics |
| `backend/app/services/scraping/maps_place_enrichment_service.py` | Structured fields; unknown ≠ drop |
| `backend/app/services/scraping/maps_export_service.py` | Multi-sheet workbook |
| `backend/app/schemas/api.py` | New place/run fields + funnel metrics |
| `backend/app/api/v1/maps.py` | Optional filters by lifecycle/eligibility |
| `src/lib/maps/api.ts` | Types |
| `src/routes/maps.$runId.tsx` | Funnel UI + category filters |
| Existing maps tests | Align with new semantics |

---

## Database migration design (`031`)

**Strategy:** Additive only. Keep `is_relevant`, `verification_*`, `places_classified_relevant`. New fields are nullable or have defaults. Derive legacy fields in code for old UI consumers.

### `maps_census_runs` — add

| Column | Type | Notes |
|---|---|---|
| `country_profile` | JSON nullable | Full profile blob |
| `country_profile_status` | String(20) | `pending`/`completed`/`failed`/`skipped` |
| `country_profile_error` | Text nullable | |
| `funnel_metrics` | JSON nullable | Snapshot of funnel counters |
| `saturation_summary` | JSON nullable | Per-region rollup |

Keep existing counters; add parallel counters in `funnel_metrics` rather than renaming columns.

### `maps_census_regions` — new table

One row per administrative region in a run:

- `id`, `run_id` FK CASCADE
- `region_name`
- `cells_planned`, `cells_completed`
- `unique_places_found`, `new_unique_places_last_window`
- `plausible_providers_found`, `new_plausible_providers_last_window`
- `duplicate_rate` (float)
- `query_languages_used` JSON
- `provider_terms_used` JSON
- `saturation_status` (`pending`/`expanding`/`saturated`/`capped`)
- timestamps

### `maps_census_cells` — add

| Column | Type |
|---|---|
| `region_id` | FK nullable → `maps_census_regions` |
| `query_family` | String(64) nullable (`generic`, `residential`, `outpatient`, `detox`, `association`, `acronym`, …) |
| `query_language` | String(32) nullable |
| `new_unique_places` | Integer default 0 |
| `new_plausible_places` | Integer default 0 |

### `maps_places` — add

| Column | Type | Notes |
|---|---|---|
| `lifecycle_status` | String(40) default `discovered` | See enum below |
| `client_eligibility` | String(20) default `excluded` | `eligible`/`review`/`excluded` |
| `operator_type` | String(40) nullable | |
| `ownership_status` | String(40) nullable | |
| `funding_type` | String(20) nullable | |
| `facility_type` | String(64) nullable | |
| `care_setting` | String(32) nullable | |
| `organization_scope` | String(32) nullable | |
| `operator_name` | String(512) nullable | |
| `contact_status` | String(20) nullable | `complete`/`phone_only`/`website_only`/`missing` |
| `addiction_focus_confirmed` | Boolean nullable | |
| `medical_detox` | Boolean nullable | |
| `residential_accommodation` | Boolean nullable | |
| `operating_status` | String(32) nullable | `open`/`closed`/`unknown` |
| `website_languages` | JSON nullable | |
| `classification_evidence` | JSON nullable | field → `{value,confidence,evidence_quote,source_url,source_type}` |
| `discovery_sources` | JSON nullable | e.g. `["google_places","directory:xyz"]` |
| `source_record_ids` | JSON nullable | |
| `registry_id` | String(128) nullable | |
| `classification_confidence` | Numeric(5,4) nullable | overall structured-pass confidence |

**Indexes:** `lifecycle_status`, `client_eligibility`, `(run_id, lifecycle_status)`, `(run_id, client_eligibility)`.

### `maps_website_crawl_cache` — new table (Phase 3)

- `id`, `normalized_domain` unique, `pages` JSON, `fetched_at`, `expires_at`

### Lifecycle enum values

```
discovered | plausible | confirmed_eligible | probable_eligible | needs_review |
confirmed_public | confirmed_individual_practitioner | confirmed_cessation_only |
contradicted | unrelated | duplicate | permanently_closed
```

### Backward-compatible derivation (code, not DB drop)

```python
# After structured classification:
is_relevant = lifecycle_status in {
  "plausible", "confirmed_eligible", "probable_eligible", "needs_review",
  "confirmed_individual_practitioner",  # kept internally; filtered by client_eligibility
}
# places_classified_relevant = count where client_eligibility == "eligible"
#   OR (legacy UI) count where lifecycle in confirmed_eligible — decide in Phase 1:
# Prefer: places_classified_relevant = eligible centers only;
# expose places_needs_review / places_plausible as new API counters.
```

**Legacy API contract:** Keep `is_relevant`, `export_eligible`, `verification_verdict` populated:
- `export_eligible` ↔ `client_eligibility == "eligible"`
- `verification_verdict`: map `confirmed_eligible`→`confirmed`, `confirmed_public`/`contradicted`/`unrelated`→`contradicted`, `needs_review`/`probable_eligible`→`unknown`

---

## Backward-compatibility risks

| Risk | Mitigation |
|---|---|
| Old UI expects `is_relevant` = export set | Derive `is_relevant` broadly; drive export from `client_eligibility`; update UI filters in Phase 4 but keep `relevant_only` working |
| `places_classified_relevant` used for Enrich button | Redefine to eligible+review candidates needing enrichment, or add `places_pending_enrichment`; update UI |
| Existing completed runs lack new columns | Defaults: `lifecycle_status=discovered` or backfill from `is_relevant`/`verification_verdict` in migration |
| Enrichment currently demotes unknowns | Must change before France re-run; Phase 1 blocker |
| Contact guard deletes candidates | Soften in Phase 1 |
| 120-cell cap still active until Phase 2 | Phase 1 can still improve recall via classifier/enrichment/eligibility alone; document that France full recall needs Phase 2 |
| Cost explosion at 1500 cells | Cap + saturation + batch limits; log cost metrics |
| Worker 6h timeout | Saturation should finish earlier; raise timeout only if needed |
| Auto-enrichment cron on old runs | New enrichment must be idempotent and not wipe review rows |

### Migration backfill (031)

```sql
-- Pseudocode intent:
UPDATE maps_places SET
  lifecycle_status = CASE
    WHEN is_relevant IS TRUE AND verification_verdict = 'confirmed' THEN 'confirmed_eligible'
    WHEN is_relevant IS TRUE AND verification_verdict = 'unknown' THEN 'needs_review'
    WHEN is_relevant IS FALSE AND verification_verdict = 'contradicted' THEN 'contradicted'
    WHEN is_relevant IS FALSE AND verification_verdict = 'unknown' THEN 'needs_review'
    WHEN is_relevant IS TRUE THEN 'plausible'
    WHEN is_relevant IS FALSE THEN 'unrelated'
    ELSE 'discovered'
  END,
  client_eligibility = CASE
    WHEN is_relevant IS TRUE AND verification_verdict = 'confirmed' THEN 'eligible'
    WHEN is_relevant IS TRUE THEN 'review'
    ELSE 'excluded'
  END,
  contact_status = CASE
    WHEN phone present AND website present THEN 'complete'
    WHEN phone present THEN 'phone_only'
    WHEN website present THEN 'website_only'
    ELSE 'missing'
  END,
  discovery_sources = '["google_places"]';
```

Note: backfill of previously deleted-as-unknown rows that remain `is_relevant=false` becomes `needs_review` so they reappear for review exports (critical for France).

---

## Eligibility rule (pure function)

```python
ELIGIBLE_FACILITY_TYPES = {
  "residential_addiction_rehab",
  "inpatient_detox_center",
  "outpatient_addiction_center",
  "psychiatric_clinic_with_addiction_program",
  "therapeutic_community",
}

def compute_client_eligibility(place) -> str:
    if place.operating_status == "closed" or place.lifecycle_status == "permanently_closed":
        return "excluded"
    if place.ownership_status == "confirmed_government":
        return "excluded"
    if place.operator_type in {"public_hospital", "government_agency"}:
        return "excluded"
    if place.facility_type in {"unrelated", "cessation_service", "harm_reduction_only"}:
        return "excluded"
    if place.organization_scope == "individual_practice" or place.facility_type in {
        "individual_addictologist", "therapist_or_counselor"
    }:
        return "excluded"  # stored under confirmed_individual_practitioner lifecycle
    if (
        place.ownership_status == "confirmed_non_government"
        and place.organization_scope == "facility"
        and place.facility_type in ELIGIBLE_FACILITY_TYPES
        and place.addiction_focus_confirmed is True
    ):
        return "eligible"
    if place.ownership_status == "probable_non_government" and place.addiction_focus_confirmed:
        return "review"
    if place.ownership_status == "ownership_unknown" and place.facility_type in ELIGIBLE_FACILITY_TYPES:
        return "review"
    return "excluded"
```

**Funding never alone decides exclusion.** Association + `funding_type=public` can still be `confirmed_non_government`.

---

## Phase plan

### Phase 1 — Classification + eligibility + export (unblocks France recall immediately)

Stops the pipeline from deleting uncertain/association/outpatient candidates. Still uses current ~120-cell grid.

**Tasks overview:**

1. Migration 031 + model fields + backfill
2. `maps_eligibility.py` + unit tests
3. Soften classifier prompt (noise-only) + code guards (no contact delete; confidence bands → lifecycle)
4. Rewrite enricher prompt + service (structured fields; unknown → needs_review)
5. Multi-sheet Excel export
6. API schema additions (lifecycle, eligibility, operator fields)
7. Update tests (13 assertions from spec where applicable without Phase 2)

### Phase 2 — Country profile + broad grid + adaptive saturation

1. Country profile service + prompt + store on run
2. Grid planner consumes profile; remove private-only / outpatient exclusions from discovery
3. Raise `MAPS_CENSUS_MAX_CELLS_PER_CAMPAIGN=1500`; region table + saturation loop
4. Config + tests for planner terminology injection and saturation stop/expand

### Phase 3 — Crawl + field evidence + directory interface

1. Website crawl service + cache table
2. Enrichment consumes crawl text + field-level evidence required
3. `MapsExternalDiscoverySource` ABC + webpage/CSV stubs (no country hardcoding)
4. Tests for evidence persistence and crawl limits

### Phase 4 — UI funnel + France validation + docs

1. Funnel metrics on run detail; category tabs/filters
2. France benchmark run (manual); compare to directories as coverage check only
3. Update `docs/maps-census-workflow.md`

---

## Phase 1 detailed tasks

### Task 1: Schema migration + models

**Files:**
- Create: `backend/alembic/versions/031_maps_census_recall_upgrade.py`
- Modify: `backend/app/db/models.py`
- Test: model import + alembic upgrade in CI path already used by other maps tests

- [ ] **Step 1:** Add enums `MapsLifecycleStatus`, `MapsClientEligibility`, `MapsContactStatus`, and string-enum helpers for operator/facility/ownership/funding/care/scope.
- [ ] **Step 2:** Add columns listed in migration design to `MapsCensusRun`, `MapsCensusCell`, `MapsPlace`; add `MapsCensusRegion` model.
- [ ] **Step 3:** Write Alembic 031 with `down_revision="030"`, upgrade creates columns/tables/indexes, backfill SQL as above, downgrade drops new objects only.
- [ ] **Step 4:** Run migration locally against test DB; confirm old place rows get `needs_review` when previously unknown-demoted.
- [ ] **Step 5:** Commit `feat(maps): add recall-upgrade schema and lifecycle fields`

### Task 2: Eligibility pure functions

**Files:**
- Create: `backend/app/services/scraping/maps_eligibility.py`
- Create: `backend/tests/test_maps_eligibility.py`

- [ ] **Step 1:** Write failing tests for spec cases 2–8 (association+public funding eligible path; public hospital excluded; outpatient eligible; individual not a center; laser cessation excluded; psych without addiction excluded; residential NGO eligible).
- [ ] **Step 2:** Implement `compute_client_eligibility` + `derive_legacy_verification_verdict` + `derive_is_relevant`.
- [ ] **Step 3:** Tests pass; commit `feat(maps): add client eligibility rules`

### Task 3: Soften Stage-3 classifier + guards

**Files:**
- Modify: `backend/app/prompts/scraping/maps_relevance_classifier.j2`
- Modify: `backend/app/services/scraping/maps_census_service.py` (`_classify_pending`, `_apply_post_classification_filters`, `_apply_missing_contact_filter`)
- Modify: `backend/tests/test_maps_census_service.py`

**Classifier change:** Only mark unrelated for wrong country, hotel/spa/pharmacy/school/physical-rehab-only/recycling/closed. Output still `is_relevant` for “plausible addiction-related” OR introduce `decision` = `plausible|needs_review|unrelated`. Prefer mapping confidence bands:
- ≥0.75 → `lifecycle=plausible`, keep
- 0.45–0.74 → `needs_review`, keep
- <0.45 unrelated only if reason says unrelated; else `needs_review`

**Contact guard change:** Set `contact_status`; never set `is_relevant=False` for missing contact.

**Plus-code / generic-name:** Downgrade to `needs_review` instead of hard drop (or keep hard drop only for pure generic names with no address — product choice: **Phase 1 = needs_review** for plus-code; keep generic-name hard-unrelated only when name is solely category words with no identity).

- [ ] Tests: missing phone/website kept; outpatient not auto-dropped by prompt examples; unknown ownership kept.
- [ ] Commit `feat(maps): keep uncertain candidates after classification`

### Task 4: Structured enrichment (unknown → needs_review)

**Files:**
- Modify: `backend/app/prompts/scraping/maps_place_enricher.j2`
- Modify: `backend/app/services/scraping/maps_place_enrichment_service.py`
- Modify: `backend/tests/test_maps_place_enrichment.py`

**Prompt must return:** operator_type, ownership_status, funding_type, facility_type, care_setting, organization_scope, addiction_focus_confirmed, medical_detox, residential_accommodation, addictions_treated, languages_spoken, operating_status, evidence map, confidence.

**Service must:**
- Parse new schema (Pydantic models)
- Persist classification fields
- Set `lifecycle_status` from results
- Call `compute_client_eligibility`
- **Never** set `is_relevant=False` solely for unknown
- Map: contradicted/public hospital → appropriate lifecycle + excluded
- Individual practitioner → `confirmed_individual_practitioner` + excluded from centers
- Cessation-only → `confirmed_cessation_only` + excluded
- Failed/omitted batch → leave lifecycle, mark enrichment failed (retry), do not demote

- [ ] Tests: unknown→needs_review; association public-funded→non-government; hospital→excluded; field evidence saved (minimal: one evidence object in JSON).
- [ ] Commit `feat(maps): structured enrichment without dropping unknowns`

### Task 5: Multi-sheet export

**Files:**
- Modify: `backend/app/services/scraping/maps_export_service.py`
- Modify: `backend/tests/test_maps_export_service.py`

Sheets:
1. Eligible Centers
2. Needs Review
3. Public/Government
4. Individual Practitioners
5. Excluded/Unrelated
6. Discovery Audit (funnel + cell summary if available)

Eligible columns per spec. Default download still one workbook.

- [ ] Tests assert sheet names and that eligible ≠ individual ≠ public.
- [ ] Commit `feat(maps): multi-sheet eligibility export`

### Task 6: API + minimal UI wiring

**Files:**
- Modify: `backend/app/schemas/api.py`, `maps_census_service._place_item`, `maps.py` query filters
- Modify: `src/lib/maps/api.ts`
- Modify: `src/routes/maps.$runId.tsx` — filter by `client_eligibility` (eligible / review / all candidates); show new counts when present
- Modify: `backend/tests/test_maps_api.py`

- [ ] Commit `feat(maps): expose lifecycle and eligibility in API/UI`

### Task 7: Phase 1 verification gate

- [ ] Run full maps test suite
- [ ] Manual: re-enrich existing France run OR new France run on staging — expect far more than 14 in Eligible+Review combined; Eligible alone may still be limited until Phase 2 vocabulary
- [ ] Document Phase 1 expected outcome in PR notes

---

## Phase 2–4 task sketches (detail when Phase 1 merges)

### Phase 2

1. `maps_country_profile_service.py` + `maps_country_profile.j2` (Sonar); store JSON on run before planner.
2. Planner prompt takes `country_profile_json`; query families from profile terms; no “private” spam; include outpatient/association/detox/acronyms.
3. Config: `MAPS_CENSUS_MAX_CELLS_PER_CAMPAIGN=1500`, `SATURATION_WINDOW=10`, `MIN_NEW_UNIQUE_FOR_EXPANSION=3`, `MIN_NEW_PLAUSIBLE_FOR_EXPANSION=1`.
4. `run_census` loop: plan seed cells → execute → update region metrics → expand productive regions / mark saturated → stop at ceiling or all saturated.
5. Tests: planner includes profile terms; saturation stops unproductive region; no France hardcode.

### Phase 3

1. Crawl service: max pages/domain, local path keywords from profile, cache table.
2. Enricher input includes crawl excerpts; require per-field evidence.
3. External discovery ABC; wire optional sources from profile directories (generic fetch only).

### Phase 4

1. Funnel panel on run page.
2. France validation run + coverage notes (not assertions on count=500).
3. Refresh `docs/maps-census-workflow.md`.

---

## Test plan (spec §16 mapped)

| # | Assertion | Phase |
|---|---|---|
| 1 | unknown → needs_review, not excluded | 1 |
| 2 | association + public funding → non-government | 1 |
| 3 | public-hospital unit → excluded from client export | 1 |
| 4 | outpatient addiction center can be eligible | 1 |
| 5 | individual addictologist stored, not counted as center | 1 |
| 6 | smoking-laser ≠ clinical center | 1 |
| 7 | generic psych clinic without addiction excluded | 1 |
| 8 | residential NGO eligible | 1 |
| 9 | missing phone/website does not delete | 1 |
| 10 | field-level evidence saved | 1 (minimal) / 3 (full) |
| 11 | planner uses country-profile terminology | 2 |
| 12 | saturation expands/stops regions | 2 |
| 13 | exports separate eligible/review/public/individual/unrelated | 1 |

**Commands:**

```bash
cd backend
python -m pytest tests/test_maps_eligibility.py tests/test_maps_place_enrichment.py tests/test_maps_census_service.py tests/test_maps_export_service.py tests/test_maps_api.py -q
```

---

## Implementation order (locked)

1. Phase 1 (this plan’s detailed tasks) — ship first  
2. Phase 2 — country profile + adaptive grid  
3. Phase 3 — crawl + evidence + directories  
4. Phase 4 — UI funnel + France benchmark + docs  

Do not start Phase 2 until Phase 1 tests are green and enrichment no longer deletes unknowns.

---

## Out of scope

- Hardcoded France connectors or expected counts  
- Treatment price AI fill  
- Merging Maps places into Scraping Council `RehabilitationFacility`  
- Rewriting Serper website mode (keep as legacy path)

---

## Spec coverage checklist

| Spec § | Covered by |
|---|---|
| 1 Country profile | Phase 2 |
| 2 Broaden grid | Phase 2 |
| 3 Adaptive saturation | Phase 2 |
| 4 Preserve candidates / lifecycle | Phase 1 |
| 5 Structured classification | Phase 1 |
| 6 Field-level evidence | Phase 1 minimal, Phase 3 full |
| 7 Operator ≠ funding | Phase 1 |
| 8 Directory discovery | Phase 3 |
| 9 Soft Google classification | Phase 1 |
| 10 Contact guard | Phase 1 |
| 11 Website crawling | Phase 3 |
| 12 Client eligibility | Phase 1 |
| 13 Export/UI | Phase 1 export, Phase 4 UI |
| 14 Funnel metrics | Phase 2 compute, Phase 4 UI |
| 15 DB changes | Phase 1 migration (+ Phase 2/3 additive) |
| 16 Tests | Per phase |
| 17 France validation | Phase 4 |
| 18 Order | This document |
