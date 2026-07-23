param(
    [string]$EnvFile = ".env"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $EnvFile)) {
    if (Test-Path -LiteralPath ".env.example") {
        Copy-Item -LiteralPath ".env.example" -Destination $EnvFile
        Write-Host "Created $EnvFile from .env.example. Add your provider credentials before running."
    }
    else {
        throw "Could not find $EnvFile or .env.example. Run this script from the project root."
    }
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = "$EnvFile.unbounded-backup-$timestamp"
Copy-Item -LiteralPath $EnvFile -Destination $backup

# Zero means unlimited for count/size-based scraper stages in the hotfix.
$settings = [ordered]@{
    "SERPER_SEARCH_RESULTS_PER_QUERY" = "20"
    "SERPER_SEARCH_MAX_QUERIES_PER_DISCOVERY" = "12"
    "SOURCE_RETRIEVAL_MAX_BYTES" = "0"
    "SOURCE_RETRIEVAL_MAX_CANDIDATES_PER_COVERAGE_CELL" = "0"
    "SOURCE_RETRIEVAL_MAX_CANDIDATES_PER_EXECUTION" = "0"
    "FACILITY_EXTRACTION_MAX_DOCUMENT_CHARACTERS" = "0"
    "FACILITY_EXTRACTION_MAX_CHUNKS_PER_DOCUMENT" = "0"
    "FACILITY_EXTRACTION_MAX_CANDIDATES_PER_CHUNK" = "0"
    "FACILITY_EXTRACTION_MAX_CANDIDATES_PER_DOCUMENT" = "0"
    "FACILITY_EXTRACTION_MAX_DOCUMENTS_PER_EXECUTION" = "0"
    "FACILITY_EXTRACTION_MAX_CHUNKS_PER_EXECUTION" = "0"
    "FACILITY_PUBLICATION_MAX_CANDIDATES_PER_EXECUTION" = "0"
    "FACILITY_EXTRACTION_MAX_OUTPUT_TOKENS" = "16384"
    "SCRAPING_WORKER_JOB_TIMEOUT_SECONDS" = "604800"
    "SCRAPING_LOOP_WATCHDOG_ENABLED" = "true"
    "SCRAPING_LOOP_WATCHDOG_REPEATED_TASK_STOP_THRESHOLD" = "3"
    "SCRAPING_LOOP_WATCHDOG_STAGNANT_ROUND_WARNING_THRESHOLD" = "2"
    "SCRAPING_LOOP_WATCHDOG_STAGNANT_ROUND_STOP_THRESHOLD" = "5"
    "SCRAPING_LOOP_WATCHDOG_URL_PATTERN_WARNING_THRESHOLD" = "250"
    "SCRAPING_LOOP_WATCHDOG_REPEATED_CONTENT_STOP_THRESHOLD" = "8"
}

$managedKeys = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($key in $settings.Keys) {
    [void]$managedKeys.Add($key)
}
$keptLines = [System.Collections.Generic.List[string]]::new()
foreach ($line in Get-Content -LiteralPath $EnvFile) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=') {
        $key = $Matches[1]
        if ($managedKeys.Contains($key)) {
            continue
        }
    }
    $keptLines.Add($line)
}

while ($keptLines.Count -gt 0 -and [string]::IsNullOrWhiteSpace($keptLines[$keptLines.Count - 1])) {
    $keptLines.RemoveAt($keptLines.Count - 1)
}

$keptLines.Add("")
$keptLines.Add("# Unbounded scraper hotfix settings. Zero means unlimited.")
$keptLines.Add("# Network calls still keep request-level timeouts/retries; the loop watchdog detects crawl traps.")
foreach ($entry in $settings.GetEnumerator()) {
    $keptLines.Add("$($entry.Key)=$($entry.Value)")
}

Set-Content -LiteralPath $EnvFile -Value $keptLines -Encoding UTF8

Write-Host "Updated $EnvFile for unbounded scraping."
Write-Host "Backup created: $backup"
Write-Host "Rebuild the api, scraping-worker, and web services before starting a new execution."
