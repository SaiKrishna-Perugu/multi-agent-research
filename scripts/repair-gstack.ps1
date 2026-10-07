[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$RegisterWorkspace,
    [string]$ClaudeRoot = (Join-Path $env:USERPROFILE '.claude\skills\gstack')
)
$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtime = Join-Path $project '.gstack\source'
$localLock = Join-Path $runtime 'bun.lock'
$originalLock = Join-Path $ClaudeRoot 'bun.lock'
if ((Get-FileHash -LiteralPath $localLock -Algorithm SHA256).Hash -ne
    (Get-FileHash -LiteralPath $originalLock -Algorithm SHA256).Hash) {
    throw 'The original installation has a different dependency lockfile. No files were restored.'
}
$savedDependencies = [IO.Path]::GetFullPath((Join-Path $runtime 'node_modules'))
$originalDependencies = [IO.Path]::GetFullPath((Join-Path $ClaudeRoot 'node_modules'))
$pending = New-Object 'Collections.Generic.Stack[string]'
$pending.Push($savedDependencies)
$checked = 0
$missing = 0
$restored = 0
while ($pending.Count) {
    $directory = $pending.Pop()
    foreach ($entry in Get-ChildItem -LiteralPath $directory -Force) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        if ($entry.PSIsContainer) { $pending.Push($entry.FullName); continue }
        $checked++
        $relative = $entry.FullName.Substring($savedDependencies.Length + 1)
        $destination = [IO.Path]::GetFullPath((Join-Path $originalDependencies $relative))
        if (-not $destination.StartsWith($originalDependencies + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw 'A dependency destination escapes the original node_modules directory.'
        }
        if (-not (Test-Path -LiteralPath $destination -PathType Leaf)) {
            $missing++
            if (-not $CheckOnly) {
                $parent = [IO.Path]::GetDirectoryName($destination)
                New-Item -ItemType Directory -Path $parent -Force | Out-Null
                Copy-Item -LiteralPath $entry.FullName -Destination $destination
                if ((Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash -ne
                    (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash) {
                    throw "Restored dependency verification failed: $relative"
                }
                $restored++
            }
        }
    }
}
[pscustomobject]@{FilesChecked=$checked; MissingDependencies=$missing; RestoredDependencies=$restored; CheckOnly=[bool]$CheckOnly} |
    ConvertTo-Json -Compress
if ($RegisterWorkspace -and -not $CheckOnly) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'gstack.ps1') setup -Workspace
    if ($LASTEXITCODE -ne 0) { throw 'Dependency restoration finished, but skill registration failed.' }
}
