#requires -Version 5.1
<#
.SYNOPSIS
    Host-side supervisor that restarts ONLY the `gosom` container when
    MultiMind's own API confirms its internal dispatcher has frozen.

.DESCRIPTION
    Runs OUTSIDE Docker, on the Windows host, because the API and
    scraping-worker containers are deliberately NOT given Docker socket
    access (an unrestricted docker.sock mount into a container is a
    container-escape risk) and cannot restart a sibling container
    themselves.

    Each run (`-Once`) does at most one restart cycle:
      1. Ask the API (`GET /internal/gosom-supervisor/status`, via
         `docker compose exec api curl ...` — no host port needs to be
         published for this) whether it has detected a freeze.
      2. If not frozen, or the server reports `restart_exhausted` (too
         many consecutive restarts already failed to bring Gosom's HTTP
         API back — see gosom_health_monitor.py), log and exit without
         restarting. `restart_exhausted` is an explicit, actionable
         stop condition, not something this script ever retries past —
         an operator must investigate Gosom directly.
      3. If frozen, but the LOCAL cooldown/hourly-rate-limit state
         (`$StateFile`) says we already attempted a restart recently or
         too many times in the last hour — counting BOTH successful and
         failed attempts — log and exit without restarting. This bound
         is enforced here, not only server-side, so it holds even if the
         API process itself restarted and lost in-memory state.
      4. Otherwise: POST /restart/begin, `docker compose restart gosom`,
         wait for Gosom's own HTTP API to answer again, POST
         /restart/complete, and record the ATTEMPT (success or failure)
         in $StateFile.

    IMPORTANT — what "restart succeeded" means here: this script can only
    confirm Gosom's REST API answers requests again after the restart.
    That is NOT the same as confirming its internal dispatcher is
    actually scraping again (the exact failure mode this whole feature
    exists for — github.com/gosom/google-maps-scraper#328 — is a
    container that answers HTTP while its dispatcher stays wedged). This
    script reports that distinction to the server as `http_responsive`,
    never as a general "success"; whether scraping itself has actually
    resumed is verified independently, server-side, by a real discovery
    job completing (see gosom_health_monitor.record_job_success) — this
    script has no way to confirm that itself and does not claim to.

    Install as a recurring Scheduled Task with -Once every 2 minutes (see
    the Task Scheduler commands in the deployment report). Never run two
    copies concurrently against the same -StateFile — use a single
    scheduled task, not -Once in a tight loop from two places.

.PARAMETER ComposeFile
    Path to docker-compose.gosom-census.yml.

.PARAMETER SupervisorToken
    Must match `MAPS_GOSOM_SUPERVISOR_TOKEN` configured on the API
    container. Required — the control endpoints 404 without it.

.PARAMETER StateFile
    Local JSON file tracking this script's own restart ATTEMPT history
    (success and failure both count), for the cooldown and
    max-restarts-per-hour guards. Defaults next to this script so it
    survives reboots without any other setup.

.PARAMETER MaxRestartsPerHour
    Hard local cap on restart ATTEMPTS (not just successes). Independent
    of, and in addition to, the server's own consecutive-failure
    exhaustion check.

.PARAMETER CooldownSeconds
    Minimum time between two restart ATTEMPTS, even if the hourly cap has
    room.

.PARAMETER DryRun
    Logs what it would do (including the actual restart command) without
    calling /restart/begin, running `docker compose restart gosom`, or
    calling /restart/complete. Use this to verify detection logic against
    a real or simulated freeze before ever letting the script restart
    anything.
#>
param(
    [string]$ComposeFile = (Join-Path $PSScriptRoot "..\docker-compose.gosom-census.yml"),
    [Parameter(Mandatory = $true)][string]$SupervisorToken,
    [string]$StateFile = (Join-Path $PSScriptRoot "gosom_supervisor_state.json"),
    [int]$MaxRestartsPerHour = 3,
    [int]$CooldownSeconds = 600,
    [switch]$Once,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Message)
    $ts = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    Write-Output "[$ts] $Message"
}

function Get-SupervisorState {
    if (Test-Path $StateFile) {
        try {
            return Get-Content $StateFile -Raw | ConvertFrom-Json
        } catch {
            Write-Log "WARN: state file unreadable, starting fresh: $_"
        }
    }
    return [PSCustomObject]@{ attempts = @() }
}

function Save-SupervisorState {
    param($State)
    $State | ConvertTo-Json -Depth 5 | Set-Content -Path $StateFile -Encoding utf8
}

function Invoke-ApiCurl {
    <#
    Goes through `docker compose exec api curl` rather than a published
    host port: api has no ports: mapping in docker-compose.gosom-census.yml
    today, and adding one is an avoidable Compose change this script does
    not need — the container already has curl (it's the healthcheck tool).

    A request BODY is sent via stdin (`curl -d @-`, piped in through
    `docker compose exec -T`), never as a `-d <json>` command-line
    argument. A JSON body containing double quotes, passed as a plain
    argument, goes through TWO layers of argument re-quoting (PowerShell's
    own process-launch quoting, then however `docker compose exec` passes
    argv into the container's curl) — exactly the kind of double
    indirection that silently mangles embedded quotes on Windows. Piping
    it as stdin avoids both layers entirely: no quoting of the body ever
    happens.
    #>
    param(
        [string[]]$CurlArgs,
        [string]$StdinBody = $null
    )
    $composeArgs = @("compose", "-f", $ComposeFile, "exec", "-T", "api", "curl", "-sS", "-w", "`n%{http_code}") + $CurlArgs
    if ($null -ne $StdinBody) {
        $output = $StdinBody | & docker @composeArgs 2>&1
    } else {
        $output = & docker @composeArgs 2>&1
    }
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose exec curl failed (exit $LASTEXITCODE): $output"
    }
    $lines = $output -split "`n"
    $statusCode = $lines[-1]
    $body = ($lines[0..($lines.Length - 2)] -join "`n")
    return [PSCustomObject]@{ StatusCode = [int]$statusCode; Body = $body }
}

function Get-GosomStatus {
    # No body, so header args are passed directly — no quoting risk here,
    # only a JSON body (handled via stdin above) is.
    $resp = Invoke-ApiCurl -CurlArgs @(
        "-H", "X-Supervisor-Token: $SupervisorToken",
        "http://127.0.0.1:8000/api/v1/maps/internal/gosom-supervisor/status"
    )
    if ($resp.StatusCode -ne 200) {
        throw "status endpoint returned HTTP $($resp.StatusCode): $($resp.Body)"
    }
    return $resp.Body | ConvertFrom-Json
}

function Invoke-RestartBegin {
    param([string]$Reason)
    $bodyJson = (@{ reason = $Reason } | ConvertTo-Json -Compress)
    $resp = Invoke-ApiCurl -StdinBody $bodyJson -CurlArgs @(
        "-X", "POST",
        "-H", "X-Supervisor-Token: $SupervisorToken",
        "-H", "Content-Type: application/json",
        "-d", "@-",
        "http://127.0.0.1:8000/api/v1/maps/internal/gosom-supervisor/restart/begin"
    )
    if ($resp.StatusCode -eq 409) {
        throw "restart/begin returned 409 — a restart is already in progress (possibly from a concurrent run of this script; never run two copies against the same StateFile)"
    }
    if ($resp.StatusCode -ne 200) {
        throw "restart/begin returned HTTP $($resp.StatusCode): $($resp.Body)"
    }
}

function Invoke-RestartComplete {
    # $HttpResponsive means ONLY "Gosom's REST API answered again" — see
    # the .DESCRIPTION note above. Never pass scraping-progress
    # verification through this parameter; the server deliberately keeps
    # that axis separate (gosom_health_monitor.complete_restart).
    param([bool]$HttpResponsive)
    $bodyJson = (@{ http_responsive = $HttpResponsive } | ConvertTo-Json -Compress)
    $resp = Invoke-ApiCurl -StdinBody $bodyJson -CurlArgs @(
        "-X", "POST",
        "-H", "X-Supervisor-Token: $SupervisorToken",
        "-H", "Content-Type: application/json",
        "-d", "@-",
        "http://127.0.0.1:8000/api/v1/maps/internal/gosom-supervisor/restart/complete"
    )
    if ($resp.StatusCode -ne 200) {
        Write-Log "WARN: restart/complete returned HTTP $($resp.StatusCode): $($resp.Body)"
        return $null
    }
    return $resp.Body | ConvertFrom-Json
}

function Wait-GosomHttpResponsive {
    <#
    Confirms ONLY that Gosom's own REST API answers again after the
    restart — NOT that its dispatcher is actually scraping. Do not rename
    this to imply more than it checks; genuine scraping-progress
    verification happens server-side, independently, from a real
    discovery job actually completing.
    #>
    param([int]$TimeoutSeconds = 120)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-ApiCurl -CurlArgs @("http://gosom:8080/api/v1/jobs")
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
                return $true
            }
        } catch {
            # not up yet
        }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Invoke-SupervisorCycle {
    Write-Log "Checking Gosom health..."
    $status = Get-GosomStatus
    Write-Log "frozen=$($status.frozen) reason=$($status.reason) consecutive_timeouts=$($status.state.consecutive_timeouts) consecutive_failed_restarts=$($status.state.consecutive_failed_restarts)"

    if ($status.state.restart_exhausted) {
        Write-Log "STOPPED: server reports restart_exhausted ($($status.state.restart_exhausted_reason)). Automatic recovery will not retry — an operator must investigate Gosom directly, then clear this state."
        return
    }

    if (-not $status.frozen) {
        Write-Log "Not frozen — nothing to do."
        return
    }

    $state = Get-SupervisorState
    $now = Get-Date
    $cutoffHour = $now.AddHours(-1)
    # Counts every ATTEMPT (success or failure) in the last hour — a
    # supervisor hammering a dead Gosom with failed restarts must still be
    # bounded by this cap, not just successful ones.
    $recentAttempts = @($state.attempts | Where-Object {
        try { [DateTime]::Parse($_) -ge $cutoffHour } catch { $false }
    })

    if ($recentAttempts.Count -ge $MaxRestartsPerHour) {
        Write-Log "BLOCKED: $($recentAttempts.Count) restart attempts already in the last hour (max $MaxRestartsPerHour). Not restarting. Operator should investigate Gosom manually."
        return
    }

    if ($state.attempts.Count -gt 0) {
        $last = [DateTime]::Parse($state.attempts[-1])
        $elapsed = ($now - $last).TotalSeconds
        if ($elapsed -lt $CooldownSeconds) {
            Write-Log "BLOCKED: last restart attempt was $([int]$elapsed)s ago, cooldown is ${CooldownSeconds}s. Not restarting."
            return
        }
    }

    $reason = "freeze detected: $($status.reason)"
    Write-Log "FREEZE CONFIRMED ($reason) — restarting gosom (attempt $($recentAttempts.Count + 1) of $MaxRestartsPerHour this hour)."

    if ($DryRun) {
        Write-Log "DRY RUN: would call restart/begin, 'docker compose -f $ComposeFile restart gosom', wait for HTTP responsiveness, then restart/complete. Nothing was called or restarted."
        return
    }

    Invoke-RestartBegin -Reason $reason
    # Record the attempt now, before we know the outcome — the hourly cap
    # and cooldown exist to bound how often we DISRUPT Gosom with a
    # restart, which happens regardless of whether it ends up responsive.
    $state.attempts = @($state.attempts) + @($now.ToUniversalTime().ToString("o"))
    Save-SupervisorState -State $state

    try {
        Write-Log "Running: docker compose -f $ComposeFile restart gosom"
        & docker compose -f $ComposeFile restart gosom
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose restart gosom exited $LASTEXITCODE"
        }

        $responsive = Wait-GosomHttpResponsive -TimeoutSeconds 120
        if (-not $responsive) {
            Write-Log "ERROR: gosom did not become HTTP-responsive within 120s after restart."
            Invoke-RestartComplete -HttpResponsive $false | Out-Null
            return
        }

        Write-Log "Gosom's HTTP API is responsive again (this does NOT yet confirm scraping has resumed — that is verified separately, server-side, by a real job completing)."
        $result = Invoke-RestartComplete -HttpResponsive $true
        if ($null -ne $result -and $result.state.restart_exhausted) {
            Write-Log "NOTE: server now reports restart_exhausted despite this attempt being HTTP-responsive — check consecutive_failed_restarts semantics if this is unexpected."
        }
    } catch {
        Write-Log "ERROR during restart: $_"
        try { Invoke-RestartComplete -HttpResponsive $false | Out-Null } catch { Write-Log "WARN: could not even report restart/complete failure: $_" }
        throw
    }
}

if ($Once) {
    Invoke-SupervisorCycle
} else {
    Write-Log "This script is designed to be invoked with -Once from a Scheduled Task, not run as a persistent loop. Exiting."
    exit 1
}

