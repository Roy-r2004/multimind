# Verified-Only Maps Results Design

## Goal

Maps census results contain only rehabilitation facilities with a verified official website. Website candidate selection uses Claude Sonnet 4 in small batches.

## Behavior

- Deterministic website validation and Serper discovery run first.
- Unresolved candidates are evaluated by Claude Sonnet 4 through OpenRouter in batches of 3.
- Stored directory URLs that fail validation are cleared before refresh.
- Website sharing occurs only for locations sharing one verified website; identical generic names alone do not establish a shared center.
- After all website discovery and sharing attempts finish, relevant places without `official_website` are permanently deleted.
- `places_classified_relevant` and `places_with_website` both equal the retained verified-facility row count.
- `places_found` remains the original number of Google Places records scanned.
- The API/UI list requests verified rows only.
- UI grouping uses normalized website host, not canonical name alone. Different domains render as separate centers.

## Safety

- Claude may only select a host present in Serper candidates.
- Existing blocked-host validation remains authoritative.
- Deletion occurs only after all enrichment stages complete, never before Claude selection or safe website propagation.

## Verification

- Backend tests cover model selection, deletion, statistics, and retention of verified rows.
- Frontend helper tests cover grouping by website host and separation of same-name/different-domain facilities.
- Run backend tests, Ruff, frontend tests/lint, and production build.
