# Maps Census Complete Excel Export

## Goal

Replace the Maps Census CSV download with a polished Excel workbook that exports every relevant facility, including incomplete and non-export-eligible rows. The workbook must not impose an application-level row limit.

## Scope

The export includes all places where `is_relevant` is true for the selected Maps Census run. It does not include Google Places results classified as irrelevant.

The existing "Export-ready only" checkbox remains a table-view filter. It never affects the downloaded workbook.

## Workbook

The backend generates one `.xlsx` file with two worksheets.

### Facilities

The primary worksheet contains the seven business columns shown in the results table:

1. Facility Name
2. Addictions Treated
3. Location
4. Languages Spoken
5. Website
6. Phone Number
7. Treatment Price

Missing values use the same user-facing placeholders as the table: `Not Specified` and `Contact for pricing`.

### Technical Data

The second worksheet contains every relevant facility with the business fields plus:

- Internal place ID
- Google Place ID
- Raw and canonical names
- Country, region, and city
- Formatted address
- Latitude and longitude
- Google Places types
- Raw website
- Official/contact website
- Website source
- Phone number
- Relevance confidence
- Relevance reason
- Verification tier
- Export eligibility
- Discovery query
- Enrichment status
- Photo availability

## Formatting

Both worksheets use:

- Bold colored headers
- Frozen header row
- Auto filters
- Excel table styling with alternating rows
- Wrapped text and top alignment
- Practical column widths capped for readability
- Clickable `http://` and `https://` hyperlinks
- Phone numbers stored as text to preserve `+` and leading zeroes
- Native Unicode values for Arabic and French

The workbook contains all matching database rows. Excel's own worksheet limit remains the only upper bound.

## API and UI

Add a Maps Census `.xlsx` endpoint returning the standard Office Open XML content type and an attachment filename such as `dz-maps-census-export.xlsx`.

The Maps results page changes its action from `Download CSV` to `Download Excel` and calls the new endpoint. The request does not send a verification-tier filter.

The existing CSV endpoint may remain for compatibility, but the primary UI no longer uses it.

## Failure Handling

If the run does not exist or belongs to another organization, preserve the existing not-found behavior. If workbook generation fails, return an API error and show `Failed to download Excel export` in the UI.

## Testing

Backend tests verify:

- Incomplete relevant rows are included.
- Non-export-eligible relevant rows are included.
- Irrelevant rows are excluded.
- Both worksheets and expected headers exist.
- Unicode, placeholders, hyperlinks, phone text formatting, filters, frozen panes, and tables are present.
- The API returns the correct filename and MIME type.

Frontend tests verify the download URL, filename extraction, and that no tier/export-ready filter is sent.
