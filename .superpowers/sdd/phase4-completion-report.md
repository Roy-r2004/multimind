# Phase 4 Completion Report — Maps Census Admin UI

**Branch:** `feat/maps-census-recall-phase1`  
**Worktree:** `.worktrees/maps-census-recall-phase1`  
**Phase 2 checkpoint:** `12449c5`  
**Phase 3 commits:** `6023962`, `a344d42`, `de4d3a4`  
**Phase 4 backend:** `0327afb`  
**Phase 4 frontend:** (see HEAD after frontend commit)

## Phase 3 commit summary

| SHA | Message |
|---|---|
| `6023962` | feat(maps): add bounded website crawling and cache |
| `a344d42` | feat(maps): ground enrichment in website evidence |
| `de4d3a4` | feat(maps): add external discovery interfaces |

**Maps tests at Phase 3 checkpoint:** 229 passed (accepted baseline; now 235 with Phase 4 admin tests).

### Phase 3 known limitations (recorded)

- No live website crawl run yet
- No live France benchmark yet
- External discovery returns candidates but does not insert `MapsPlace` rows
- External discovery disabled by default (`maps_census_external_discovery_enabled=false`)
- Phase 3 not production-validated with real credentials

## Phase 4 scope delivered

Operational admin frontend + API for Maps Census campaign management (Phases 1–3 backend unchanged in eligibility/discovery/crawl semantics except minimal pause integration).

## Files changed (Phase 4)

### Backend (commit `0327afb`)

- `backend/alembic/versions/034_maps_place_review_actions.py`
- `backend/app/services/scraping/maps_admin_service.py`
- `backend/app/api/v1/maps.py` — admin routes
- `backend/app/schemas/api.py` — admin DTOs
- `backend/app/db/models.py` — `MapsPlaceReviewAction`
- `backend/app/core/config.py` — `maps_census_admin_ui_enabled`
- `backend/app/services/scraping/maps_cell_runner.py` — `is_campaign_paused`
- `backend/app/services/scraping/maps_census_service.py` — pause check in cell loop
- `backend/app/services/scraping/maps_export_service.py` — export summary
- `backend/tests/test_maps_admin_api.py`
- `backend/tests/test_maps_api.py` — backward-compatible `/cells` preserved

### Frontend

- `src/lib/maps/adminTypes.ts`, `adminApi.ts`, `adminPaths.ts`, `adminFeature.ts`, `adminApi.test.ts`
- `src/components/maps/admin/MapsCampaignAdminPage.tsx`
- `src/routes/admin/maps.index.tsx`, `maps.$runId.tsx`
- `src/components/admin/AdminShell.tsx` — Maps Census nav (feature-flagged)
- `.env.example` — `VITE_MAPS_CENSUS_ADMIN_ENABLED=true`

## Routes / screens added

| Route | Screen |
|---|---|
| `/admin/maps` | Campaign list — create link, open dashboard |
| `/admin/maps/$runId` | Full campaign admin dashboard |

Legacy user-facing routes unchanged (`/maps`, `/maps/$runId`).

## API endpoints added (admin-only via `require_org_admin`)

| Method | Path |
|---|---|
| GET | `/maps/runs/{run_id}/dashboard` |
| GET | `/maps/runs/{run_id}/regions` |
| GET | `/maps/runs/{run_id}/cells/paged` |
| GET | `/maps/runs/{run_id}/places/paged` |
| GET | `/maps/runs/{run_id}/places/{place_id}` |
| POST | `/maps/runs/{run_id}/pause`, `/resume`, `/cancel` |
| POST | `/maps/runs/{run_id}/retry-failed-cells`, `/retry-websites`, `/retry-enrichment` |
| POST | `/maps/runs/{run_id}/places/{place_id}/review` |
| GET | `/maps/runs/{run_id}/export-summary` |

**Backward compatibility:** `GET /maps/runs/{run_id}/cells` still returns `list[MapsCensusCellItem]` for legacy UI.

## UI states (descriptions)

1. **Active campaign** — sticky header shows stage (`discovery`, `country_profile`, etc.), metrics poll every 5s, pause/cancel enabled.
2. **Paused campaign** — `campaign_paused` badge; resume enabled.
3. **Completed campaign** — export panel enabled; polling stops; incomplete warning if export summary shows pending rows.
4. **Capped cells** — cells table filter `capped_only`; region table shows saturation status.
5. **Failed cells** — failed filter + retry failed cells action.
6. **Needs-review providers** — Needs Review tab; review actions require reason.
7. **Provider with evidence** — evidence drawer shows per-field quote, source URL, confidence (truncated excerpts).
8. **Insufficient evidence** — unknown/empty fields labeled; review actions available.

## Campaign controls

- **Start:** create run via existing `POST /maps/runs` (from list page → `/maps/new`)
- **Pause/Resume:** `processing_state.campaign_paused`
- **Cancel:** run status → `cancelled`
- **Retry failed cells / websites / enrichment:** dedicated POST endpoints
- **Refresh:** manual reload button + auto-poll on active runs
- **Export:** multi-sheet XLSX via existing export endpoint + summary counts

## Provider review

Manual review actions (`mark_eligible`, `mark_review`, `mark_public`, `mark_individual`, `mark_excluded`) persist audit rows in `maps_place_review_actions` with reviewer, reason, previous/new values. Records are never deleted.

## Authorization & feature flag

- All admin routes: `require_org_admin` (owner/admin only)
- Frontend: wrapped in existing `AdminGuard`
- `maps_census_admin_ui_enabled` (backend) + `VITE_MAPS_CENSUS_ADMIN_ENABLED` (frontend)

## Test results

```bash
cd backend && python -m pytest tests/test_maps_*.py -q
# 235 passed

node --experimental-strip-types --test src/lib/maps/adminApi.test.ts
# 7 passed

npm run build
# success
```

## Known limitations

- No live website crawl or France benchmark in Phase 4 validation (mocked/seeded data only)
- External discovery still stub-only; not surfaced as place inserts
- Admin cell/place tables paginated (50 default); full audit export via XLSX
- Production deployment requires: migration 033 + 034, env credentials for Google Places/LLM, `VITE_MAPS_CENSUS_ADMIN_ENABLED=true`

## Stop for review

Phase 4 implementation complete. Do not open PR until accepted.
