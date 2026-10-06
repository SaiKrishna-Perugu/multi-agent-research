# Invoked only by test_deploy_vertex.ps1; no Google Cloud calls are made.
$global:LASTEXITCODE = 0
$global:CloudCalls.Add(($args -join ' '))
$command = $args -join ' '
if ($command -match 'services describe') {
    $global:DescribeCount++
    if ($global:Scenario -eq 'concurrent_change' -and $global:DescribeCount -eq 3) {
        $global:PublicRevision = 'other'
    }
    $traffic = @(@{ revisionName = $global:PublicRevision; percent = 100 })
    if ($global:CandidateExists) {
        $traffic += @{ revisionName = 'candidate'; tag = $global:TestTag; url = 'https://candidate.example' }
    }
    @{ status = @{ url = 'https://public.example'; traffic = $traffic } } | ConvertTo-Json -Depth 5 -Compress
} elseif ($command -match 'run deploy') {
    $global:CandidateExists = $true
    $global:TestTag = ($args | Where-Object { $_ -like '--tag=*' }) -replace '^--tag=', ''
} elseif ($command -match '--to-revisions=candidate=100') {
    $global:PublicRevision = 'candidate'
    $global:PromotionCount++
    if ($global:Scenario -eq 'partial_promotion') { $global:LASTEXITCODE = 1 }
} elseif ($command -match '--to-revisions=stable=100') {
    $global:PublicRevision = 'stable'
    $global:RollbackCount++
} elseif ($command -match '--remove-tags=') {
    $global:CleanupCount++
    if ($global:Scenario -eq 'cleanup_failure') { $global:LASTEXITCODE = 1 }
}
