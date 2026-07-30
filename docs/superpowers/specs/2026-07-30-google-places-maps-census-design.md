# Google Places Maps Census Design

**Date:** 2026-07-30

## Goal

Add a standalone "Maps" feature (own tab, own workspace) that runs a country-level rehab/addiction/psychiatric facility census against the **Google Places API**, independent of the existing Scraping Council pipeline. Results are kept separate so they can later be compared against a Scraping Council execution for the same country (found-only-in-Maps, found-only-in-scraping, found-in-both, website match/conflict/missing). Comparison itself is a fast-follow, not part of this build.

## Why standalone (not merged into `RehabilitationFacility`)

`RehabilitationFacility` is tightly coupled to the scraping pipeline: `execution_id` → `ScrapingExecution` → `ScrapingRun` → `ScrapingMission` → `ScrapingBlueprint`, plus source discovery/retrieval/extraction phase state. A Places census has none of that — no blueprint approval, no team plan, no source documents/chunks. Forcing Places results into that table would mean fake or nullable versions of unrelated machinery. A small dedicated schema is simpler and keeps the two pipelines independently understandable; comparison is a query (fuzzy match on name/city/phone), not a foreign key join.

## Approved decisions

1. **Scope of run:** standalone Maps census for one country at a time (new top-level nav tab), not tied to an existing scraping mission/execution.
2. **Geographic scoping:** per-city/region grid, not whole-country queries. Mirrors the existing coverage-cell idea (`ScrapingCoverageCell`) conceptually, but is its own lightweight cell list for Maps.
3. **City/region list source:** LLM-generated per country at run start (reusing the existing prompt/provider infra pattern from blueprint/query planning), since there is no static per-country city list in the codebase today (`countries.py` only has code/name).
4. **Query terms per cell:** English + local-language rehab/addiction/psychiatric/narcological terms, generated per country (matches what worked in Serper Maps testing for Belarus).
5. **Places provider:** Google Places API (Text Search + Place Details, or Places API New `searchText`), using the verified key. **Not** Serper Maps — avoids conflating two different data sources in the comparison later.
6. **Facility classification:** LLM classifies each returned place (name + Places type + address + geography) as rehab/addiction/psychiatric-relevant or not. Catches non-obvious names (e.g. "Paratsel's" mental health center) that a keyword filter would miss.
7. **Website validation:** reuse the existing strict rules from `facility_website_enrichment_service` (`select_official_website` / `website_needs_enrichment` / rejection lists) unchanged, so "official" means the same thing in both pipelines. A place's Google-provided website goes through the same rejection checks (directories, social, document suffixes, municipal CMS/department paths, weak host-coverage heuristics) before being accepted as `official_website`; otherwise it's left blank rather than guessed.
8. **Execution model:** new ARQ job type on the existing worker (reuses queue, heartbeat, retry, and stale-recovery infra already built for scraping executions), not a synchronous request — countries can have enough cells × queries to exceed a request timeout.
9. **API key handling:** stored as a new backend setting (`google_places_api_key`, `google_places_enabled`) read from environment, never committed. The key shared in chat during testing must be treated as compromised — restrict/rotate it in Google Cloud before relying on it in production.

## Data model (new tables)

- `MapsCensusRun` — one row per country run: `id`, `organization_id`, `country_code`, `country_name`, `status` (queued/running/completed/failed/cancelled), `created_by`, `created_at`, `started_at`, `completed_at`, `heartbeat_at`, `error_message`, counters (`cells_total`, `cells_completed`, `places_found`, `places_classified_relevant`, `places_with_website`).
- `MapsCensusCell` — one row per city/region × query-term batch: `id`, `run_id`, `region_name`, `city_name`, `query_text`, `status`, `places_found`, `error_message`, timestamps. Mirrors `ScrapingCoverageCell`'s shape at a smaller scale.
- `MapsPlace` — one row per distinct Google place (deduped by `google_place_id`): `id`, `run_id`, `google_place_id`, `raw_name`, `canonical_name`, `place_types` (json), `formatted_address`, `city_name`, `region_name`, `latitude`, `longitude`, `international_phone_number`, `raw_website` (Google-provided, unvalidated), `official_website` (nullable, after strict validation), `is_relevant` (LLM classification result), `relevance_reason`, `confidence_score`, `discovered_via_query`, `created_at`, `updated_at`.

Deduplication key: `google_place_id` is authoritative; a secondary pass also collapses obvious duplicates by close lat/lng + normalized phone, since the grid can hit the same place from adjacent cells.

## Pipeline

1. User starts a Maps census for a country from the new Maps tab → creates `MapsCensusRun` (`status=queued`), enqueues ARQ job.
2. Job generates the city/region grid + local-language query terms for the country via LLM, writes `MapsCensusCell` rows.
3. For each cell (short-lived DB sessions around each network call, same discipline as `facility_ai_cleanup_service`/`facility_website_enrichment_service`):
   - Call Google Places Text Search for the cell's query.
   - For each result, fetch Place Details for website/phone/address if not already present in the search response.
   - Upsert into `MapsPlace` keyed by `google_place_id`; skip re-fetching details for already-seen place IDs.
4. Once all cells are fetched, batch-classify pending `MapsPlace` rows with the LLM (batched like `facility_ai_cleanup_service`) to set `is_relevant` + `relevance_reason`.
5. For relevant places with a `raw_website`, run it through the shared strict website validator; set `official_website` only if it passes, else leave null.
6. Mark `MapsCensusRun` completed with final counters; heartbeat throughout so stale-recovery can requeue a stuck run the same way scraping executions do.

## UI

- New nav entry "Maps" (own workspace, alongside Chat Council / Scraping Council) in `AppShell.tsx` / `WORKSPACES`.
- `/maps` — list of past Maps census runs + "New Maps Census" (country picker, mirrors mission creation's country field).
- `/maps/:runId` — run detail: status header, cell progress, results table (name, address, phone, website, relevance, confidence), filter by relevant/website-present.
- No comparison view in this iteration — noted as fast-follow once both pipelines have data for the same country.

## Safety and correctness

- Google Places calls are bounded per cell (result page cap) and per run (max cells, max places) via new settings, mirroring existing `facility_website_enrichment_*` budget settings.
- Never treat a Places "website" field as trustworthy without the shared validator.
- API failures/timeouts per cell are recorded and skipped, never fail the whole run.
- Short-lived DB sessions around every Places/LLM call — no long-held connection during network waits.
- API key read only from settings/environment; not logged, not embedded in error messages returned to the client.

## Testing

- Grid/query generation produces per-country city + local-language terms (unit test with a fixed country fixture, no live LLM).
- Place upsert dedupes by `google_place_id` across overlapping cells.
- Relevance classification unit tests with fixture LLM responses (obvious rehab, obvious non-rehab, ambiguous name).
- Website validation reuses existing `facility_website_enrichment_service` tests' fixtures (directory/social/gov rejected, real homepage accepted).
- Run-level: cell failure doesn't abort the run; heartbeat updates; completion counters match place counts.
- API layer: create run, list runs, get run detail/places — auth/org scoping like existing scraping endpoints.
