# Maps Census Recall Upgrade — Design Lock

**Date:** 2026-08-01  
**Plan:** `docs/superpowers/plans/2026-08-01-maps-census-recall-upgrade.md`

## Principle

Discovery = broad · Classification = structured · Client export = strict

## Non-negotiables

- No country hardcoding; no fixed facility counts.
- Operator ownership ≠ funding source.
- `unknown` / insufficient evidence → `needs_review`, never auto-delete.
- Individual practitioners are stored but not “centers.”
- Additive schema; legacy `is_relevant` / `verification_verdict` derived for compatibility.

## Eligibility (client export)

Eligible when:

- `ownership_status = confirmed_non_government`
- `organization_scope = facility`
- `facility_type` ∈ residential / inpatient detox / outpatient addiction / psych+addiction / therapeutic community
- `addiction_focus_confirmed = true`

Probable non-government → `review`. Public hospital / government / cessation-only / individuals / unrelated → `excluded` from Eligible sheet (still retained in other sheets).

## Phases

1. Lifecycle + eligibility + soft classifier/enrichment + multi-sheet export  
2. Country profile + broad planner + adaptive saturation  
3. Website crawl + field evidence + directory interface  
4. UI funnel + France validation + docs  
