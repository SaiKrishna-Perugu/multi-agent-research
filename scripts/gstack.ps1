[CmdletBinding()]
param(
    [ValidateSet('setup', 'browse', 'run', 'doctor')]
    [string]$Action = 'doctor',
    [switch]$StageOnly,
    [switch]$Workspace,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)
$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$runtime = Join-Path $project '.gstack\source'
$bash = Join-Path $env:ProgramFiles 'Git\bin\bash.exe'
if (-not (Test-Path -LiteralPath $bash)) { throw 'Git Bash is required.' }
if (-not (Test-Path -LiteralPath (Join-Path $runtime 'VERSION'))) {
    throw 'The gstack runtime is missing from .gstack/source.'
}
$env:Path = (Join-Path $env:ProgramFiles 'Git\bin') + ';' +
    (Join-Path $env:ProgramFiles 'Git\usr\bin') + ';' + $env:Path
$env:GSTACK_HOME = (Join-Path $project '.gstack\state').Replace('\', '/')
$env:GSTACK_STATE_ROOT = $env:GSTACK_HOME
$env:BROWSE_STATE_FILE = (Join-Path $project '.gstack\browse.json').Replace('\', '/')
if (-not $env:BROWSE_START_TIMEOUT) { $env:BROWSE_START_TIMEOUT = '45000' }
New-Item -ItemType Directory -Force $env:GSTACK_HOME | Out-Null
if (-not $env:PLAYWRIGHT_BROWSERS_PATH) {
    $browserCache = Join-Path $env:LOCALAPPDATA 'ms-playwright'
    if (Test-Path -LiteralPath $browserCache) { $env:PLAYWRIGHT_BROWSERS_PATH = $browserCache }
}

switch ($Action) {
    'setup' {
        $render = Join-Path $project '.gstack\render'
        $log = Join-Path $project '.gstack\generation.log'
        $savedErrorPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & bun run (Join-Path $runtime 'scripts\gen-skill-docs.ts') --host codex --out-dir $render *> $log
            $generationExit = $LASTEXITCODE
        } finally { $ErrorActionPreference = $savedErrorPreference }
        if ($generationExit -ne 0) { throw "Skill generation failed; see $log" }
        $generated = Join-Path $render '.agents\skills'
        $destinations = @((Join-Path $project '.agents\skills'))
        if ($Workspace) {
            $destinations += (Join-Path ([IO.Directory]::GetParent($project).FullName) '.agents\skills')
        }
        if ($StageOnly) { $destinations = @((Join-Path $project '.gstack\skills')) }
        $envScript = (Join-Path $PSScriptRoot 'gstack-env.sh').Replace('\', '/')
        $utf8 = New-Object Text.UTF8Encoding($false)
        $skills = @(Get-ChildItem -LiteralPath $generated -Directory)
        foreach ($destination in $destinations) {
            New-Item -ItemType Directory -Force $destination | Out-Null
            foreach ($skill in $skills) {
                $target = Join-Path $destination $skill.Name
                $targetDoc = Join-Path $target 'SKILL.md'
                if ((Test-Path -LiteralPath $targetDoc) -and
                    -not ([IO.File]::ReadAllText($targetDoc).Contains('AUTO-GENERATED from SKILL.md.tmpl'))) {
                    throw "Refusing to replace an unrelated skill: $targetDoc"
                }
                New-Item -ItemType Directory -Force $target | Out-Null
                Copy-Item -Path (Join-Path $skill.FullName '*') -Destination $target -Recurse -Force
                $text = [IO.File]::ReadAllText($targetDoc)
                $text = [regex]::Replace($text, '(?m)^name: .+$', ('name: ' + $skill.Name))
                $bootstrap = 'GSTACK_BIN="$GSTACK_ROOT/bin"'
                $text = $text.Replace($bootstrap, ('source "' + $envScript + '"' + "`n" + $bootstrap))
                foreach ($reference in $skills) {
                    $shortName = $reference.Name -replace '^gstack-', ''
                    $text = $text.Replace(('${GSTACK_ROOT}/' + $shortName + '/SKILL.md'),
                        ('${GSTACK_SKILLS}/' + $reference.Name + '/SKILL.md'))
                    $text = $text.Replace(('$GSTACK_ROOT/' + $shortName + '/SKILL.md'),
                        ('$GSTACK_SKILLS/' + $reference.Name + '/SKILL.md'))
                }
                [IO.File]::WriteAllText($targetDoc, $text, $utf8)
            }
        }
        $savedSetupRunning = $env:GSTACK_SETUP_RUNNING
        try {
            $env:GSTACK_SETUP_RUNNING = '1'
            & $bash (Join-Path $PSScriptRoot 'gstack-run.sh') gstack-config set skill_prefix true
            $configExit = $LASTEXITCODE
        } finally { $env:GSTACK_SETUP_RUNNING = $savedSetupRunning }
        if ($configExit -ne 0) { throw 'Could not configure skill names.' }
        if ($StageOnly) {
            Write-Output "Prepared $($skills.Count) gstack skills in .gstack/skills; registration remains pending."
        } else {
            Write-Output "Registered $($skills.Count) gstack skills in $($destinations.Count) discovery location(s)."
        }
    }
    'browse' {
        $stoppingPid = $null
        if ($Arguments -and $Arguments[0] -eq 'stop' -and (Test-Path -LiteralPath $env:BROWSE_STATE_FILE)) {
            $stoppingPid = (Get-Content -LiteralPath $env:BROWSE_STATE_FILE -Raw | ConvertFrom-Json).pid
        }
        & (Join-Path $runtime 'browse\dist\browse.exe') @Arguments
        $browseExit = $LASTEXITCODE
        if ($browseExit -eq 0 -and $stoppingPid) {
            $stopDeadline = [DateTime]::UtcNow.AddSeconds(15)
            while (Get-Process -Id $stoppingPid -ErrorAction SilentlyContinue) {
                if ([DateTime]::UtcNow -ge $stopDeadline) {
                    throw 'The browser is still shutting down. Retry shortly before starting another session.'
                }
                Start-Sleep -Milliseconds 100
            }
        }
        exit $browseExit
    }
    'run' {
        if (-not $Arguments -or $Arguments[0] -notmatch '^gstack-[a-z0-9-]+$') {
            throw 'Use run gstack-<helper> [arguments].'
        }
        $helper = Join-Path $runtime ('bin\' + $Arguments[0])
        if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw "Missing helper: $helper" }
        $helperArguments = @($Arguments | Select-Object -Skip 1)
        & $bash (Join-Path $PSScriptRoot 'gstack-run.sh') $Arguments[0] @helperArguments
        exit $LASTEXITCODE
    }
    'doctor' {
        Write-Output ('gstack ' + [IO.File]::ReadAllText((Join-Path $runtime 'VERSION')).Trim())
        & bun --version
        & node --version
        & $bash --version | Select-Object -First 1
        & (Join-Path $runtime 'browse\dist\browse.exe') --help
        exit $LASTEXITCODE
    }
}
