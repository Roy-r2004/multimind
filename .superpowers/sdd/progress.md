# SDD Progress Ledger — Maps Census Recall Upgrade

## Checkpoint
- Branch: feat/maps-census-recall-phase1
- Worktree: .worktrees/maps-census-recall-phase1
- Phase 1 rollback: 0ac0a41
- Phase 1: complete (accepted)
- Phase 2: complete (accepted)

## Global Constraints
- Do not hardcode France, French keywords, "500 centers," or any country-specific count.
- Do not classify public funding as government ownership; operator is primary.
- Do not auto-delete unknown / insufficient-evidence candidates (needs_review instead).
- Do not count individual practitioners as "centers" in eligible export.
- Do not rewrite unrelated product areas (Scraping Council, chat, etc.).
- Preserve existing run rows; migrations are additive.
- France is validation only, not logic.
- Do not squash/amend/rewrite Phase 1 commit 0ac0a41 or migration 031.
- No PR yet; stay on this branch/worktree.

## Phase 2 Tasks
- Task 1: Country profile service + prompt + store on run — complete (0ac0a41..907d02e, review clean)
- Task 2: Grid planner consumes profile; broaden discovery — complete (907d02e..6a7849d, review clean)
- Task 3: Config + saturation module (1500, window, expand thresholds) — complete (6a7849d..f1c7dd0, review clean)
- Task 4: Adaptive run_census loop + region metrics + funnel snapshot — complete (f1c7dd0..b77c119, review clean)
- Task 5: Phase 2 verification gate (assertions 11–12) — complete (190 passed)

## Phase 2 progress
- Task 1: complete (commits 0ac0a41..907d02e, review clean)
  - Minors for final review: query_families filter lacks dedicated drop-path test; query_families casing vs provider_terms keys — Task 2 planner should normalize.

- Task 2: complete (commits 907d02e..6a7849d, review clean)
  - Minors for final review: query_family/query_language not in list_cells API; soft-drop of unmatched query_family.

- Task 3: complete (commits 6a7849d..f1c7dd0, review clean)
- Task 4: complete (commits f1c7dd0..b77c119, review clean)

## Phase 2 gaps (post b77c119) — COMPLETE (accepted)
- Implementation HEAD: eaf75b7
- Migration 032: pagination, resumable cells, subdivision, quota, processing_state
- Gap 1 pagination: search_text_paginated + persisted cell pagination fields
- Gap 2 subdivision: CAPPED parent + child cells (parent_cell_id, expansion_reason, expansion_depth)
- Gap 3 resumable cells: claim_cells, stale recovery, retry, cancellation
- Gap 4 >250 bottleneck: resumable website/enrichment batches with cursors
- Gap 5 saturation: classifier-based new_plausible_places + region funnel counters
- Gap 6 quota: MapsQuotaTracker → run.quota_metrics + funnel_metrics
- Gap 7 validation: France funnel test + list_cells query_family/query_language
- Tests: 218 passed (maps suite)
- France validation: deterministic/mocked in tests; live France run with real credentials pending
- Report: .superpowers/sdd/phase2-gaps-report.md

## Phase 3
- NOT STARTED — do not begin website crawling until explicitly requested
