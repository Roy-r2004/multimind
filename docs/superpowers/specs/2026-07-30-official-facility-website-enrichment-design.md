# Official Facility Website Enrichment Design

**Date:** 2026-07-30

## Goal

Automatically discover and attach each published facility's official homepage when extraction produced no website or produced a suspicious directory/listing URL.

## Approved approach

Add a deterministic post-publication enrichment pass:

1. Build exact-name search queries from facility name, city/region, country, and “official website”.
2. Search through the existing configured search provider.
3. Reject unsuitable candidates (directories, social media, documents, search pages, and known listing/registry hosts).
4. Rank remaining candidates using normalized facility-name token overlap, title/snippet overlap, geography, URL shape, and official-site signals.
5. Accept only a candidate above a conservative threshold. Ambiguous results leave the website empty and require review.
6. Persist the accepted homepage as the facility's `primary_website` and a website contact without replacing an already strong official match.
7. Make the facility title in the dossier link to the confirmed website.

## Safety and correctness

- Search results are evidence candidates, not truth.
- Never use a registry/list page as the facility homepage merely because it contains the facility name.
- Never overwrite an existing non-suspicious website.
- Restrict accepted schemes to HTTP/HTTPS and canonicalize to an origin/homepage URL where appropriate.
- Enrichment failures and provider timeouts must not fail or stall the census.
- Bound search calls and candidate counts per facility.
- Record provenance and confidence in contact metadata/evidence where supported.

## Components

- New focused service under `backend/app/services/scraping/` for query construction, ranking, and persistence.
- Execution orchestration invokes the service after publication for facilities needing enrichment.
- Existing search provider abstraction performs the web search.
- `FacilityDossier.tsx` links the canonical title when `primary_website` is present.

## Testing

- Query includes exact facility name and geography.
- Known directory/social/document candidates are rejected.
- Strong official-domain result is selected.
- Ambiguous/low-score results produce no website.
- Existing good website is preserved.
- Search failure is non-fatal.
- UI build and lint pass with linked title.
