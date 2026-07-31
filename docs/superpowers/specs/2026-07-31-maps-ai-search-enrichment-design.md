# Maps Census AI + Web-Search Enrichment

## Goal

Fill the **Addictions Treated** and **Languages Spoken** export columns for *every*
relevant facility in a Maps Census run — including facilities that have no website
or only a Facebook page — by asking a web-search-capable LLM directly instead of
crawling facility websites. Treatment price stays out of scope.

## Census scope (classification + verification)

The Maps Census keeps only **non-government** facilities whose core mission is
clinical addiction treatment:

- Private, NGO, or charity-funded
- Inpatient / residential addiction rehab, **or** a private addictologue / addiction
  specialist practice that explicitly treats addiction
- Physical street address
- Explicit addiction / detox / narcology / named-substance evidence

Excluded: government/public/state centers, general psychiatric clinics without
addiction work, outpatient-only (except private addictologues), and non-addiction
facilities.

## Problem

The current Phase-2 enrichment crawls each facility's official website. In
countries like Algeria most relevant facilities have no crawlable site (only a
phone, a Facebook page, or nothing), so 8/10 rows are `skipped` and the columns
stay empty. Website crawling can never cover those rows.

## Approach

Replace website crawling with a batched call to a web-search model (Perplexity
Sonar Pro, already wired through OpenRouter and used by the website finder). The
model receives the facility's identity fields and searches the web itself.

- **Batch size:** 5 facilities per call (identity kept separate via `place_id`).
- **No crawling:** we never fetch facility pages ourselves.
- **Eligibility:** all relevant places whose `enrichment_status` is `pending`,
  `failed`, or `skipped`. Website presence is irrelevant.
- **Model:** `maps_census_enrichment_model`, default changes from `gpt-4.1` to
  `sonar-pro`. Overridable via env.

### Input per facility
`place_id`, name, city, region, address, phone, Google place types, website (if
any), country name/code.

### Verification

Google Maps names are noisy, so the same pass also verifies the listing really is
an addiction provider rather than trusting the name. Each facility gets a verdict:

| Verdict | Meaning | Action |
| --- | --- | --- |
| `confirmed` | Sources show a non-government addiction provider in scope (private/NGO inpatient rehab or private addictologue) | keep + enrich |
| `contradicted` | Sources show out of scope (government/public, general clinic, outpatient-only non-addictologue, shop, closed, policy office) | `is_relevant = False`, columns cleared |
| `unknown` | Nothing findable about this specific facility | keep, flagged |

`contradicted` requires positive evidence that it is *not* an addiction provider.
"Found nothing" is `unknown`, never `contradicted` — Sonar is conservative and
many small clinics have no web footprint. An unrecognized/missing verdict falls
back to `unknown`, so a malformed response can never delete facilities.

Verdicts persist on `maps_places` as `verification_verdict`,
`verification_reason`, and `verification_source_url` (migration `030`) and are
exposed on the place API payload.

Because verification can demote places, `places_classified_relevant`,
`places_with_website`, and `places_enriched` are all recomputed at the end of the
pass.

### Output (strict JSON)
```json
{
  "results": [
    {
      "place_id": "abc",
      "addictions_treated": [
        {"value": "Alcohol", "evidence_quote": "...", "source_url": "https://..."}
      ],
      "languages_spoken": [
        {"value": "Arabic", "evidence_quote": "...", "source_url": "https://..."}
      ]
    }
  ]
}
```

### Output rules
- Addictions must map to the existing `ADDICTION_TAXONOMY` labels (aliases
  applied). Non-mapping values are dropped.
- Languages are free text, de-duplicated.
- Empty lists when the model cannot verify anything — no guessing "typical for
  this country". `source_url`/`evidence_quote` are captured for traceability but
  are not required to persist the value (the model's search grounding is trusted;
  we do not have local text to string-match against).
- No `treatment_price`.

## Data flow

`enrich_run(run_id)`
1. Load relevant places with status in {pending, failed, skipped}, cap at
   `maps_census_enrichment_max_places_per_run`.
2. Chunk into batches of `maps_census_enrichment_batch_size` (default 5).
3. Per batch: render `maps_place_enricher.j2`, call the model with a heartbeat
   update, parse JSON, normalize.
4. Write `addictions_treated` + `languages_spoken`; set status `completed`
   (even if both empty — we tried) and stamp `enrichment_completed_at`.
   On a batch exception, mark those places `failed`.
5. Recount `run.places_enriched` = relevant completed places that have any
   addiction or language.

## API / UI changes

- Endpoint unchanged: `POST /maps/runs/{id}/enrich`.
- Button relabeled **Enrich with AI**; shown when
  `status == completed && places_enriched < places_classified_relevant`
  (no longer tied to `places_with_website`, which caused a permanent no-op when
  only Facebook rows remained).

## Failure handling

- Batch call error/timeout → those places `failed`, retriable by clicking again.
- Malformed JSON → treated as batch failure.
- A place the model omits → `completed` with empty columns (still counts as
  attempted; not stuck `pending`).

## Testing (local)

- Unit: batched enrichment fills addictions + languages for a place with **no
  website** (fake provider); place is not skipped.
- Unit: batch exception marks places `failed`.
- Unit: addictions normalized to taxonomy; non-taxonomy dropped.
- Unit: `run.places_enriched` counts only rows with data.
- Unit: `contradicted` verdict demotes the place and clears its columns.
- Unit: `unknown` verdict keeps the place.
- Unit: missing/invalid verdict defaults to `unknown` (never deletes).
- Manual: run against the local completed Algeria run and confirm columns fill
  for phone-only / Facebook-only facilities.

## Out of scope

- Treatment price.
- Crawling / fetching facility websites.
- Changing classification or discovery.
