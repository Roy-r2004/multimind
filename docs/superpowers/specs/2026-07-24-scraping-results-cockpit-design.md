# Scraping Results Cockpit + Facility Dossier

**Date:** 2026-07-24  
**Status:** Approved — implementing  
**Product:** MultiVerdict / MultiAI scraping (census)  
**Approach:** Results cockpit (execution page), facility dossier as hero

## Problem

Live scrapes publish richer facility data than the UI shows. Excel export already has Locations, Contacts, Treatment Services, Programs, Populations Served, Admissions and Eligibility, Sources, and Field Evidence. The execution page only renders a thin facility table (name, type, website, confidence). The facilities list API returns the same thin summary and there is no facility detail endpoint for the UI.

Operators cannot see “what we got” without downloading Excel. That undercuts trust in the scrape product.

## Goals (v1)

1. Rebuild the execution page into an ops **results cockpit** where the facility dossier is primary once data exists.
2. Show Excel-parity facility detail in-app (tabs + honest empty states).
3. Expose full facility children via API so the UI is not Excel-only.
4. Fix the live-publish gap so extracted `services` land as `treatment_service` attributes (today publish creates locations/contacts but skips services).

## Non-goals (v1)

- Mission composer / blueprint visual redesign
- Public FindTreatment-style directory or SEO facility pages
- Expanding the LLM extraction schema for Programs / Populations / Admissions (tabs exist; stay empty until v1.1)
- Pricing, staff, amenities, operating hours tabs in the dossier (Excel-only for now)
- Replacing LiveSiteActivity; only re-prioritize it under Pipeline

## Users

- **Primary:** census / ops operators reviewing completeness, trust, and export
- **Secondary later:** end-user discovery (directory) — not v1

## Information architecture

### Route

Keep: `/scraping/{missionId}/executions/{executionId}`

Add shareable selection: `?facility={facilityId}`

### Layout (desktop)

```
Header: mission · run status · Export Excel · Cancel/Delete
Run pulse: Sources → Pages → Facilities · confidence / needs-review
┌ Facility roster (~40%) ─┬─ Facility dossier (~60%) ──────────┐
│ search, type filter     │ header + tabs                      │
│ dense rows              │ Overview · Locations · Contacts …  │
└─────────────────────────┴────────────────────────────────────┘
Collapsible Pipeline: LiveSiteActivity (expanded while 0 facilities)
Collapsible Sources & pages (existing tables)
```

### Layout (mobile)

Roster list → full-screen dossier with back. Pulse and header stay compact on top.

### Mission / blueprint / composer

Out of scope for v1 except incidental nav polish required by the execution page.

## Run pulse

Four stages with live counts from existing execution metrics + loaded facilities:

1. Sources discovered (`sources_discovered` / candidates length)
2. Pages retrieved (`documents_found` / documents length)
3. Facilities published (facilities length / `records_verified`)
4. Confidence summary: mean confidence + count where `human_review_status === "required"`

Stage states: pending · active · done · failed (derived from execution status + counts).

Clicking a stage focuses the matching panel (sources list, pipeline, roster).

While facility count is 0, Pipeline stays expanded. Once ≥1 facility exists, Pipeline collapses by default so the dossier stays primary.

## Facility roster

List endpoint remains summary-shaped for performance, enriched slightly for ops scanning:

- canonical name
- facility type
- primary city / region
- primary contact (already on summary)
- primary website
- confidence
- source_count
- **New summary fields (optional but preferred):** `location_count`, `contact_count`, `treatment_service_count` so rows can show chips without N+1 detail fetches

Search: client-side filter on name, city, region, type.  
Type filter: distinct `facility_type` values in the loaded set.

Row click selects facility and loads dossier detail.

## Facility dossier

### Header

- Canonical name (hero-level in the dossier panel, not page-level brand)
- Type badge, city/region, country
- Confidence % and human review status
- Actions: Open website · Copy primary contact · Focus Sources tab

### Tabs (always visible)

| Tab | Backing data |
|-----|----------------|
| Overview | name, aliases, type, operator (if present), primary address/city/region, confidence, review, source_count |
| Locations | `rehabilitation_facility_locations` |
| Contacts | `rehabilitation_facility_contacts` (non-social first; social can group under Contacts or a subsection) |
| Treatment Services | attributes where `attribute_group == "treatment_service"` |
| Programs | attributes where `attribute_group == "program"` |
| Populations Served | attributes where `attribute_group == "population_served"` |
| Admissions & Eligibility | attributes where `attribute_group == "admission_eligibility"` |
| Sources & Evidence | linked sources + `rehabilitation_field_evidence` quotes |

Attribute group keys must match Excel export (`admission_eligibility`, not a new spelling).

### Empty states

Every empty tab shows: **“Not extracted from retrieved pages yet.”**  
No fake placeholders. No hiding empty tabs.

### Visual language

Reuse MultiVerdict cinematic chrome (`GlassCard`, existing badges/buttons). Structure inspired by FindTreatment facility sections; do not clone FindTreatment styling (no cream/serif directory look; stay on product brand).

## Backend API

### `GET /scraping/executions/{execution_id}/facilities` (list)

Keep list response; optionally add count fields:

- `location_count`
- `contact_count`
- `treatment_service_count`

### `GET /scraping/executions/{execution_id}/facilities/{facility_id}` (new)

Auth + org scoping same as execution. Return detail payload:

```text
ScrapingFacilityDetail:
  …all summary fields…
  description?
  primary_address?
  aliases: [{ name, alias_type, is_primary }]
  locations: [{ id, location_type, location_name, full_address, city?, region?, is_primary, confidence_score }]
  contacts: [{ id, contact_type, label?, value, is_primary, confidence_score }]
  attributes: [{ id, attribute_group, attribute_key, value_text, confidence_score }]
  sources: [{ id, url, title?, relationship_type }]
  evidence: [{ id, field_path, extracted_value, evidence_text, source_url_snapshot?, page_title? }]
```

404 if facility not in execution / org.

### Publication fix (v1 data correctness)

In `facility_candidate_publication_service`, when publishing a candidate:

- Persist extracted `services` as `RehabilitationFacilityAttribute` with `attribute_group="treatment_service"`.
- Persist `license_or_registration` into the licenses path already used by export/mock if one exists; otherwise leave for v1.1 if no clean table mapping without scope creep.
- Persist `operator` onto facility or attribute if a clear field exists; otherwise Overview can omit until mapped.

Minimum required for v1 dossier value: **services → treatment_service attributes**. Locations and contacts already publish.

Re-scrapes are not required for old executions that already skipped services; only new publishes get the fix unless a backfill is explicitly requested later.

## Frontend structure (implementation sketch)

- Enrich `src/lib/scraping/types.ts` + `api.ts` with detail fetch
- New components under `src/components/scraping/`:
  - `RunPulse.tsx`
  - `FacilityRoster.tsx`
  - `FacilityDossier.tsx` (tabs)
- Refactor `scraping.$missionId.executions.$executionId.tsx` to compose cockpit; keep data loading / SSE refresh patterns

## Success criteria

1. Opening a completed Austria (or any) execution shows facilities with city/contact chips in the roster.
2. Selecting a facility shows Locations and Contacts when present in DB without Excel.
3. New publishes with extracted services show them under Treatment Services.
4. Empty Programs / Populations / Admissions tabs explain data was not extracted yet.
5. Pipeline collapses after first facility appears; pulse remains accurate live and after complete.
6. Excel export remains available and unchanged in sheet semantics.

## v1.1 (explicit follow-ups)

- Extraction schema for programs, populations, admissions/eligibility
- Publish licenses / operator cleanly into dossier Overview
- Mission + blueprint shell polish
- End-user directory / discovery surface

## Decisions log

| Decision | Choice |
|----------|--------|
| Scope shape | B (workspace) with A (dossier) as results hero |
| Audience | Ops first; discovery later |
| First ship | Dossier + run/results shell together |
| Approach | Results cockpit on execution page |
| Visual | MultiVerdict brand; FindTreatment structure only |
| Empty tabs | Visible with honest empty copy |
| Programs/Populations/Admissions data | Tabs in v1; extraction later |
