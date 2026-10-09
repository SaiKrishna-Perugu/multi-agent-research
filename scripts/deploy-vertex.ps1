param(
    [string]$ProjectId = 'multi-agent-research-507619',
    [string]$Region = 'us-central1',
    [string]$Model = 'gemini-3.5-flash',
    [string]$ModelLocation = 'global',
    [switch]$Promote,
    [string]$ApiKey = $env:RESEARCH_API_KEY,
    [string]$TypeSafeModel = 'jev-1.13.0'
)

$ErrorActionPreference = 'Stop'
$service = 'multi-agent-research'
$repoRoot = Split-Path -Parent $PSScriptRoot
$gcloudCommand = (Get-Command gcloud.cmd -ErrorAction Stop).Source
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddHHmmss')
$tag = "vertexfix-$stamp"
$image = "$Region-docker.pkg.dev/$ProjectId/research-repo/${service}:vertexfix-$stamp"
$scope = @("--project=$ProjectId", "--region=$Region")
$promoted = $false
$promotionAttempted = $false
$deploymentAttempted = $false
$publicThreadId = $null
$apiHeaders = @{}
if ($ApiKey) { $apiHeaders['X-API-Key'] = $ApiKey }

function Invoke-Gcloud {
    param([string[]]$Arguments)
    $output = & $gcloudCommand @Arguments
    if ($LASTEXITCODE -ne 0) { throw "gcloud failed: $($Arguments[0..1] -join ' ')" }
    return $output
}

function Get-Service {
    $result = Invoke-Gcloud -Arguments (@('run', 'services', 'describe', $service) + $scope + @('--format=json(status.url,status.traffic)'))
    return (($result -join "`n") | ConvertFrom-Json)
}

function Get-Traffic {
    param($ServiceState)
    return (($ServiceState.status.traffic | Where-Object { $_.percent -gt 0 } |
        Sort-Object revisionName | ForEach-Object { "$($_.revisionName)=$($_.percent)" }) -join ',')
}

function Wait-Research {
    param([string]$BaseUrl, [string]$ThreadId, [string]$Expected, [Microsoft.PowerShell.Commands.WebRequestSession]$WebSession)
    $deadline = [DateTime]::UtcNow.AddMinutes(10)
    $consecutiveErrors = 0
    do {
        try {
            $state = Invoke-RestMethod -WebSession $WebSession -Headers $apiHeaders -Uri "$BaseUrl/research/$ThreadId" -TimeoutSec 90
            $consecutiveErrors = 0
            if ($state.error) { throw "Research failed: $($state.error)" }
            if ($Expected -eq 'review' -and $state.awaiting_review -and -not $state.running) { return $state }
            if ($Expected -eq 'finalized' -and $state.status -eq 'finalized' -and -not $state.running) { return $state }
            Write-Host "Research status: $($state.status), running: $($state.running)"
        } catch {
            $err = $_
            if ($err.ToString() -match "Research failed:") { throw $err }
            $consecutiveErrors++
            if ($consecutiveErrors -ge 6) {
                throw $err
            }
            Write-Warning "Transient polling issue: $($err.Exception.Message). Retrying..."
        }
        Start-Sleep -Seconds 5
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Research did not reach $Expected within ten minutes."
}

function Test-Lifecycle {
    param([string]$BaseUrl)
    # Keep Cloud Run's affinity cookie for all requests in this lifecycle.
    $WebSession = New-Object Microsoft.PowerShell.Commands.WebRequestSession
    $health = Invoke-RestMethod -WebSession $WebSession -Headers $apiHeaders -Uri "$BaseUrl/health" -TimeoutSec 60
    if ($health.status -ne 'ok') { throw 'Health check failed.' }
    $ready = Invoke-RestMethod -WebSession $WebSession -Headers $apiHeaders -Uri "$BaseUrl/ready" -TimeoutSec 60
    if ($ready.status -ne 'ready' -or $ready.model_provider -ne 'vertexai' -or $ready.model -ne $Model) {
        throw 'Readiness did not confirm the expected Vertex model.'
    }
    $started = Invoke-RestMethod -WebSession $WebSession -Headers $apiHeaders -Method Post -Uri "$BaseUrl/research" -ContentType 'application/json' -TimeoutSec 60 `
        -Body (@{ topic = 'Recent progress in solid-state batteries: cite two sources and summarize briefly.' } | ConvertTo-Json)
    if (-not $started.thread_id) { throw 'No research thread was returned.' }
    $review = Wait-Research -BaseUrl $BaseUrl -ThreadId $started.thread_id -Expected 'review' -WebSession $WebSession
    if (-not $review.draft -or @($review.sources).Count -eq 0) { throw 'Draft or research sources are missing.' }
    if (-not $review.review_version) { throw 'Server did not issue a review_version for the pending review.' }
    $reviewPayload = @{
        approved = $true
        action = 'approve'
        review_version = $review.review_version
    } | ConvertTo-Json
    $null = Invoke-RestMethod -WebSession $WebSession -Headers $apiHeaders -Method Post -Uri "$BaseUrl/research/$($started.thread_id)/review" `
        -ContentType 'application/json' -Body $reviewPayload -TimeoutSec 90
    $final = Wait-Research -BaseUrl $BaseUrl -ThreadId $started.thread_id -Expected 'finalized' -WebSession $WebSession
    if (-not $final.final_report) { throw 'Final report is empty.' }
    Write-Host "Verified research, human review, and finalization: $($started.thread_id)"
    return $started.thread_id
}

Push-Location $repoRoot
try {
    # Keep test settings scoped to child processes; preserve the caller's environment.
    $testSettings = @{
        MODEL_PROVIDER = 'groq'; GROQ_API_KEY = 'gsk-test-placeholder-not-real'
        TAVILY_API_KEY = 'tvly-test-placeholder-not-real'; API_KEY = ''
        GCP_PROJECT_ID = ''; ENABLE_TYPESAFE = 'false'; TYPESAFE_API_KEY = ''
        LANGSMITH_TRACING = 'false'; LANGCHAIN_TRACING_V2 = 'false'
        PYTHON_DOTENV_DISABLED = '1'
    }
    $savedSettings = @{}
    try {
        foreach ($name in $testSettings.Keys) {
            $savedSettings[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
            [Environment]::SetEnvironmentVariable($name, $testSettings[$name], 'Process')
        }
        & uv sync --frozen
        if ($LASTEXITCODE -ne 0) { throw 'Dependency sync failed.' }
        & uv run --frozen ruff check .
        if ($LASTEXITCODE -ne 0) { throw 'Lint failed.' }
        & uv run --frozen ruff format --check .
        if ($LASTEXITCODE -ne 0) { throw 'Formatting failed.' }
        & uv run --frozen python -m pytest tests/ -v
        if ($LASTEXITCODE -ne 0) { throw 'Tests failed; deployment stopped.' }
    } finally {
        foreach ($name in $savedSettings.Keys) {
            # PowerShell 7 preserves empty strings on Linux; remove originally
            # absent variables explicitly rather than binding null to a string.
            if ($null -eq $savedSettings[$name]) {
                Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
            } else {
                [Environment]::SetEnvironmentVariable($name, $savedSettings[$name], 'Process')
            }
        }
    }

    $before = Get-Service
    $previousTraffic = Get-Traffic -ServiceState $before
    if (-not $previousTraffic) { throw 'Cannot capture current traffic for rollback.' }
    $null = Invoke-Gcloud -Arguments @('builds', 'submit', "--tag=$image", "--project=$ProjectId", '--quiet')
    # Deployment can create a revision/tag even if the CLI reports an error.
    $deploymentAttempted = $true
    $null = Invoke-Gcloud -Arguments (@('run', 'deploy', $service) + $scope + @(
        "--image=$image", '--no-traffic', "--tag=$tag", '--quiet',
        '--memory=1Gi', '--timeout=300', '--min-instances=1', '--max-instances=1',
        '--concurrency=8', '--session-affinity', '--no-cpu-throttling',
        "--service-account=$service@$ProjectId.iam.gserviceaccount.com",
        "--update-env-vars=MODEL_PROVIDER=vertexai,GCP_PROJECT_ID=$ProjectId,GCP_LOCATION=$ModelLocation,VERTEX_CHAT_MODEL=$Model,RESEARCHER_MODEL_OVERRIDE=$Model,ANALYST_MODEL_OVERRIDE=$Model,WRITER_MODEL_OVERRIDE=$Model,LLM_REQUEST_TIMEOUT=60,LLM_MAX_RETRIES=1,TYPESAFE_MODEL=$TypeSafeModel"
    ))

    $candidate = Get-Service
    $tagged = $candidate.status.traffic | Where-Object { $_.tag -eq $tag } | Select-Object -First 1
    if (-not $tagged.url -or -not $tagged.revisionName) { throw 'Tagged revision was not found.' }
    $threadId = Test-Lifecycle -BaseUrl $tagged.url
    if ($Promote) {
        if ((Get-Traffic -ServiceState (Get-Service)) -ne $previousTraffic) {
            throw 'Public traffic changed during verification; promotion stopped.'
        }
        # The server may apply this change even if the CLI reports an error.
        $promotionAttempted = $true
        $null = Invoke-Gcloud -Arguments (@('run', 'services', 'update-traffic', $service) + $scope + @("--to-revisions=$($tagged.revisionName)=100", '--quiet'))
        $promoted = $true
        $publicThreadId = Test-Lifecycle -BaseUrl $candidate.status.url
    }
    $report = @{
        checked_at = [DateTime]::UtcNow.ToString('o'); project = $ProjectId
        revision = $tagged.revisionName; test_url = $tagged.url; model = $Model
        model_location = $ModelLocation; thread_id = $threadId
        promoted = $promoted; public_thread_id = $publicThreadId
        previous_traffic = $previousTraffic; image = $image
    }
    $null = New-Item -ItemType Directory -Path 'logs' -Force
    $report | ConvertTo-Json | Set-Content -LiteralPath "logs/vertex-rollout-$stamp.json" -Encoding UTF8
    Write-Host "Vertex verified. Promoted: $promoted. Test URL: $($tagged.url)"
    if (-not $Promote) { Write-Host "Promote using: gcloud run services update-traffic $service --project=$ProjectId --region=$Region --to-revisions=$($tagged.revisionName)=100" }
} catch {
    $failure = $_
    if ($promotionAttempted) {
        try {
            $currentTraffic = Get-Traffic -ServiceState (Get-Service)
            if ($currentTraffic -eq "$($tagged.revisionName)=100") {
                $null = Invoke-Gcloud -Arguments (@('run', 'services', 'update-traffic', $service) + $scope + @("--to-revisions=$previousTraffic", '--quiet'))
                Write-Host 'Restored the previous public traffic.'
            } elseif ($currentTraffic -ne $previousTraffic) {
                Write-Warning 'Traffic was changed by another rollout; automatic rollback skipped.'
            }
        } catch {
            Write-Warning "Could not restore public traffic: $_"
        }
    }
    if ($deploymentAttempted) {
        try {
            $null = Invoke-Gcloud -Arguments (@('run', 'services', 'update-traffic', $service) + $scope + @("--remove-tags=$tag", '--quiet'))
        } catch {
            Write-Warning "Could not remove the test tag: $_"
        }
    }
    throw $failure
} finally {
    Pop-Location
}
