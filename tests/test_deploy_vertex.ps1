# Standalone, offline rollout tests. Runs on Windows PowerShell and PowerShell 7.
$ErrorActionPreference = 'Stop'
$mockPath = Join-Path $PSScriptRoot 'mock_gcloud.ps1'
$sourceScript = Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts/deploy-vertex.ps1'
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("vertex-rollout-tests-" + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path (Join-Path $testRoot 'scripts') -Force
$testScript = Join-Path $testRoot 'scripts/deploy-vertex.ps1'
Copy-Item -LiteralPath $sourceScript -Destination $testScript

function Get-Command {
    param([string]$Name, [string]$ErrorAction)
    if ($Name -ne 'gcloud.cmd') { throw "Unexpected command: $Name" }
    return [PSCustomObject]@{ Source = $mockPath }
}
function uv {
    $global:LASTEXITCODE = if ($global:Scenario -eq 'dependency_failure') { 1 } else { 0 }
}
function Start-Sleep { throw 'Fixture unexpectedly entered a polling retry.' }
function Invoke-RestMethod {
    param([string]$Uri, [string]$Method, [string]$ContentType, [int]$TimeoutSec, [string]$Body, [hashtable]$Headers)
    if ($global:Scenario -eq 'authenticated' -and $Headers['X-API-Key'] -ne 'test-rollout-key') {
        throw 'Missing rollout API key header'
    }
    if ($Uri -like 'https://public.example/*') {
        if ($global:Scenario -eq 'concurrent_public_change') { $global:PublicRevision = 'other' }
        if ($global:Scenario -in @('public_failure', 'concurrent_public_change')) { throw 'Simulated public failure' }
    }
    if ($Uri -like '*/health') { return @{ status = 'ok' } }
    if ($Uri -like '*/ready') {
        $provider = if ($global:Scenario -in @('wrong_provider', 'cleanup_failure')) { 'groq' } else { 'vertexai' }
        return @{ status = 'ready'; model_provider = $provider; model = 'gemini-3.5-flash' }
    }
    if ($Uri -like '*/review') { $global:Finalized = $true; return @{ running = $true } }
    if ($Method -eq 'Post') { $global:Finalized = $false; return @{ thread_id = 'test-thread' } }
    if ($global:Scenario -eq 'research_failure') { return @{ error = 'Simulated research failure' } }
    if ($global:Finalized) { return @{ status = 'finalized'; running = $false; final_report = 'Completed report' } }
    return @{ status = 'drafted'; awaiting_review = $true; running = $false; draft = 'Draft'; sources = @(@{url = 'https://source.example'}) }
}

$envNames = @('MODEL_PROVIDER', 'GROQ_API_KEY', 'TAVILY_API_KEY', 'API_KEY', 'GCP_PROJECT_ID',
    'ENABLE_TYPESAFE', 'TYPESAFE_API_KEY', 'LANGSMITH_TRACING', 'LANGCHAIN_TRACING_V2', 'PYTHON_DOTENV_DISABLED')
$beforeEnvironment = @{}
foreach ($name in $envNames) { $beforeEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }

$cases = @('verify_only', 'promote', 'authenticated', 'partial_deploy', 'wrong_provider', 'research_failure', 'public_failure',
    'partial_promotion', 'concurrent_change', 'concurrent_public_change', 'cleanup_failure', 'dependency_failure')
foreach ($case in $cases) {
    $global:Scenario = $case
    $global:CloudCalls = [System.Collections.Generic.List[string]]::new()
    $global:PublicRevision = 'stable'
    $global:CandidateExists = $false
    $global:PromotionCount = 0
    $global:RollbackCount = 0
    $global:DescribeCount = 0
    $global:CleanupCount = 0
    $global:Finalized = $false
    $failure = $null
    try { & $testScript -Promote:($case -ne 'verify_only') -ApiKey 'test-rollout-key' } catch { $failure = $_ }
    if ($case -in @('verify_only', 'promote', 'authenticated') -and $failure) { throw "$case unexpectedly failed: $failure" }
    if ($case -notin @('verify_only', 'promote', 'authenticated') -and -not $failure) { throw "$case should fail" }
    if ($case -in @('verify_only', 'partial_deploy', 'wrong_provider', 'research_failure', 'concurrent_change', 'cleanup_failure', 'dependency_failure') -and $global:PromotionCount -ne 0) {
        throw "$case incorrectly promoted"
    }
    if ($case -eq 'promote' -and $global:PublicRevision -ne 'candidate') { throw 'Successful rollout was not promoted' }
    if ($case -in @('public_failure', 'partial_promotion') -and ($global:PublicRevision -ne 'stable' -or $global:RollbackCount -ne 1)) {
        throw "$case did not roll back"
    }
    if ($case -in @('concurrent_change', 'concurrent_public_change') -and ($global:PublicRevision -ne 'other' -or $global:RollbackCount -ne 0)) {
        throw "$case overwrote another rollout"
    }
    if ($case -eq 'cleanup_failure' -and $failure.ToString() -notmatch 'Readiness') { throw 'Cleanup masked the original failure' }
    if ($case -eq 'dependency_failure') {
        if ($global:CloudCalls.Count -ne 0) { throw 'Cloud calls occurred after local checks failed' }
    } else {
        $deploy = $global:CloudCalls | Where-Object { $_ -match 'run deploy' }
        foreach ($required in @('--no-traffic', '--no-cpu-throttling', '--max-instances=1', '--project=multi-agent-research-507619', 'RESEARCHER_MODEL_OVERRIDE=gemini-3.5-flash', 'ANALYST_MODEL_OVERRIDE=gemini-3.5-flash', 'WRITER_MODEL_OVERRIDE=gemini-3.5-flash', 'LLM_REQUEST_TIMEOUT=60', 'LLM_MAX_RETRIES=1')) {
            if ($deploy -notlike "*$required*") { throw "Deployment missing $required" }
        }
        if ($failure -and $global:CleanupCount -ne 1) { throw "$case did not attempt tag cleanup" }
    }
    foreach ($name in $envNames) {
        if ([Environment]::GetEnvironmentVariable($name, 'Process') -ne $beforeEnvironment[$name]) { throw "Environment was not restored: $name" }
    }
    Write-Output "PASS: $case"
}
Write-Output "$($cases.Count) rollout tests passed."
# The last scenario's mocked `uv` deliberately leaves $LASTEXITCODE=1 to
# simulate a dependency failure; without resetting it here, this script's
# own process exit code would read as a failure in CI even though every
# assertion above passed.
exit 0
